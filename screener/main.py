"""실행: python -m screener.main --markets KR,US [--full]"""
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import ai_notes, fundamentals, prices, universe
from .common import DATA, DOCS_DATA, load_config, read_json, write_json
from .scoring import fundamental_metrics, score

LATEST = DOCS_DATA / "latest.json"


def pct_rank(values, higher_better=True):
    s = pd.Series(values, dtype=float)
    return s.rank(ascending=higher_better, pct=True)  # 가장 좋은 값 = 1.0


def run_market(mkt, cfg, prev, full=False):
    mcfg = cfg["markets"][mkt]
    print(f"\n===== {mkt} =====")
    unis = {}
    for name in mcfg["indexes"]:
        df, src = universe.get_constituents(name)
        unis[name] = (df, src)
        print(f"{name}: {len(df)}종목 ({src})")

    all_syms = sorted({s for df, _ in unis.values() for s in df["symbol"]})
    idx_syms = list(mcfg["m_indexes"].values())
    if mcfg["base_index"] not in idx_syms:
        idx_syms.append(mcfg["base_index"])
    px = prices.download(idx_syms + all_syms)
    print(f"시세 {len(px)}/{len(all_syms) + len(idx_syms)}")

    m_states = [prices.market_state(n, px[s]) for n, s in mcfg["m_indexes"].items() if s in px]
    if mcfg["base_date"] == "auto":
        base = prices.auto_base_date(px[mcfg["base_index"]])
        base_mode = "auto (최근 1년 지수 최저일)"
    else:
        base = pd.Timestamp(mcfg["base_date"])
        base_mode = "수동 지정"
    # 팔로스루데이 확인 시 새 장세로 자동 전환 (고점 대비 하락폭이 기준 이상일 때만)
    if mcfg.get("base_auto_ftd") and mcfg["base_index"] in px:
        fs = prices.ftd_state(px[mcfg["base_index"]])
        if fs["ftd"] and fs["dd"] <= cfg["thresholds"].get("ftd_new_cycle_dd", -0.2) and pd.Timestamp(fs["low_date"]) > base:
            base = pd.Timestamp(fs["low_date"])
            base_mode = f"자동 전환 (팔로스루데이 {fs['ftd']['date']})"
    print(f"장세 시작일: {base.date()} ({base_mode})")

    rows = []
    for name, (df, _) in unis.items():
        for r in df.itertuples():
            rows.append({"symbol": r.symbol, "code": r.code, "market": mkt})
    fcache = fundamentals.refresh(
        list({r["symbol"]: r for r in rows}.values()),
        cfg["fundamental_refresh_days"], cfg["max_fundamental_refresh_per_run"], full,
    )

    prev_idx = (prev or {}).get("indexes", {})
    out_indexes = {}
    m_down = any(m["status"] == "down" for m in m_states)
    m_label = ", ".join(f"{m['name']} {m['label']}" for m in m_states)
    for name, (df, src) in unis.items():
        recs = []
        for r in df.itertuples():
            if r.symbol not in px:
                continue
            try:
                pm = prices.price_metrics(px[r.symbol], base)
            except Exception as e:  # noqa: BLE001
                print(f"  지표 실패 {r.symbol}: {e}")
                continue
            recs.append({"symbol": r.symbol, "code": r.code, "name": r.name, "sector_idx": r.sector, **pm})
        if not recs:
            continue
        rs = pct_rank([x["rs_raw"] for x in recs])
        mom = pct_rank([x["mom_base"] for x in recs])
        pre = pct_rank([x["pre_base_1y"] for x in recs])
        for i, x in enumerate(recs):
            x["rs"] = round(float(rs.iloc[i]) * 99, 0) if not np.isnan(rs.iloc[i]) else 0
            x["mom_pct"] = round((1 - float(mom.iloc[i])) * 100 + 0.0001, 1) if not np.isnan(mom.iloc[i]) else None
            x["prev_leader"] = bool(not np.isnan(pre.iloc[i]) and pre.iloc[i] >= 0.95)
            f = fcache.get(x["symbol"], {})
            fm = fundamental_metrics(f)
            sc = score(x, fm, f, cfg, x["symbol"])
            x.update(fm)
            x.update(sc)
            x["sector"] = f.get("sector") or x.pop("sector_idx", "")
            x.pop("sector_idx", None)
            x["industry"] = f.get("industry")
            x["name"] = x["name"] if x["name"] and x["name"] != "nan" else f.get("name")
            x["mktcap"] = f.get("mktcap")
            x["currency"] = f.get("currency")
            x["fin_source"] = f.get("fin_source")
            x["has_fin"] = bool(f.get("annual"))
        recs.sort(key=lambda x: (x["grade"] != "탈락", x["score"]), reverse=True)
        prev_top = {s["symbol"] for s in prev_idx.get(name, {}).get("stocks", [])[:30]}
        for rank, x in enumerate(recs, 1):
            x["rank"] = rank
            x["new"] = bool(prev_top) and rank <= 30 and x["symbol"] not in prev_top
        ai_n = ai_notes.annotate(recs, fcache, m_label, cfg)
        print(f"{name}: {len(recs)}종목 점수화, AI 코멘트 {ai_n}건")
        out_indexes[name] = {
            "market": mkt, "label": universe.INDEX_META[name]["label"], "source": src,
            "count": len(recs), "stocks": recs[: cfg["list_top_n"]],
        }
    write_json(fundamentals.CACHE_FILE, fcache)
    details = {}
    for name, block in out_indexes.items():
        for s in block["stocks"]:
            f = fcache.get(s["symbol"], {})
            details[s["symbol"]] = {
                "summary": f.get("summary"), "website": f.get("website"),
                "annual": f.get("annual", [])[-6:], "quarters": f.get("quarters", [])[-8:],
                "ai": s.get("ai") or f.get("ai"),
            }
    market_block = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "asof": max((m["asof"] for m in m_states), default=None),
        "M": m_states, "m_down": m_down,
        "base_date": base.strftime("%Y-%m-%d"), "base_mode": base_mode,
        "days_since_base": int((px[mcfg["base_index"]].index[-1] - base).days),
    }
    return market_block, out_indexes, details


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="KR,US")
    ap.add_argument("--full", action="store_true", help="재무 캐시 전체 갱신")
    args = ap.parse_args()
    cfg = load_config()
    prev = read_json(LATEST, {})
    out = {"generated": None, "markets": dict(prev.get("markets", {})), "indexes": dict(prev.get("indexes", {}))}
    details = read_json(DOCS_DATA / "details.json", {})
    failed = []
    for mkt in [m.strip().upper() for m in args.markets.split(",") if m.strip()]:
        try:
            mb, idx, det = run_market(mkt, cfg, prev, args.full)
        except Exception as e:  # noqa: BLE001  한 시장이 실패해도 나머지는 진행
            print(f"\n!!! {mkt} 실패: {e}")
            failed.append(mkt)
            continue
        out["markets"][mkt] = mb
        out["indexes"].update(idx)
        details.update(det)
        day = mb["asof"] or datetime.now().strftime("%Y-%m-%d")
        write_json(DATA / "history" / f"{day}_{mkt}.json", {
            "market": mb, "top": {n: [{k: s[k] for k in ("symbol", "name", "score", "grade", "rank")} for s in b["stocks"][:30]]
                                  for n, b in idx.items()}})
    keep = {s["symbol"] for b in out["indexes"].values() for s in b["stocks"]}
    details = {k: v for k, v in details.items() if k in keep}
    out["generated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    write_json(LATEST, out)
    write_json(DOCS_DATA / "details.json", details)
    print("\n저장 완료:", LATEST)
    if failed:
        raise SystemExit(f"실패한 시장: {', '.join(failed)} (위 로그 참고)")


if __name__ == "__main__":
    main()
