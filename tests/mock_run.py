"""네트워크 없이 합성 데이터로 전체 파이프라인을 돌려보는 테스트.
실행: python -m tests.mock_run   (결과는 docs/data/ 에 '샘플'로 저장됨 — 실제 운용 전 삭제됨)"""
import random

import numpy as np
import pandas as pd

from screener import fundamentals, main, prices, universe

rng = np.random.default_rng(7)
random.seed(7)
DAYS = pd.bdate_range(end="2026-11-06", periods=800)


def fake_series(drift, vol, crash=0.0):
    r = rng.normal(drift, vol, len(DAYS))
    r[-90:] += crash / 90
    c = 100 * np.exp(np.cumsum(r))
    v = rng.integers(1e5, 1e6, len(DAYS)).astype(float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": v}, index=DAYS)


def fake_constituents(name):
    n = {"KOSPI200": 200, "KOSDAQ150": 150, "NASDAQ100": 100, "SP500": 500}[name]
    if name.startswith("KOS"):
        suf = ".KS" if name == "KOSPI200" else ".KQ"
        base = 0 if name == "KOSPI200" else 300000
        rows = [{"code": f"{base + i:06d}", "symbol": f"{base + i:06d}{suf}", "name": f"샘플{name[:3]}{i}", "sector": ""} for i in range(n)]
    else:
        pre = "N" if name == "NASDAQ100" else "S"
        rows = [{"code": f"{pre}{i}", "symbol": f"{pre}{i}", "name": f"Sample {pre}{i} Inc", "sector": "Information Technology"} for i in range(n)]
        if name == "SP500":
            rows[:40] = [{"code": f"N{i}", "symbol": f"N{i}", "name": f"Sample N{i} Inc", "sector": "Information Technology"} for i in range(40)]
    return pd.DataFrame(rows), "mock"


def fake_download(symbols, **_):
    out = {}
    for s in symbols:
        if s.startswith("^"):
            out[s] = fake_series(0.0006, 0.012, crash=-0.35 if s in ("^KS11", "^KQ11") else -0.12)
        else:
            out[s] = fake_series(rng.normal(0.0005, 0.0006), rng.uniform(0.015, 0.035), crash=rng.normal(-0.25, 0.2))
    return out


def fake_fund(symbol):
    years = [2021, 2022, 2023, 2024, 2025]
    rev0 = rng.uniform(1e9, 5e10)
    g = rng.normal(0.15, 0.2)
    m0 = rng.uniform(0.03, 0.3)
    annual, quarters = [], []
    for k, y in enumerate(years):
        rev = rev0 * (1 + g) ** k
        opm = max(-0.1, m0 + rng.normal(0.01, 0.03) * k)
        annual.append({"year": y, "end": f"{y}-12-31", "revenue": rev, "op": rev * opm, "ni": rev * opm * 0.75,
                       "eps": rev * opm * 0.75 / 1e8})
    for k, (y, q) in enumerate([(2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2), (2026, 3)]):
        rev = rev0 / 4 * (1 + g) ** (4 + k / 4)
        opm = m0 + rng.normal(0.02, 0.04)
        quarters.append({"period": f"{y}Q{q}", "end": f"{y}-{q * 3:02d}-28", "revenue": rev, "op": rev * opm,
                         "ni": rev * opm * 0.75, "eps": rev * opm * 0.75 / 4e8})
    kr = symbol[:1].isdigit()
    return {
        "name": None, "sector": random.choice(["Technology", "Healthcare", "Industrials", "Financial Services", "Basic Materials"]),
        "industry": random.choice(["Semiconductors", "Biotechnology", "Engineering & Construction", "Steel", "Software—Application"]),
        "summary": "This is synthetic sample data used to test the dashboard layout. It does not describe a real company.",
        "mktcap": rng.uniform(1e11, 1e13) if kr else rng.uniform(1e10, 1e12),
        "currency": "KRW" if kr else "USD", "inst": None if kr else rng.uniform(0.2, 0.9),
        "roe": rng.uniform(0.02, 0.35), "website": None, "annual": annual, "quarters": quarters,
        "fin_source": "mock",
    }


universe.get_constituents = fake_constituents
prices.download = fake_download
fundamentals.fetch_yf = fake_fund
fundamentals.time.sleep = lambda *_: None
prices.time.sleep = lambda *_: None

if __name__ == "__main__":
    import sys
    sys.argv = ["x", "--markets", "KR,US", "--full"]
    main.main()
