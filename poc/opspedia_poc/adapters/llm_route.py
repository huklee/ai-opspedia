"""LLM 라우팅: 1순위 GPT-OSS-120B(사내 OpenAI 호환 엔드포인트), 엔드포인트 미설정 · 장애 시 PoC 대역 모델.

실제로 응답한 모델을 last['used'] 에 기록 → 생성 이력에 "요청 모델 / 실제 모델" 을 그대로 표시.
"""
from __future__ import annotations

import os
from typing import Any


class RoutedLLM:
    def __init__(self, primary: dict[str, Any], fallback: dict[str, Any] | None, make):
        self.primary_spec, self.fallback_spec = primary, fallback
        url = os.environ.get(primary.get("base_url_env", ""), "") or primary.get("base_url") or ""
        self.primary = make({**primary, "base_url": url}) if url else None
        self.fallback = make(fallback) if fallback else None
        self.requested = primary["model"]
        self.label = primary["model"] if self.primary else f"{primary['model']} (대역 {fallback['model']})" if fallback else primary["model"]
        self.last: dict[str, Any] = {}

    def complete_json(self, system: str, user: str, schema):
        if self.primary is not None:
            try:
                out = self.primary.complete_json(system, user, schema)
                self.last = {**getattr(self.primary, "last", {}), "requested": self.requested, "used": self.requested}
                return out
            except Exception as ex:  # 엔드포인트 장애 → 대역
                reason = f"{type(ex).__name__}: {str(ex)[:80]}"
                if self.fallback is None:
                    raise
        else:
            reason = f"엔드포인트 미설정 ({self.primary_spec.get('base_url_env')})"
        out = self.fallback.complete_json(system, user, schema)
        self.last = {**getattr(self.fallback, "last", {}), "requested": self.requested,
                     "used": self.fallback_spec["model"], "fallback_reason": reason}
        return out
