"""KOSPI 시총 상위 100 — 60EMA 대비 -25% 이격 터치 후 단기 반등 백테스트.

신호: 당일 저가 <= 전일 EMA60 x 0.75 (장중 지정가 터치 가정)
진입가: min(전일 EMA60 x 0.75, 당일 시가)  — 갭하락 시 시가 체결
중복 제거: 신호 후 종가가 EMA60 x 0.85 위로 회복해야 재무장 (한 번의 급락 = 1이벤트)
기간: 2000-01-01 이후, yfinance 수정주가(auto_adjust)
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)
GAP = 0.25
BASE = {}
REARM = 0.85
HORIZONS = [3, 5, 10, 20]
TARGETS = [0.05, 0.10]
START = "2000-01-01"
ETF_WORDS = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "KOSEF", "ARIRANG", "PLUS",
             "KIWOOM", "TIMEFOLIO", "스팩", "ETN", "리츠")


def top100():
    rows, seen = [], set()
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
    try:
        for page in range(1, 6):
            r = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}",
                             headers=hdr, timeout=20)
            html = r.content.decode("euc-kr", "ignore")
            for c, nm in re.findall(r'code=(\d{6})"[^>]*class="tltle"[^>]*>([^<]+)<', html):
                nm = nm.strip()
                if c in seen or not c.endswith("0") or any(w in nm.upper() for w in ETF_WORDS):
                    continue
                seen.add(c)
                rows.append((c, nm))
            if len(rows) >= 100:
                break
        if len(rows) >= 90:
            print(f"naver 시총순위 {len(rows)}개")
            return rows[:100], "naver 시총순위(보통주)"
    except Exception as e:  # noqa: BLE001
        print("naver 실패", e)
    df = pd.read_csv(Path(__file__).parent.parent / "data/constituents/KOSPI200.csv", dtype=str)
    return list(zip(df.code[:100], df.name[:100])), "cache KOSPI200 상위100"


def events_for(code, name, df):
    df = df[(df["Open"] > 0) & (df["Volume"] > 0)].copy()
    if len(df) < 120:
        return []
    df["ema60"] = df["Close"].ewm(span=60, adjust=False, min_periods=60).mean()
    df["lvl"] = df["ema60"].shift(1) * (1 - GAP)
    df["ret"] = df["Close"].pct_change().abs()
    df["lim"] = np.where(df.index < "2015-06-15", 0.155, 0.305)
    df["bad"] = (df["ret"] > df["lim"]).astype(int)
    # 기준선: 아무 날 종가에 샀을 때 5일 내 고가 +5% 도달 확률
    fwd_hi = df["High"][::-1].rolling(5, min_periods=5).max()[::-1].shift(-1)
    df["base5"] = (fwd_hi >= df["Close"] * 1.05).astype(float).where(fwd_hi.notna())
    df = df[df.index >= START]
    BASE[code] = float(df["base5"].mean())
    o, h, l, c, lvl, ema = (df[k].to_numpy() for k in ["Open", "High", "Low", "Close", "lvl", "ema60"])
    bad = df["bad"].to_numpy()
    dates = df.index
    out, armed = [], True
    n = len(df)
    for i in range(n):
        if not armed:
            if c[i] > ema[i] * REARM:
                armed = True
            continue
        if np.isnan(lvl[i]) or l[i] > lvl[i]:
            continue
        entry = min(lvl[i], o[i])
        armed = False
        ev = {"code": code, "name": name, "date": str(dates[i].date()), "entry": round(float(entry), 2),
              "gap_open": bool(o[i] < lvl[i]),
              "bad_data": bool(bad[max(0, i - 60):i + 21].sum() > 0)}
        # 진입일 종가 기준 당일 수익
        ev["d0_close_ret"] = c[i] / entry - 1
        for hz in HORIZONS:
            j = min(n, i + 1 + hz)
            if i + 1 >= n or j - (i + 1) < hz:
                ev[f"maxup_{hz}"] = np.nan
                ev[f"mdd_{hz}"] = np.nan
                ev[f"close_{hz}"] = np.nan
                continue
            hh = h[i + 1:j]
            ll = l[i:j]  # 진입일 저가 포함
            ev[f"maxup_{hz}"] = hh.max() / entry - 1
            ev[f"mdd_{hz}"] = ll.min() / entry - 1
            ev[f"close_{hz}"] = c[j - 1] / entry - 1
        # 목표 도달일 (진입 다음날부터, 최대 20일)
        for t in TARGETS:
            d = np.nan
            for k in range(1, 21):
                if i + k >= n:
                    break
                if h[i + k] >= entry * (1 + t):
                    d = k
                    break
            ev[f"days_to_{int(t*100)}"] = d
        # 목표 도달 전 최대 낙폭 (+5% 기준)
        d5 = ev["days_to_5"]
        if not np.isnan(d5):
            ev["mdd_before_5"] = l[i:i + int(d5) + 1].min() / entry - 1
        else:
            ev["mdd_before_5"] = np.nan
        out.append(ev)
    return out


def main():
    tickers, src = top100()
    allev, fails = [], []
    for code, name in tickers:
        sym = f"{code}.KS"
        try:
            df = yf.Ticker(sym).history(period="max", auto_adjust=True)
            df.index = df.index.tz_localize(None)
            evs = events_for(code, name, df)
            allev += evs
            print(f"{sym} {name}: {len(df)}봉, 이벤트 {len(evs)}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{sym} {e}")
            print("FAIL", sym, e)
    ev = pd.DataFrame(allev)
    ev.to_csv(OUT / "events_raw.csv", index=False, encoding="utf-8-sig")
    if ev.empty:
        print("이벤트 없음"); return
    n_raw, n_bad = len(ev), int(ev.bad_data.sum())
    ev = ev[~ev.bad_data & ev.close_10.notna()].copy()
    ev.to_csv(OUT / "events.csv", index=False, encoding="utf-8-sig")
    g = ev.groupby(["code", "name"])
    per = pd.DataFrame({
        "n": g.size(),
        "episodes": g["date"].apply(lambda s: pd.to_datetime(s).dt.to_period("M").nunique()),
        "hit5_in5": g["days_to_5"].apply(lambda s: (s <= 5).mean()),
        "hit5_in10": g["days_to_5"].apply(lambda s: (s <= 10).mean()),
        "hit10_in10": g["days_to_10"].apply(lambda s: (s <= 10).mean()),
        "hit10_in20": g["days_to_10"].apply(lambda s: (s <= 20).mean()),
        "med_days_to_5": g["days_to_5"].median(),
        "avg_maxup_5": g["maxup_5"].mean(),
        "avg_close_5": g["close_5"].mean(),
        "avg_close_10": g["close_10"].mean(),
        "worst_mdd_10": g["mdd_10"].min(),
        "avg_mdd_before_5": g["mdd_before_5"].mean(),
        "first": g["date"].min(),
        "last": g["date"].max(),
    }).reset_index()
    per["base5_in5"] = per["code"].map(BASE)
    per["edge5_in5"] = per["hit5_in5"] - per["base5_in5"]
    per = per.sort_values(["hit5_in5", "n"], ascending=[False, False])
    per.to_csv(OUT / "per_stock.csv", index=False, encoding="utf-8-sig")
    # 연도별 (시장 폭락 클러스터 확인)
    ev["year"] = ev["date"].str[:4]
    yr = ev.groupby("year").agg(n=("code", "size"), hit5_in5=("days_to_5", lambda s: (s <= 5).mean()),
                                avg_close_10=("close_10", "mean")).reset_index()
    yr.to_csv(OUT / "by_year.csv", index=False)
    summ = {
        "universe_source": src, "events_raw": n_raw, "excluded_bad_data": n_bad, "stocks": len(tickers), "fails": fails,
        "events": int(len(ev)), "stocks_with_events": int(ev.code.nunique()),
        "hit5_in5": float((ev.days_to_5 <= 5).mean()), "hit5_in10": float((ev.days_to_5 <= 10).mean()),
        "hit10_in10": float((ev.days_to_10 <= 10).mean()), "hit10_in20": float((ev.days_to_10 <= 20).mean()),
        "avg_close_5": float(ev.close_5.mean()), "avg_close_10": float(ev.close_10.mean()),
        "median_mdd_10": float(ev.mdd_10.median()),
    }
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2))
    print(json.dumps(summ, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
