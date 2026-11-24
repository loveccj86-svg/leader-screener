"""일별 순위 히스토리를 대시보드용 한 파일로 모읍니다.

data/history/YYYY-MM-DD_{KR,US}.json 들을 읽어
docs/data/ranks.json 하나로 합칩니다. 브라우저가 파일 수백 개를 받지 않도록.
"""
import json
import re
from pathlib import Path

DAYS = 120          # 화면에 보여줄 최대 거래일 수
FNAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_([A-Z]+)\.json$")


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  깨진 파일 하나가 전체를 막지 않게
        return None


def build(hist_dir: Path, out_path: Path, days: int = DAYS) -> dict:
    """히스토리 폴더를 읽어 ranks.json 을 쓰고, 요약을 돌려줍니다."""
    by_mkt: dict[str, list[tuple[str, Path]]] = {}
    for p in sorted(hist_dir.glob("*.json")):
        m = FNAME.match(p.name)
        if m:
            by_mkt.setdefault(m.group(2), []).append((m.group(1), p))

    dates: dict[str, list] = {}
    series: dict[str, dict] = {}
    for _mkt, items in by_mkt.items():
        items = sorted(items)[-days:]
        for col, (day, path) in enumerate(items):
            doc = _load(path)
            if not doc:
                continue
            for idx, rows in (doc.get("top") or {}).items():
                ds = dates.setdefault(idx, [])
                if day not in ds:
                    ds.append(day)
                slot = len(ds) - 1
                bucket = series.setdefault(idx, {})
                for s in rows:
                    sym = s.get("symbol")
                    if not sym:
                        continue
                    e = bucket.setdefault(sym, {"name": s.get("name") or sym, "r": {}, "sc": {}})
                    e["name"] = s.get("name") or e["name"]
                    e["r"][slot] = s.get("rank")
                    e["sc"][slot] = s.get("score")

    out = {"dates": {}, "series": {}}
    for idx, ds in dates.items():
        n = len(ds)
        out["dates"][idx] = ds
        rows = {}
        for sym, e in series.get(idx, {}).items():
            r = [e["r"].get(i) for i in range(n)]
            if all(v is None for v in r):
                continue
            rows[sym] = {"n": e["name"], "r": r,
                         "s": [e["sc"].get(i) for i in range(n)]}
        out["series"][idx] = rows

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
    return {"indexes": {k: len(v) for k, v in out["series"].items()},
            "days": {k: len(v) for k, v in out["dates"].items()},
            "kb": round(out_path.stat().st_size / 1024, 1)}
