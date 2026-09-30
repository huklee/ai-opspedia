"""장애 요약 서술 (LLM 보조, ADR-008). 모든 문장에 [S#] 인용 필수, 실패 시 템플릿 요약 유지."""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SYSTEM = ("너는 운영 위키 편집자. 주어진 출처 발췌만 근거로 장애 요약을 한국어 불릿 2–4개로 작성. "
          "각 불릿 끝에 근거 출처 번호를 [S1] 형식으로 표기. 출처에 없는 사실 금지. 명사형 종결.")


class CitedBullet(BaseModel):
    cites: list[int] = Field(min_length=1, max_length=3, description="근거 번호 (S1 → 1)")
    text: str


class IncidentSummary(BaseModel):
    """cites 를 문장보다 먼저 (제약 디코딩 순서) — 인용 없는 문장을 구조적으로 막음."""
    bullets: list[CitedBullet] = Field(min_length=1, max_length=4)


def sources_of(e: dict[str, Any]) -> list[str]:
    f = e["facts"]["fields"]
    parts = [f"현상: {f.get('description') or ''}", f"근본 원인: {f.get('customfield_root_cause') or ''}",
             f"조치: {f.get('customfield_resolution') or ''}"]
    parts += [f"코멘트 {c['created']}: {c['body']}" for c in f["comment"]["comments"]]
    return [p for p in parts if p.split(":", 1)[1].strip()]


def validate(bullets: list[str], n_sources: int) -> bool:
    if not bullets:
        return False
    for b in bullets:
        refs = [int(x) for x in re.findall(r"\[S(\d+)\]", b)]
        if not refs or any(r < 1 or r > n_sources for r in refs):
            return False
    return True


def narrate_incident(llm, e: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(서술 또는 None, 생성 내역). 실패해도 페이지 생성은 계속 (템플릿 요약 유지)."""
    import time
    srcs = sources_of(e)
    user = e["title"] + "\n\n" + "\n".join(f"[S{i + 1}] {s}" for i, s in enumerate(srcs))
    t0 = time.perf_counter()
    info: dict[str, Any] = {"model": getattr(llm, "label", "llm"), "sources": len(srcs)}
    try:
        out = llm.complete_json(SYSTEM, user, IncidentSummary)
    except Exception as ex:  # LLM 장애는 페이지 생성을 막지 않음
        log.warning("narrate %s: %s", e["title"], ex)
        return None, {**info, "status": "error", "detail": str(ex)[:200]}
    info["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    info.update({k: v for k, v in getattr(llm, "last", {}).items() if k in ("requested", "used", "fallback_reason", "cached")})
    bullets = [re.sub(r"\s*\[S\d+\]", "", b.text).strip() + " " + "".join(f"[S{c}]" for c in b.cites) for b in out.bullets]
    info["bullets"] = bullets
    if not validate(bullets, len(srcs)):
        bad = [b for b in bullets if not validate([b], len(srcs))]
        log.warning("narrate %s: 인용 검사 실패", e["title"])
        return None, {**info, "status": "rejected", "detail": f"인용 없음 · 없는 근거 인용 {len(bad)}개 → 템플릿 요약 유지", "uncited": bad}
    return {"summary": "\n".join(f"- {b}" for b in bullets), "sources": srcs}, {**info, "status": "ok", "detail": "인용 검사 통과"}
