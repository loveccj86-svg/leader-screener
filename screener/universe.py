"""지수 구성종목 수집. 실패하면 data/constituents/*.csv 캐시를 사용."""
import io
import re

import pandas as pd
import requests

from .common import DATA

UA = {"User-Agent": "Mozilla/5.0 (compatible; leader-screener/1.0)"}
CACHE = DATA / "constituents"

INDEX_META = {
    "KOSPI200": {"market": "KR", "label": "코스피 200"},
    "KOSDAQ150": {"market": "KR", "label": "코스닥 150"},
    "NASDAQ100": {"market": "US", "label": "나스닥 100"},
    "SP500": {"market": "US", "label": "S&P 500"},
}


def _save(name, df):
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE / f"{name}.csv", index=False, encoding="utf-8-sig")


def _load(name):
    p = CACHE / f"{name}.csv"
    return pd.read_csv(p, dtype=str) if p.exists() else None


def _kr_pykrx(index_code, suffix):
    import os
    if not os.environ.get("KRX_ID"):
        raise ValueError("KRX_ID 없음 — 건너뜀")
    from pykrx import stock

    codes = stock.get_index_portfolio_deposit_file(index_code)
    if not codes or len(codes) < 50:
        raise ValueError(f"구성종목 {len(codes or [])}개")
    return pd.DataFrame(
        [{"code": c, "symbol": f"{c}{suffix}", "name": stock.get_market_ticker_name(c), "sector": ""} for c in codes]
    )


def _kospi200_naver():
    rows, seen = [], set()
    for page in range(1, 25):
        r = requests.get(
            f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}",
            headers={**UA, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
                     "Referer": "https://finance.naver.com/sise/sise_index.naver?code=KPI200"}, timeout=20)
        if page == 1:
            print(f"  naver HTTP {r.status_code}, {len(r.content)} bytes")
        html = r.content.decode("euc-kr", "ignore")
        found = re.findall(r'code=(\d{6})"[^>]*>([^<]+)<', html)
        new = [(c, n.strip()) for c, n in found if c not in seen]
        if not new:
            break
        for c, n in new:
            seen.add(c)
            rows.append({"code": c, "symbol": f"{c}.KS", "name": n, "sector": ""})
    if len(rows) < 150:
        raise ValueError(f"네이버 {len(rows)}개")
    return pd.DataFrame(rows)


ETF_WORDS = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "KOSEF", "ARIRANG", "PLUS", "KIWOOM",
             "TIMEFOLIO", "스팩", "ETN", "리츠")


BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}


def _decode(content):
    head = content[:5000].lower()
    return content.decode("utf-8" if b"utf-8" in head else "euc-kr", "ignore")


def _keep(code, name):
    return code.endswith("0") and not any(w in name.upper() for w in ETF_WORDS)  # 우선주·ETF·스팩·리츠 제외


def _naver_api_top(market, n, suffix):
    """네이버 모바일 증권 JSON (시가총액순)"""
    rows, seen = [], set()
    for page in range(1, 8):
        r = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100",
                         headers=BROWSER, timeout=20)
        if page == 1:
            print(f"  naver api {market} HTTP {r.status_code}, {len(r.content)} bytes")
        items = (r.json() or {}).get("stocks") or []
        if not items:
            break
        for it in items:
            c, nm = str(it.get("itemCode", "")), str(it.get("stockName", "")).strip()
            if it.get("stockEndType") not in (None, "stock") or c in seen or not re.fullmatch(r"\d{6}", c) or not _keep(c, nm):
                continue
            seen.add(c)
            rows.append({"code": c, "symbol": f"{c}{suffix}", "name": nm, "sector": ""})
        if len(rows) >= n:
            break
    if len(rows) < n * 0.8:
        raise ValueError(f"네이버 api {len(rows)}개")
    return pd.DataFrame(rows[:n])


def _naver_mcap_top(sosok, n, suffix):
    """네이버 PC 시가총액 순위 페이지 (보통주만)"""
    rows, seen = [], set()
    for page in range(1, 15):
        r = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}",
                         headers=BROWSER, timeout=20)
        html = _decode(r.content)
        i = html.find('class="type_2"')
        body = html[i: html.find("</table>", i)] if i >= 0 else html
        found = re.findall(r'code=(\d{6})"[^>]*>([^<]+)</a>', body)
        if page == 1:
            print(f"  naver 시총순위 HTTP {r.status_code}, {len(r.content)} bytes, 표 {'있음' if i >= 0 else '없음'}, 링크 {len(found)}개")
            if not found:
                k = html.find("code=")
                print("  샘플:", re.sub(r"\s+", " ", html[max(0, k - 200): k + 300] if k >= 0 else html[:500]))
        if not found:
            break
        for c, nm in found:
            nm = nm.strip()
            if c in seen or not nm or not _keep(c, nm):
                continue
            seen.add(c)
            rows.append({"code": c, "symbol": f"{c}{suffix}", "name": nm, "sector": ""})
        if len(rows) >= n:
            break
    if len(rows) < n * 0.8:
        raise ValueError(f"네이버 시총순위 {len(rows)}개")
    return pd.DataFrame(rows[:n])


def _wiki(url, key_cols):
    html = requests.get(url, headers=BROWSER, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        for k in key_cols:
            if k in cols and len(t) >= 90:
                return t, k
    raise ValueError(f"{url} 에서 {key_cols} 표를 못 찾음")


def _frame(t, sym_col, name_col, sec_col):
    sym = t[sym_col].astype(str).str.strip()
    return pd.DataFrame({
        "code": sym, "symbol": sym.str.replace(".", "-", regex=False),
        "name": t[name_col].astype(str) if name_col in t.columns else sym,
        "sector": t[sec_col].astype(str) if sec_col and sec_col in t.columns else "",
    })


def _sp500_wiki():
    t, k = _wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["Symbol", "Ticker"])
    return _frame(t, k, "Security", "GICS Sector")


def _sp500_github():
    t = pd.read_csv(io.StringIO(requests.get(
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv", timeout=30).text))
    if len(t) < 450:
        raise ValueError(f"{len(t)}개")
    return _frame(t, "Symbol", "Security", "GICS Sector")


def _ndx_nasdaq():
    r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                     headers={**BROWSER, "Accept": "application/json, text/plain, */*",
                              "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}, timeout=30)
    print(f"  nasdaq api HTTP {r.status_code}")
    rows = (((r.json() or {}).get("data") or {}).get("data") or {}).get("rows") or []
    if len(rows) < 90:
        raise ValueError(f"nasdaq api {len(rows)}개")
    return _frame(pd.DataFrame(rows), "symbol", "companyName", None)


def _ndx_wiki():
    t, k = _wiki("https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"])
    name_col = next((c for c in t.columns if str(c) in ("Company", "Security", "Name")), t.columns[0])
    sec_col = next((c for c in t.columns if "Sector" in str(c)), None)
    return _frame(t, k, name_col, sec_col)


SOURCES = {
    "KOSPI200": [("pykrx", lambda: _kr_pykrx("1028", ".KS")), ("naver", _kospi200_naver),
                 ("naver 시총 상위 200 (근사)", lambda: _naver_api_top("KOSPI", 200, ".KS")),
                 ("naver PC 시총 상위 200 (근사)", lambda: _naver_mcap_top(0, 200, ".KS"))],
    "KOSDAQ150": [("pykrx", lambda: _kr_pykrx("2203", ".KQ")),
                  ("naver 시총 상위 150 (근사)", lambda: _naver_api_top("KOSDAQ", 150, ".KQ")),
                  ("naver PC 시총 상위 150 (근사)", lambda: _naver_mcap_top(1, 150, ".KQ"))],
    "NASDAQ100": [("nasdaq.com", _ndx_nasdaq), ("wikipedia", _ndx_wiki)],
    "SP500": [("wikipedia", _sp500_wiki), ("github datasets", _sp500_github)],
}


def get_constituents(name):
    """(DataFrame[code, symbol, name, sector], source 설명) 반환"""
    errors = []
    for src, fn in SOURCES[name]:
        try:
            df = fn()
            df = df.drop_duplicates("symbol").reset_index(drop=True)
            _save(name, df)
            return df, src
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {e}")
    df = _load(name)
    if df is None:
        raise RuntimeError(f"{name} 구성종목을 가져오지 못함: {errors}")
    df["sector"] = df.get("sector", "").fillna("")
    return df, "cache (" + "; ".join(errors) + ")"
