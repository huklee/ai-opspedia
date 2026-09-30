"""에이전트 도구: mcp Python SDK 2.x(MCPServer), stdio. REST 와 같은 서비스 함수를 호출 (ADR-015 PoC)."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import Settings
from .services import Services


def create(cfg: Settings) -> MCPServer:
    svc = Services(cfg)
    mcp = MCPServer("ai-opspedia")

    @mcp.tool()
    def search(query: str, k: int = 5, type: str | None = None) -> list[dict[str, Any]]:
        """운영 위키 하이브리드 검색(한국어 nori). type: dag | table | index | alias | incident | runbook"""
        return svc.retriever.search(query, k, {"type": type})

    @mcp.tool()
    def context(entity: str) -> dict[str, Any]:
        """장애 대응 컨텍스트 한 번에: 요약 · 상태 스냅샷 · 온콜 · 다운스트림 · 알려진 장애 · 런북.
        entity 예: dag:ranking_score_daily, index:products, table:reco.user_features"""
        return svc.context(entity) or {"error": f"unknown entity {entity}"}

    @mcp.tool()
    def impact(entity: str, hops: int = 3) -> dict[str, Any]:
        """영향 범위: N홉 다운스트림 · 업스트림."""
        g = svc.graph
        return {"downstream": [dict(x, **svc.brief(x["id"])) for x in g.downstream(entity, hops)],
                "upstream": [dict(x, **svc.brief(x["id"])) for x in g.upstream(entity, hops)]}

    return mcp
