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


def _naver_mcap_top(sosok, n, suffix):
    """네이버 시가총액 순위 상위 n개 (보통주만). KRX 로그인 없이 지수 구성종목을 근사."""
    rows, seen = [], set()
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
    for page in range(1, 15):
        r = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}",
                         headers=hdr, timeout=20)
        if page == 1:
            print(f"  naver 시총순위 HTTP {r.status_code}, {len(r.content)} bytes")
        html = r.content.decode("euc-kr", "ignore")
        found = re.findall(r'code=(\d{6})"[^>]*class="tltle"[^>]*>([^<]+)<', html)
        if not found:
            break
        for c, nm in found:
            nm = nm.strip()
            if c in seen or not c.endswith("0") or any(w in nm.upper() for w in ETF_WORDS):
                continue  # 우선주·ETF·스팩·리츠 제외
            seen.add(c)
            rows.append({"code": c, "symbol": f"{c}{suffix}", "name": nm, "sector": ""})
        if len(rows) >= n:
            break
    if len(rows) < n * 0.8:
        raise ValueError(f"네이버 시총순위 {len(rows)}개")
    return pd.DataFrame(rows[:n])


def _wiki(url, key_col):
    html = requests.get(url, headers=UA, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        if key_col in cols:
            return t
    raise ValueError(f"{url} 에서 {key_col} 표를 못 찾음")


def _sp500():
    t = _wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol")
    return pd.DataFrame(
        {
            "code": t["Symbol"].astype(str),
            "symbol": t["Symbol"].astype(str).str.replace(".", "-", regex=False),
            "name": t["Security"].astype(str),
            "sector": t["GICS Sector"].astype(str),
        }
    )


def _ndx():
    t = _wiki("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker")
    name_col = "Company" if "Company" in t.columns else t.columns[0]
    sec_col = next((c for c in t.columns if "Sector" in str(c)), None)
    return pd.DataFrame(
        {
            "code": t["Ticker"].astype(str),
            "symbol": t["Ticker"].astype(str).str.replace(".", "-", regex=False),
            "name": t[name_col].astype(str),
            "sector": t[sec_col].astype(str) if sec_col else "",
        }
    )


SOURCES = {
    "KOSPI200": [("pykrx", lambda: _kr_pykrx("1028", ".KS")), ("naver", _kospi200_naver),
                 ("naver 시총 상위 200 (근사)", lambda: _naver_mcap_top(0, 200, ".KS"))],
    "KOSDAQ150": [("pykrx", lambda: _kr_pykrx("2203", ".KQ")),
                  ("naver 시총 상위 150 (근사)", lambda: _naver_mcap_top(1, 150, ".KQ"))],
    "NASDAQ100": [("wikipedia", _ndx)],
    "SP500": [("wikipedia", _sp500)],
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
