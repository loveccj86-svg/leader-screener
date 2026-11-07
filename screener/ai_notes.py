"""상위 후보에 대해 Claude가 reference/leader_rules.md 를 읽고 한국어 회사 소개·판정 코멘트를 작성 (선택).
ANTHROPIC_API_KEY 가 없으면 건너뛴다."""
import json
import os
import re
from datetime import date

import requests

from .common import ROOT, clean

RULES = (ROOT / "reference" / "leader_rules.md").read_text(encoding="utf-8")

SYSTEM = (
    "너는 한국어로 쓰는 주식 리서치 보조다. 아래 '주도주 판정 규칙' 문서만 기준으로 삼아 종목을 평가한다. "
    "주어진 데이터에 없는 숫자는 만들지 않는다. 매수·매도 권유가 아니라 규칙 대비 위치를 설명한다.\n\n"
    "=== 주도주 판정 규칙 ===\n" + RULES
)

PROMPT = """종목 데이터(JSON):
{data}

시장 상태(M): {market}

아래 JSON 형식으로만 답하라. 다른 말은 쓰지 마라.
{{"intro": "어떤 회사인지 2문장 (주력 사업·매출원)",
  "verdict": "규칙(CAN SLIM, DB 생로병사, 하나 생애주기, 신영 신호) 기준 판정 3문장 이내. 통과·미달 항목을 짚고, 주도주 단계(탄생/2차상승/소멸 경고/탈락)를 말할 것",
  "watch": "다음에 확인할 것 1문장 (예: 다음 분기 OPM, 신고가 돌파 시 거래량)"}}"""


def _call(api_key, model, data, market):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": model,
            "max_tokens": 700,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": PROMPT.format(data=json.dumps(clean(data), ensure_ascii=False), market=market)}],
        },
        timeout=60,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def annotate(stocks, fund_cache, market_label, cfg):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return 0
    model = os.getenv("AI_MODEL") or cfg.get("ai_model", "claude-haiku-5-5")
    n = 0
    today = date.today()
    for s in stocks[: cfg["ai_top_n_per_index"]]:
        f = fund_cache.get(s["symbol"], {})
        old = f.get("ai") or {}
        try:
            age = (today - date.fromisoformat(old.get("date", "2000-01-01"))).days
        except ValueError:
            age = 999
        if age < cfg["ai_refresh_days"] and old.get("grade") == s["grade"]:
            s["ai"] = old
            continue
        data = {
            "symbol": s["symbol"], "name": s["name"], "sector": s.get("sector"), "industry": s.get("industry"),
            "summary_en": (f.get("summary") or "")[:600],
            "score": s["score"], "grade": s["grade"],
            "checks": [{k: c[k] for k in ("key", "label", "pass", "value")} for c in s["checks"]],
            "tags": [t["t"] for t in s["tags"]],
            "price": {k: s.get(k) for k in ("from_hi52", "cycle_dd", "mom_base", "rs", "r3m", "r12m")},
            "annual": f.get("annual", [])[-5:], "quarters": f.get("quarters", [])[-5:],
        }
        try:
            res = _call(key, model, data, market_label)
            if res:
                res.update({"date": today.isoformat(), "grade": s["grade"], "model": model})
                f["ai"] = res
                fund_cache[s["symbol"]] = f
                s["ai"] = res
                n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  AI 실패 {s['symbol']}: {e}")
    return n
