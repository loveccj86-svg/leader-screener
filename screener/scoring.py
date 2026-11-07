"""CAN SLIM + 보고서 3건 규칙으로 점수화"""
import numpy as np


def _growth(a, b):
    if a is None or b is None or b == 0:
        return None
    if b < 0:
        return None
    return a / b - 1


def fundamental_metrics(f):
    q = [x for x in (f.get("quarters") or []) if x.get("period")]
    a = sorted([x for x in (f.get("annual") or []) if x.get("year")], key=lambda x: x["year"])
    out = {"q_eps_yoy": None, "q_rev_yoy": None, "q_turnaround": False, "q_opm": None, "q_opm_prev": None,
           "a_eps_cagr3": None, "a_rev_growth": None, "opm_last": None, "opm_prev": None, "opm_trend": None}
    if q:
        last = q[-1]
        y, qq = last["period"].split("Q")
        prev = next((x for x in q if x["period"] == f"{int(y) - 1}Q{qq}"), None)
        if prev:
            out["q_eps_yoy"] = _growth(last.get("eps"), prev.get("eps"))
            out["q_rev_yoy"] = _growth(last.get("revenue"), prev.get("revenue"))
            if (prev.get("eps") is not None and prev["eps"] <= 0 and (last.get("eps") or 0) > 0):
                out["q_turnaround"] = True
            if last.get("revenue") and prev.get("revenue") and last.get("op") is not None and prev.get("op") is not None:
                out["q_opm"] = last["op"] / last["revenue"]
                out["q_opm_prev"] = prev["op"] / prev["revenue"]
    if len(a) >= 2:
        out["a_rev_growth"] = _growth(a[-1].get("revenue"), a[-2].get("revenue"))
    if len(a) >= 4:
        e0, e3 = a[-4].get("eps"), a[-1].get("eps")
        if e0 and e3 and e0 > 0 and e3 > 0:
            out["a_eps_cagr3"] = (e3 / e0) ** (1 / 3) - 1
    opms = [(x["op"] / x["revenue"]) for x in a if x.get("op") is not None and x.get("revenue")]
    if len(opms) >= 2:
        out["opm_last"], out["opm_prev"] = opms[-1], opms[-2]
        if len(opms) >= 3 and opms[-1] < opms[-2] < opms[-3]:
            out["opm_trend"] = "down2"
        elif opms[-1] > opms[-2]:
            out["opm_trend"] = "up"
        elif opms[-1] < opms[-2]:
            out["opm_trend"] = "down"
        else:
            out["opm_trend"] = "flat"
    return out


def _chk(key, group, label, passed, value, pts, got):
    return {"key": key, "group": group, "label": label, "pass": passed, "value": value, "pts": pts, "got": got}


def score(p, fm, f, cfg, symbol):
    th = cfg["thresholds"]
    checks = []

    # ---- CAN SLIM ----
    v = fm["q_eps_yoy"]
    if v is None and not fm["q_turnaround"]:
        checks.append(_chk("C", "CAN SLIM", "분기 EPS YoY ≥25%", None, None, 15, 0))
    elif fm["q_turnaround"]:
        checks.append(_chk("C", "CAN SLIM", "분기 EPS 흑자전환", True, "흑전", 15, 8))
    else:
        checks.append(_chk("C", "CAN SLIM", "분기 EPS YoY ≥25%", v >= th["c_eps_yoy"], v, 15,
                           15 if v >= th["c_eps_yoy"] else (5 if v > 0 else 0)))

    v, roe = fm["a_eps_cagr3"], f.get("roe")
    if v is None:
        checks.append(_chk("A", "CAN SLIM", "3년 EPS 연평균 ≥25%", None, None, 15, 0))
    else:
        got = (10 if v >= th["a_eps_cagr"] else (4 if v > 0.1 else 0)) + (5 if roe and roe >= th["a_roe"] else 0)
        checks.append(_chk("A", "CAN SLIM", "3년 EPS 연평균 ≥25% · ROE ≥17%", v >= th["a_eps_cagr"], v, 15, got))

    v = p["from_hi52"]
    checks.append(_chk("N", "CAN SLIM", "52주 고점 -15% 이내", v >= th["n_from_high"], v, 10,
                       10 if v >= th["n_from_high"] else (5 if v >= -0.25 else 0)))

    v = p["ud_ratio"]
    if v is None or (isinstance(v, float) and np.isnan(v)):
        checks.append(_chk("S", "CAN SLIM", "50일 상승/하락 거래량비 ≥1", None, None, 10, 0))
    else:
        checks.append(_chk("S", "CAN SLIM", "50일 상승/하락 거래량비 ≥1", v >= th["s_ud_ratio"], v, 10,
                           10 if v >= 1.2 else (5 if v >= th["s_ud_ratio"] else 0)))

    v = p["rs"]
    checks.append(_chk("L", "CAN SLIM", "RS 상위 20%", v >= th["l_rs"], v, 15,
                       15 if v >= th["l_rs"] else (8 if v >= 70 else 0)))

    v = f.get("inst")
    if v is None:
        checks.append(_chk("I", "CAN SLIM", "기관 보유 ≥30%", None, None, 5, 0))
    else:
        checks.append(_chk("I", "CAN SLIM", "기관 보유 ≥30%", v >= th["i_inst"], v, 5, 5 if v >= th["i_inst"] else 0))

    # ---- DB 생로병사 ----
    v = p["mom_pct"]
    checks.append(_chk("DB1", "DB 생로병사", "장세 시작 후 수익률 상위 5%", v is not None and v <= th["db_mom_top_pct"],
                       p["mom_base"], 7, 7 if v is not None and v <= th["db_mom_top_pct"] else (3 if v is not None and v <= 20 else 0)))
    v = p["cycle_dd"]
    ok = v is not None and not np.isnan(v) and v > th["db_cycle_dd"]
    checks.append(_chk("DB2", "DB 생로병사", "장세 고점 대비 -30% 이내", ok, v, 5, 5 if ok else 0))
    v = p["w10_gt_w40"]
    checks.append(_chk("DB3", "DB 생로병사", "10주선 > 40주선", v, None, 3, 3 if v else 0))

    # ---- 하나 생애주기 ----
    v = fm["q_rev_yoy"] if fm["q_rev_yoy"] is not None else fm["a_rev_growth"]
    if v is None:
        checks.append(_chk("H1", "하나 생애주기", "매출 성장 ≥20% (탄생)", None, None, 5, 0))
    else:
        checks.append(_chk("H1", "하나 생애주기", "매출 성장 ≥20% (탄생)", v >= th["hana_rev_growth"], v, 5,
                           5 if v >= th["hana_rev_growth"] else (2 if v > 0.05 else 0)))
    if fm["q_opm"] is not None:
        up = fm["q_opm"] > fm["q_opm_prev"]
        checks.append(_chk("H2", "하나 생애주기", "영업이익률 상승 (분기 YoY)", up, fm["q_opm"] - fm["q_opm_prev"], 5, 5 if up else 0))
    elif fm["opm_trend"]:
        up = fm["opm_trend"] == "up"
        checks.append(_chk("H2", "하나 생애주기", "영업이익률 상승 (연간)", up, (fm["opm_last"] or 0) - (fm["opm_prev"] or 0), 5, 5 if up else 0))
    else:
        checks.append(_chk("H2", "하나 생애주기", "영업이익률 상승", None, None, 5, 0))

    # ---- 태그 ----
    tags = []
    if not ok and p["cycle_dd"] is not None and not np.isnan(p["cycle_dd"]):
        tags.append({"t": "탈락 -30%", "k": "bad"})
    if p["prev_leader"]:
        tags.append({"t": "직전 주도주", "k": "warn"})
    rev_up = (fm["q_rev_yoy"] or fm["a_rev_growth"] or 0) > 0
    opm_down = (fm["q_opm"] is not None and fm["q_opm"] < fm["q_opm_prev"]) or fm["opm_trend"] == "down2"
    if rev_up and opm_down:
        tags.append({"t": "OPM 하락 (소멸 경고)", "k": "bad"})
    ind = (f.get("industry") or "")
    commodity = symbol in cfg["commodity_symbols"] or ind in cfg["commodity_industries"]
    if commodity:
        tags.append({"t": "범용재·이익지속성 주의", "k": "warn"})
    if p["above_ma50"] and p["from_hi52"] >= -0.05 and (p["vol_ratio"] or 0) >= 1.4:
        tags.append({"t": "거래량 동반 신고가권", "k": "good"})

    # ---- 합산 ----
    avail_max = sum(c["pts"] for c in checks if c["pass"] is not None)
    got = sum(c["got"] for c in checks)
    total = 100 * got / avail_max if avail_max else 0
    if commodity:
        total -= 5
    total = max(0.0, min(100.0, total))
    coverage = avail_max / sum(c["pts"] for c in checks)
    canslim_pass = sum(1 for c in checks if c["group"] == "CAN SLIM" and c["pass"])

    if any(t["t"].startswith("탈락") for t in tags):
        grade = "탈락"
    elif total >= 70 and canslim_pass >= 4 and (p["rs"] or 0) >= 70:  # 주도주는 모멘텀이 먼저 (DB)
        grade = "주도주 후보"
    elif total >= 50:
        grade = "관찰"
    else:
        grade = "약함"
    return {"score": round(total, 1), "coverage": round(coverage, 2), "canslim_pass": canslim_pass,
            "grade": grade, "checks": checks, "tags": tags}
