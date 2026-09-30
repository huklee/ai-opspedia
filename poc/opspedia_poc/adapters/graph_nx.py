"""GraphService: networkx 어댑터. 본 구현은 edges + 재귀 CTE (ADR-012)."""
from __future__ import annotations

from typing import Any

import networkx as nx

from ..model import Edge

# 데이터 흐름 방향: +1 = src→dst, -1 = dst→src. 나머지 관계는 흐름 아님
FLOW = {"writes": 1, "builds": 1, "swaps": 1, "reads": -1, "alias_of": -1, "depends_on": -1}
# 흐름 방향으로 읽히는 표시용 이름
FLOW_LABEL = {"writes": "writes", "builds": "builds", "swaps": "swaps", "reads": "read by",
              "alias_of": "served as", "depends_on": "triggers"}


class NxGraph:
    def __init__(self, edges: list[Edge]):
        self._edges = edges
        self.flow = nx.DiGraph()
        for e in edges:
            d = FLOW.get(e.rel)
            if d == 1:
                self.flow.add_edge(e.src, e.dst, rel=FLOW_LABEL[e.rel])
            elif d == -1:
                self.flow.add_edge(e.dst, e.src, rel=FLOW_LABEL[e.rel])

    def _walk(self, g: nx.DiGraph, entity: str, hops: int) -> list[dict[str, Any]]:
        if entity not in g:
            return []
        dist = nx.single_source_shortest_path_length(g, entity, cutoff=hops)
        paths = nx.single_source_shortest_path(g, entity, cutoff=hops)
        out = []
        for node, d in sorted(dist.items(), key=lambda x: (x[1], x[0])):
            if d == 0:
                continue
            p = paths[node]
            out.append({"id": node, "distance": d, "via": p[-2], "rel": g.edges[p[-2], node]["rel"]})
        return out

    def downstream(self, entity: str, hops: int = 3) -> list[dict[str, Any]]:
        return self._walk(self.flow, entity, hops)

    def upstream(self, entity: str, hops: int = 3) -> list[dict[str, Any]]:
        return self._walk(self.flow.reverse(copy=False), entity, hops)

    def neighbors(self, entity: str) -> list[Edge]:
        return [e for e in self._edges if e.src == entity or e.dst == entity]

    def mermaid(self, center: str, hops: int = 2, label=lambda i: i, up_hops: int = 1) -> str:
        nodes = {center} | {n["id"] for n in self.upstream(center, up_hops)} | {n["id"] for n in self.downstream(center, hops)}
        sub = self.flow.subgraph(nodes)
        ids = {n: f"n{i}" for i, n in enumerate(sorted(nodes))}
        lines = ["flowchart TD"]
        for n in sorted(nodes):
            lines.append(f'    {ids[n]}["{label(n)}"]')
        for a, b, d in sorted(sub.edges(data=True)):
            lines.append(f"    {ids[a]} -->|{d['rel']}| {ids[b]}")
        lines.append(f"    style {ids[center]} stroke-width:3px")
        return "\n".join(lines)
