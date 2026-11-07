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
        html = requests.get(
            f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}", headers=UA, timeout=20
        ).content.decode("euc-kr", "ignore")
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
    "KOSPI200": [("pykrx", lambda: _kr_pykrx("1028", ".KS")), ("naver", _kospi200_naver)],
    "KOSDAQ150": [("pykrx", lambda: _kr_pykrx("2203", ".KQ"))],
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
