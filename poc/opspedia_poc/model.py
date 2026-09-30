"""지식 모델의 PoC 부분집합 (docs/knowledge-model.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawItem:
    """커넥터 출력 한 건. kind 별로 payload 형태가 다름."""
    source: str
    kind: str          # dag_code | dag_rest | table | index_meta | incident | manual
    key: str
    payload: dict[str, Any]
    origin: str        # 출처: path:line, API@time, 티켓 id


@dataclass
class Edge:
    src: str
    rel: str           # reads | writes | builds | alias_of | depends_on | owned_by | part_of | affected | mentions
    dst: str
    source: str = ""


@dataclass
class Document:
    id: str            # 엔티티 id 와 같음 (dag:feature_store_daily)
    type: str
    title: str
    path: str          # 트리 경로 (systems/reco/dags/feature_store_daily)
    system: str | None
    team: str | None
    markdown: str
    sources: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    status: str = "generated"
    gen: dict[str, Any] = field(default_factory=dict)   # 생성 내역: 템플릿, LLM 결과
