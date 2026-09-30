"""PoC 시연용 mock LLM (실제 GPT-OSS-120B 아님). 출처 발췌를 요약 불릿으로 재조합.

일부러 '자동완성' 장애에서는 인용 없는 문장을 섞어 인용 검사 거절 경로를 보여줌.
"""
from __future__ import annotations

import re
from typing import Any


class MockLLM:
    label = "mock-llm (GPT-OSS-120B 대역)"

    def __init__(self, **_: Any):
        pass

    def complete_json(self, system: str, user: str, schema):
        title = user.split("\n", 1)[0]
        bullets = []
        for m in re.finditer(r"^\[S(\d+)\] ([^:]+): (.+)$", user, re.M):
            n, label, text = m.group(1), m.group(2), m.group(3)
            first = re.split(r"(?<=[.다])\s", text.strip())[0].rstrip(".")
            if label.startswith("코멘트"):
                continue
            head = {"현상": "현상", "근본 원인": "원인", "조치": "조치"}.get(label, label)
            bullets.append({"cites": [int(n)], "text": f"{head}: {first[:90]}"})
        if "자동완성" in title:
            bullets.append({"cites": [99], "text": "재발 방지를 위한 DAG 일시정지 모니터링 강화 필요"})  # 없는 근거 → 거절
        return schema.model_validate({"bullets": bullets[:4]})
