"""재무·회사 정보 수집. 기본은 yfinance(4개년), 한국은 OpenDART, 미국은 SEC로 5개년 이상 보강(선택).
결과는 data/fundamentals.json 에 캐시하고 일정 주기로만 갱신한다."""
import io
import os
import time
import zipfile
from datetime import date, datetime

import pandas as pd
import requests

from .common import DATA, num, read_json, write_json

CACHE_FILE = DATA / "fundamentals.json"

REV_ROWS = ["Total Revenue", "Operating Revenue"]
OP_ROWS = ["Operating Income", "Total Operating Income As Reported"]
NI_ROWS = ["Net Income", "Net Income Common Stockholders"]
EPS_ROWS = ["Diluted EPS", "Basic EPS"]


def _row(df, names, col):
    for n in names:
        if n in df.index:
            return num(df.at[n, col])
    return None


def _table(df, quarterly=False):
    out = []
    if df is None or getattr(df, "empty", True):
        return out
    for col in sorted(df.columns):
        ts = pd.Timestamp(col)
        rec = {
            "end": ts.strftime("%Y-%m-%d"),
            "revenue": _row(df, REV_ROWS, col),
            "op": _row(df, OP_ROWS, col),
            "ni": _row(df, NI_ROWS, col),
            "eps": _row(df, EPS_ROWS, col),
        }
        if quarterly:
            rec["period"] = f"{ts.year}Q{(ts.month - 1) // 3 + 1}"
        else:
            rec["year"] = ts.year
        if any(rec[k] is not None for k in ("revenue", "op", "ni", "eps")):
            out.append(rec)
    return out


def fetch_yf(symbol):
    import yfinance as yf

    t = yf.Ticker(symbol)
    info = {}
    try:
        info = t.get_info() or {}
    except Exception:  # noqa: BLE001
        pass
    annual, quarters = [], []
    try:
        annual = _table(t.income_stmt)
    except Exception:  # noqa: BLE001
        pass
    try:
        quarters = _table(t.quarterly_income_stmt, quarterly=True)
    except Exception:  # noqa: BLE001
        pass
    summary = info.get("longBusinessSummary") or ""
    return {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": summary[:900],
        "mktcap": num(info.get("marketCap")),
        "currency": info.get("financialCurrency") or info.get("currency"),
        "inst": num(info.get("heldPercentInstitutions")),
        "roe": num(info.get("returnOnEquity")),
        "website": info.get("website"),
        "annual": annual,
        "quarters": quarters[-8:],
        "fin_source": "yfinance",
    }


# ---------- OpenDART (한국, 선택) ----------
_dart_map = None


def _dart_corp_map(key):
    global _dart_map
    if _dart_map is None:
        r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=60)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml = z.read(z.namelist()[0]).decode("utf-8")
        df = pd.read_xml(io.StringIO(xml), dtype=str)
        df = df[df["stock_code"].fillna("").str.strip() != ""]
        _dart_map = dict(zip(df["stock_code"].str.strip(), df["corp_code"]))
    return _dart_map


def _amt(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _dart_year(key, corp, year):
    r = requests.get(
        "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
        params={"crtfc_key": key, "corp_code": corp, "bsns_year": str(year), "reprt_code": "11011"},
        timeout=30,
    ).json()
    if r.get("status") != "000":
        return None
    items = r.get("list", [])
    fs = "CFS" if any(i.get("fs_div") == "CFS" for i in items) else "OFS"
    items = [i for i in items if i.get("fs_div") == fs]
    out = {year: {}, year - 1: {}, year - 2: {}}
    for i in items:
        nm = i.get("account_nm", "")
        key_ = (
            "revenue" if nm in ("매출액", "수익(매출액)", "영업수익", "매출") else
            "op" if nm in ("영업이익", "영업이익(손실)") else
            "ni" if nm in ("당기순이익", "당기순이익(손실)") else None
        )
        if not key_:
            continue
        for y, col in ((year, "thstrm_amount"), (year - 1, "frmtrm_amount"), (year - 2, "bfefrmtrm_amount")):
            v = _amt(i.get(col))
            if v is not None and key_ not in out[y]:
                out[y][key_] = v
    return out


def enrich_dart(code, rec, key):
    corp = _dart_corp_map(key).get(code)
    if not corp:
        return rec
    this = date.today().year
    years = {}
    for y in (this - 1, this - 2):  # 최신 사업보고서 연도 탐색
        res = _dart_year(key, corp, y)
        if res:
            years.update(res)
            older = _dart_year(key, corp, y - 3)
            if older:
                for k, v in older.items():
                    years.setdefault(k, v)
            break
        time.sleep(0.2)
    if not years:
        return rec
    eps_by_year = {a["year"]: a.get("eps") for a in rec.get("annual", [])}
    annual = []
    for y in sorted(years):
        v = years[y]
        if not v:
            continue
        annual.append({"year": y, "end": f"{y}-12-31", "revenue": v.get("revenue"), "op": v.get("op"),
                       "ni": v.get("ni"), "eps": eps_by_year.get(y)})
    if len(annual) >= len(rec.get("annual", [])):
        rec["annual"] = annual[-6:]
        rec["fin_source"] = "OpenDART + yfinance"
    return rec


# ---------- SEC companyfacts (미국, 선택) ----------
_sec_map = None
SEC_CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "op": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def _sec_cik(symbol, ua):
    global _sec_map
    if _sec_map is None:
        j = requests.get("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": ua}, timeout=30).json()
        _sec_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in j.values()}
    return _sec_map.get(symbol.upper().replace("-", "."))  or _sec_map.get(symbol.upper())


def _sec_series(facts, names, unit):
    best = {}
    for n in names:
        node = facts.get("us-gaap", {}).get(n, {}).get("units", {}).get(unit, [])
        for f in node:
            if f.get("form") != "10-K" or not f.get("start") or not f.get("end"):
                continue
            days = (datetime.fromisoformat(f["end"]) - datetime.fromisoformat(f["start"])).days
            if not 330 <= days <= 380:
                continue
            end = f["end"]
            if end not in best or f.get("filed", "") > best[end][1]:
                best[end] = (f["val"], f.get("filed", ""))
        if len(best) >= 5:
            break
    return {k: v[0] for k, v in best.items()}


def enrich_sec(symbol, rec, ua):
    cik = _sec_cik(symbol, ua)
    if not cik:
        return rec
    j = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                     headers={"User-Agent": ua}, timeout=30).json()
    facts = j.get("facts", {})
    series = {
        "revenue": _sec_series(facts, SEC_CONCEPTS["revenue"], "USD"),
        "op": _sec_series(facts, SEC_CONCEPTS["op"], "USD"),
        "ni": _sec_series(facts, SEC_CONCEPTS["ni"], "USD"),
        "eps": _sec_series(facts, SEC_CONCEPTS["eps"], "USD/shares"),
    }
    ends = sorted(set().union(*[set(s) for s in series.values()]))[-6:]
    if len(ends) < len(rec.get("annual", [])):
        return rec
    rec["annual"] = [
        {"year": int(e[:4]), "end": e, **{k: num(series[k].get(e)) for k in series}} for e in ends
    ]
    rec["fin_source"] = "SEC 10-K + yfinance"
    time.sleep(0.15)
    return rec


def refresh(universe_rows, refresh_days=7, cap=400, full=False):
    """universe_rows: [{symbol, code, market}] → 캐시 dict 반환"""
    cache = read_json(CACHE_FILE, {})
    today = date.today()
    dart_key = os.getenv("DART_API_KEY")
    sec_ua = os.getenv("SEC_USER_AGENT")

    def stale(s):
        c = cache.get(s)
        if not c:
            return 10_000
        try:
            return (today - date.fromisoformat(c.get("fetched", "2000-01-01"))).days
        except ValueError:
            return 10_000

    todo = [r for r in universe_rows if stale(r["symbol"]) >= refresh_days]
    todo.sort(key=lambda r: -stale(r["symbol"]))
    if not full:
        todo = todo[:cap]
    print(f"재무 갱신 대상 {len(todo)}개 (전체 {len(universe_rows)})")
    for n, r in enumerate(todo, 1):
        s = r["symbol"]
        try:
            rec = fetch_yf(s)
            if r["market"] == "KR" and dart_key:
                try:
                    rec = enrich_dart(r["code"], rec, dart_key)
                except Exception as e:  # noqa: BLE001
                    print(f"  DART 실패 {s}: {e}")
            if r["market"] == "US" and sec_ua:
                try:
                    rec = enrich_sec(r["code"], rec, sec_ua)
                except Exception as e:  # noqa: BLE001
                    print(f"  SEC 실패 {s}: {e}")
            rec["fetched"] = today.isoformat()
            old = cache.get(s, {})
            if old.get("ai"):
                rec["ai"] = old["ai"]
            cache[s] = rec
        except Exception as e:  # noqa: BLE001
            print(f"  재무 실패 {s}: {e}")
        if n % 50 == 0:
            print(f"  {n}/{len(todo)}")
            write_json(CACHE_FILE, cache)
        time.sleep(0.4)
    write_json(CACHE_FILE, cache)
    return cache
