"""한국어 검색 평가 (ADR-019 기준치). ranx 는 x86 macOS 휠 부재로 제외, 지표는 직접 계산."""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from .config import Settings
from .services import Services

TARGET = {"recall@5": 0.85, "mrr@10": 0.7, "zero_rate": 0.05}
HANGUL = re.compile(r"[가-힣]")


def evaluate(cfg: Settings, backends: list[str]) -> dict[str, Any]:
    qs = yaml.safe_load((cfg.root / "dummy" / "eval.yaml").read_text(encoding="utf-8"))["queries"]
    report: dict[str, Any] = {"queries": len(qs), "korean": sum(bool(HANGUL.search(q["q"])) for q in qs),
                              "target": TARGET, "backends": {}}
    for b in backends:
        r = Services(cfg, b).retriever
        rec, mrr, zero, misses = [], [], 0, []
        for q in qs:
            ids = [x["id"] for x in r.search(q["q"], 10)]
            rel = set(q["relevant"])
            zero += not ids
            rec.append(len(rel & set(ids[:5])) / len(rel))
            rr = next((1 / (i + 1) for i, x in enumerate(ids) if x in rel), 0.0)
            mrr.append(rr)
            if rr < 0.5:
                misses.append({"q": q["q"], "got": ids[:3], "want": q["relevant"]})
        m = {"recall@5": round(sum(rec) / len(qs), 3), "mrr@10": round(sum(mrr) / len(qs), 3),
             "zero_rate": round(zero / len(qs), 3)}
        m["pass"] = m["recall@5"] >= TARGET["recall@5"] and m["mrr@10"] >= TARGET["mrr@10"] and m["zero_rate"] < TARGET["zero_rate"]
        m["misses"] = misses
        report["backends"][b] = m
    (cfg.data / "eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
