"""LLMClient: openai SDK → OpenAI 호환 엔드포인트(GPT-OSS-120B, ADR-018). 본 구현은 httpx 직접 호출."""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ValidationError


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key_env: str = "GPT_OSS_API_KEY", http_client=None,
                 label: str | None = None, temperature: float = 0.2, **_: Any):
        from openai import OpenAI
        self.model, self.temperature = model, temperature
        self.label = label or model
        self.client = OpenAI(base_url=base_url, api_key=os.environ.get(api_key_env, "none"),
                             http_client=http_client, max_retries=1, timeout=120)

    def complete_json(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        """JSON schema 강제 요청 → 미지원·검증 실패 시 1회 재시도."""
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        fmt = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        last: Exception | None = None
        for attempt in range(2):
            r = self.client.chat.completions.create(model=self.model, messages=msgs,
                                                    response_format=fmt, temperature=self.temperature)
            text = r.choices[0].message.content or ""
            try:
                return schema.model_validate(json.loads(_strip_fence(text)))
            except (json.JSONDecodeError, ValidationError) as e:
                last = e
                msgs += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"JSON 스키마 위반: {e}. 스키마에 맞는 JSON 만 다시 출력."}]
        raise ValueError(f"LLM 구조화 출력 실패: {last}")


def _strip_fence(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return t
