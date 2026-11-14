"""생존 보드 — 하락장에서 지수보다 덜 빠지는(살아남는) 종목을 가려내는 지표.

낙폭 비율 = 종목 52주 고점대비 낙폭 ÷ 지수 52주 고점대비 낙폭
  · 현재 비율: 지금 시점
  · 최대 비율: 지수 52주 고점일 이후 종목의 최대 낙폭 ÷ 지수의 최대 낙폭
  · 지수 낙폭이 min_idx_dd(-5%)보다 얕으면 비율이 튀므로 계산하지 않는다(None)
충격일 = 최근 window 거래일 중 지수 하루 등락률이 소충격 기준 이하인 날
  · 그날 종목 등락률 - 지수 등락률 ≥ +band → g(방어), ≤ -band → r(더 빠짐), 그 사이 → n
  · 강도: S(소) / M(중) / L(대) — 지수마다 기준이 다르다
"""
import numpy as np
import pandas as pd

DEFAULT = {
    "index_map": {"KOSPI200": "^KS11", "KOSDAQ150": "^KQ11", "NASDAQ100": "^IXIC", "SP500": "^GSPC"},
    "index_name": {"^KS11": "코스피", "^KQ11": "코스닥", "^IXIC": "나스닥", "^GSPC": "S&P500"},
    # 소 / 중 / 대 충격 (지수 하루 등락률)
    "tiers": {"^GSPC": [-0.01, -0.015, -0.025], "^IXIC": [-0.015, -0.02, -0.03],
              "^KS11": [-0.015, -0.025, -0.035], "^KQ11": [-0.02, -0.03, -0.04]},
    "window": 60,          # 충격일을 찾는 최근 거래일 수
    "band": 0.005,         # 지수 대비 ±0.5%p 이내는 '비슷'
    "min_idx_dd": -0.05,   # 지수 낙폭이 이보다 얕으면 비율 계산 안 함
    "series_days": 90,     # 상세 차트용 낙폭 추이 일수
}


def conf(cfg):
    out = dict(DEFAULT)
    out.update((cfg or {}).get("survival", {}))
    return out


def _dd(c):
    """매일의 52주(252봉) 고점대비 낙폭"""
    return c / c.rolling(252, min_periods=20).max() - 1


def index_block(idx_df, isym, sc):
    c = idx_df["Close"].dropna()
    if len(c) < 120:
        return None
    w = c.iloc[-252:]
    peak_date = w.idxmax()
    dd_now = float(c.iloc[-1] / w.max() - 1)
    mdd = float(c.loc[peak_date:].min() / c.loc[peak_date] - 1)
    t = sc["tiers"].get(isym, [-0.015, -0.02, -0.03])
    r = c.pct_change().iloc[-sc["window"]:]
    shocks = []
    for d, v in r.items():
        if np.isfinite(v) and v <= t[0]:
            shocks.append({"d": d.strftime("%Y-%m-%d"), "r": round(float(v), 4),
                           "t": "L" if v <= t[2] else "M" if v <= t[1] else "S"})
    dd = _dd(c).iloc[-sc["series_days"]:]
    return {
        "index": isym, "name": sc["index_name"].get(isym, isym),
        "dd": round(dd_now, 4), "mdd": round(mdd, 4), "peak": peak_date.strftime("%Y-%m-%d"),
        "tiers": t, "window": sc["window"], "min_idx_dd": sc["min_idx_dd"], "band": sc["band"],
        "ratio_ok": dd_now <= sc["min_idx_dd"], "ratio_max_ok": mdd <= sc["min_idx_dd"],
        "shocks": shocks,
        "dates": [x.strftime("%Y-%m-%d") for x in dd.index],
        "series": [None if not np.isfinite(v) else round(float(v) * 100, 1) for v in dd.values],
    }


def stock_metrics(df, ib, sc, kr=False):
    """(생존 지표 dict, 상세 차트용 낙폭 추이 list) 반환"""
    c = df["Close"].dropna()
    c = c[c > 0]
    if len(c) < 60 or ib is None:
        return None, None
    rr = c.pct_change()
    # 국장은 가격제한폭 ±30% → 하루 -31% 이하는 액면분할 등 비수정 데이터로 본다
    bad = bool(kr and (rr.iloc[-260:] <= -0.31).any())
    if bad:
        return {"bad": True}, None
    w = c.iloc[-252:]
    dd_now = float(c.iloc[-1] / w.max() - 1)
    ddser = _dd(c)
    since = ddser.loc[ddser.index >= pd.Timestamp(ib["peak"])]
    mdd = float(since.min()) if len(since) else dd_now
    r_now = dd_now / ib["dd"] if ib["ratio_ok"] else None
    r_max = mdd / ib["mdd"] if ib["ratio_max_ok"] else None

    marks, srets = [], []
    for s in ib["shocks"]:
        d = pd.Timestamp(s["d"])
        v = rr.get(d, np.nan)
        if not np.isfinite(v):
            marks.append("-"); srets.append(None); continue
        diff = v - s["r"]
        marks.append("g" if diff >= sc["band"] else "r" if diff <= -sc["band"] else "n")
        srets.append(round(float(v), 4))
    valid = [m for m in marks if m != "-"]
    streak = 0
    for m in reversed(valid):
        if m != "g":
            break
        streak += 1
    big = [m for m, s in zip(marks, ib["shocks"]) if s["t"] == "L" and m != "-"]
    sv = {
        "dd": round(dd_now, 4), "mdd": round(mdd, 4),
        "r": None if r_now is None else round(r_now, 2),
        "rm": None if r_max is None else round(r_max, 2),
        "m": "".join(marks), "sr": srets,
        "ok": valid.count("g"), "n": len(valid), "streak": streak,
        "big_ok": big.count("g"), "big_n": len(big),
        "last": valid[-1] if valid else None,
    }
    # 상세 차트: 지수 낙폭 추이와 같은 날짜에 맞춘다
    al = ddser.reindex(pd.to_datetime(ib["dates"]))
    series = [None if not np.isfinite(v) else round(float(v) * 100, 1) for v in al.values]
    return sv, series
