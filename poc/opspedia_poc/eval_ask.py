"""자연어 질의 평가: 검색 3방식 비교(키워드 · +규칙 엔티티 · +LLM 재작성) + 답변 품질."""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

import yaml

from .config import Settings
from .services import Services


def evaluate(cfg: Settings) -> dict[str, Any]:
    qs = yaml.safe_load((cfg.root / "dummy" / "eval_nl.yaml").read_text(encoding="utf-8"))["queries"]
    svc = Services(cfg)
    a = svc.asker
    modes = {"키워드만": [], "+규칙 엔티티": [], "+LLM 재작성": []}
    rows = []
    for q in qs:
        rel = set(q["relevant"])
        kw = [x["id"] for x in svc.retriever.search(q["q"], 5)]
        plan = a.plan(q["q"])
        rule = [d["id"] for d in a.retrieve(q["q"], {**plan, "llm": None}, 5)]
        full = [d["id"] for d in a.retrieve(q["q"], plan, 5)]
        for m, ids in zip(modes, (kw, rule, full)):
            modes[m].append(len(rel & set(ids)) / len(rel))
        t0 = time.perf_counter()
        r = a.ask(q["q"])
        ms = (time.perf_counter() - t0) * 1000
        text = " ".join(r["answer"]["sentences"]).lower()
        hit = any(str(e).lower() in text for e in q["expect"])
        rows.append({"q": q["q"], "intent": plan["intent"], "status": r["answer"]["status"], "fact_hit": hit,
                     "ms": round(ms), "answer": r["answer"]["sentences"]})
    rep = {"queries": len(qs), "model": getattr(a.llm, "label", None),
           "recall@5": {m: round(statistics.mean(v), 3) for m, v in modes.items()},
           "answer": {"fact_hit": round(sum(r["fact_hit"] for r in rows) / len(rows), 3),
                      "status": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})},
                      "p50_ms": round(statistics.median(r["ms"] for r in rows))},
           "rows": rows}
    (cfg.data / "eval_ask.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep
