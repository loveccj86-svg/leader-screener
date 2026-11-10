"""시세 수집(yfinance)과 가격 기반 지표 계산"""
import time

import numpy as np
import pandas as pd


def _norm(d):
    d = d.copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d.dropna(subset=["Close"])
    d = d[d["Close"] > 0]                       # 야후 국장에 섞이는 0 행 제거
    return d[~d.index.duplicated(keep="last")]


def download(symbols, period="3y", chunk=80, pause=2.0):
    import yfinance as yf

    out = {}
    symbols = list(dict.fromkeys(symbols))
    for i in range(0, len(symbols), chunk):
        part = symbols[i : i + chunk]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(
                    part, period=period, interval="1d", auto_adjust=True,
                    group_by="ticker", threads=True, progress=False,
                )
                break
            except Exception:  # noqa: BLE001
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            continue
        multi = isinstance(df.columns, pd.MultiIndex)
        lv0 = set(df.columns.get_level_values(0)) if multi else set()
        for s in part:
            try:
                d = df[s] if multi and s in lv0 else (df if not multi and len(part) == 1 else None)
            except KeyError:
                d = None
            if d is None or "Close" not in d:
                continue
            d = _norm(d)
            if len(d) >= 60:
                out[s] = d
        time.sleep(pause)
    return out


# ── 국장 보정: 야후가 최근 봉을 빠뜨리면 네이버 일봉으로 교체 ─────────────────
_NAVER_IDX = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}


def naver_daily(code, count=800):
    """네이버 fchart 일봉 (수정주가 아님). code: 005930 / KOSPI / KOSDAQ"""
    import re
    import requests

    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
    )
    r.raise_for_status()
    rows = []
    for item in re.findall(r'data="([^"]+)"', r.text):
        f = item.split("|")
        if len(f) < 6:
            continue
        try:
            rows.append((pd.Timestamp(f[0]), *(float(x) for x in f[1:6])))
        except ValueError:
            continue
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    return _norm(d)


def _naver_code(sym):
    if sym in _NAVER_IDX:
        return _NAVER_IDX[sym]
    if sym.endswith((".KS", ".KQ")):
        return sym.split(".")[0]
    return None


def kr_freshen(px, symbols, workers=8):
    """국장 심볼 중 최근 거래일이 빠진 것만 네이버 일봉으로 교체. 바뀐 개수 반환"""
    from concurrent.futures import ThreadPoolExecutor

    try:
        ref = naver_daily("KOSPI", 10)
        latest = ref.index[-1] if ref is not None and len(ref) else None
    except Exception as e:  # noqa: BLE001
        print(f"  네이버 기준일 조회 실패: {e}")
        return 0
    if latest is None:
        return 0
    targets = [s for s in dict.fromkeys(symbols) if _naver_code(s)
               and (s not in px or px[s].index[-1] < latest)]
    print(f"  국장 기준일 {latest.date()} — 야후 누락/지연 {len(targets)}종목 네이버로 보정")

    def one(s):
        try:
            d = naver_daily(_naver_code(s))
            return s, d if d is not None and len(d) >= 60 else None
        except Exception:  # noqa: BLE001
            return s, None

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for s, d in ex.map(one, targets):
            if d is not None:
                px[s] = d
                n += 1
    print(f"  네이버 보정 완료 {n}/{len(targets)}")
    return n


def auto_base_date(index_df, lookback=250):
    c = index_df["Close"].iloc[-lookback:]
    return c.idxmin()


def _ret(c, n):
    return c.iloc[-1] / c.iloc[-n - 1] - 1 if len(c) > n else np.nan


def price_metrics(d, base_date):
    c, v = d["Close"], d["Volume"].fillna(0)
    last = float(c.iloc[-1])
    r3, r6, r9, r12 = (_ret(c, n) for n in (63, 126, 189, 252))
    parts = [(0.4, r3), (0.2, r6), (0.2, r9), (0.2, r12)]
    avail = [(w, r) for w, r in parts if not np.isnan(r)]
    rs_raw = sum(w * r for w, r in avail) / sum(w for w, _ in avail) if avail else np.nan

    hi52 = float(c.iloc[-252:].max())
    since = c[c.index >= base_date]
    if len(since):
        mom = last / float(since.iloc[0]) - 1
        cyc_hi = float(since.max())
        cyc_dd = last / cyc_hi - 1
    else:
        mom = cyc_hi = cyc_dd = np.nan
    prev = c[c.index < base_date]
    pre1y = float(prev.iloc[-1] / prev.iloc[-253] - 1) if len(prev) > 253 else np.nan

    wk = c.resample("W-FRI").last().dropna()
    w10 = wk.rolling(10).mean().iloc[-1] if len(wk) >= 10 else np.nan
    w40 = wk.rolling(40).mean().iloc[-1] if len(wk) >= 40 else np.nan
    ma50 = c.rolling(50).mean().iloc[-1]
    ma200 = c.rolling(200).mean().iloc[-1] if len(c) >= 200 else np.nan

    chg = c.diff().iloc[-50:]
    vv = v.iloc[-50:]
    down_vol = vv[chg < 0].sum()
    ud = vv[chg > 0].sum() / down_vol if down_vol > 0 else np.nan
    avg50 = v.iloc[-51:-1].mean()
    vol_ratio = float(v.iloc[-1] / avg50) if avg50 > 0 else np.nan

    spark = wk.iloc[-52:]
    return {
        "price": last,
        "asof": c.index[-1].strftime("%Y-%m-%d"),
        "chg1d": _ret(c, 1),
        "r3m": r3, "r6m": r6, "r12m": r12,
        "rs_raw": rs_raw,
        "hi52": hi52,
        "from_hi52": last / hi52 - 1,
        "mom_base": mom,
        "cycle_high": cyc_hi,
        "cycle_dd": cyc_dd,
        "pre_base_1y": pre1y,
        "w10_gt_w40": bool(w10 > w40) if not (np.isnan(w10) or np.isnan(w40)) else None,
        "above_ma50": bool(last > ma50) if not np.isnan(ma50) else None,
        "above_ma200": bool(last > ma200) if not np.isnan(ma200) else None,
        "ud_ratio": ud,
        "vol_ratio": vol_ratio,
        "weekly": [round(float(x), 4) for x in spark.values],
        "weekly_dates": [x.strftime("%Y-%m-%d") for x in spark.index],
    }


def ftd_state(d, gain=0.0125, min_day=4, min_dd=-0.10):
    """오닐 팔로스루데이: 52주 고점 이후 최저 종가를 바닥 후보로 보고,
    반등 {min_day}일째 이후 +1.25% 이상 & 거래량 전일 대비 증가한 첫날을 팔로스루데이로 판정."""
    c, v = d["Close"].dropna(), d["Volume"].reindex(d["Close"].dropna().index).fillna(0)
    w = c.iloc[-252:]
    peak_i = int(np.argmax(w.values))
    after = w.iloc[peak_i:]
    low_i = int(np.argmin(after.values))
    low_date, low = after.index[low_i], float(after.iloc[low_i])
    peak_date, peak = w.index[peak_i], float(w.iloc[peak_i])
    dd = low / peak - 1
    pos = c.index.get_loc(low_date)
    days = len(c) - 1 - pos
    has_vol = v.iloc[-60:].sum() > 0
    ftd = None
    for k in range(pos + min_day, len(c)):
        chg = c.iloc[k] / c.iloc[k - 1] - 1
        if chg >= gain and (not has_vol or v.iloc[k] > v.iloc[k - 1]):
            ftd = {"date": c.index[k].strftime("%Y-%m-%d"), "day": k - pos, "chg": float(chg)}
            break
    if dd > min_dd:
        stage, text = "none", f"고점 대비 {dd:.0%} — 의미 있는 조정 아님"
    elif ftd:
        stage, text = "ftd", f"팔로스루데이 {ftd['date']} (반등 {ftd['day']}일째 +{ftd['chg']:.1%}) → 새 장세 시작 후보 {low_date:%Y-%m-%d}"
    elif days == 0:
        stage, text = "falling", f"바닥 미확인 — 오늘이 신저가 (고점 대비 {dd:.0%})"
    else:
        stage, text = "rally", f"바닥 후보 {low_date:%Y-%m-%d} · 반등 {days}일째 · 팔로스루데이 대기"
    return {"peak_date": peak_date.strftime("%Y-%m-%d"), "low_date": low_date.strftime("%Y-%m-%d"),
            "dd": float(dd), "from_low": float(c.iloc[-1] / low - 1), "rally_days": int(days),
            "ftd": ftd, "stage": stage, "text": text, "vol_used": bool(has_vol)}


def market_state(name, d):
    """CAN SLIM M + 신영 신호(2) 큰 상승/하락일 균형 + 분산일"""
    c, v = d["Close"], d["Volume"].fillna(0)
    last = float(c.iloc[-1])
    ma50 = float(c.rolling(50).mean().iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else np.nan
    hi52 = float(c.iloc[-252:].max())
    r = c.pct_change()
    sigma = r.iloc[-378:-126].std() if len(r) > 378 else r.iloc[:-126].std()
    recent = r.iloc[-126:]
    big_up = int((recent > 3 * sigma).sum()) if sigma and not np.isnan(sigma) else None
    big_dn = int((recent < -3 * sigma).sum()) if sigma and not np.isnan(sigma) else None
    dist = 0
    if v.iloc[-25:].sum() > 0:
        for i in range(len(c) - 25, len(c)):
            if r.iloc[i] <= -0.002 and v.iloc[i] > v.iloc[i - 1]:
                dist += 1
    else:
        dist = None
    above50, above200 = last > ma50, (last > ma200 if not np.isnan(ma200) else None)
    if above50 and above200:
        status, label = "up", "상승 추세"
    elif not above50 and above200 is False:
        status, label = "down", "하락 추세"
    else:
        status, label = "pressure", "조정/압박"
    return {
        "name": name,
        "close": last,
        "asof": c.index[-1].strftime("%Y-%m-%d"),
        "chg1d": float(r.iloc[-1]),
        "ma50": ma50, "ma200": ma200,
        "from_hi52": last / hi52 - 1,
        "big_up_6m": big_up, "big_down_6m": big_dn,
        "dist_days_25": dist,
        "status": status, "label": label,
        "ftd": ftd_state(d),
    }
