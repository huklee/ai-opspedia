"""API · MCP · 평가가 공유하는 서비스 조립 (인터페이스 구현체 선택은 여기서만)."""
from __future__ import annotations

import re
from functools import cached_property
from typing import Any

from .adapters.graph_nx import NxGraph
from .mdutil import strip_frontmatter
from .config import Settings
from .retrieval import Retriever
from .storage import SqliteRepository


def search_text(markdown: str) -> str:
    _, body = strip_frontmatter(markdown)
    body = re.sub(r"```mermaid.*?```", " ", body, flags=re.S)
    body = re.sub(r"\[([^\]]*)\]\(/e/[^)]*\)", r"\1", body)
    return body


def chunk_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """섹션(## 헤딩) 단위 청크 (05-search-api: 청크 FTS → 문서 단위 묶기). id = 문서id#n."""
    out = []
    for d in docs:
        parts, cur, head = [], [], "개요"
        for line in d["body"].splitlines():
            if line.startswith("## "):
                parts.append((head, "\n".join(cur)))
                head, cur = line[3:].strip(), []
            else:
                cur.append(line)
        parts.append((head, "\n".join(cur)))
        for n, (h, text) in enumerate(p for p in parts if p[1].strip()):
            out.append({**d, "id": f"{d['id']}#{n}", "doc_id": d["id"], "section": h, "body": f"{h}\n{text}"})
    return out


def keyword_index(cfg: Settings, backend: str | None = None):
    backend = backend or cfg.search["backend"]
    if backend == "opensearch":
        from .adapters.opensearch import OpenSearchIndex
        return OpenSearchIndex(cfg.search["opensearch_url"], cfg.search["index"])
    from .adapters.sqlite_fts import SqliteFtsIndex
    if backend == "sqlite_fts":
        from .adapters.opensearch import NoriAnalyzer
        return SqliteFtsIndex(cfg.data / "fts_nori.db", NoriAnalyzer(cfg.search["opensearch_url"], cfg.user_dictionary))
    if backend == "sqlite_bigram":
        return SqliteFtsIndex(cfg.data / "fts_bigram.db", None)
    raise ValueError(backend)


def embedder(cfg: Settings):
    if cfg.embedder.get("provider") == "sentence-transformers":
        from .adapters.vectors import STEmbedder
        return STEmbedder(cfg.embedder["model"])
    return None


def llm(cfg: Settings, role: str = "llm"):
    """role: llm (합성) | ask (자연어 검색). provider: off | mock | ollama | openai. 결과는 입력 해시로 캐시."""
    spec = getattr(cfg, role, None) or cfg.llm
    p = spec.get("provider")
    if p == "mock":
        from .adapters.llm_mock import MockLLM
        return MockLLM()
    if p in ("ollama", "openai"):
        from .adapters.llm_cache import CachedLLM
        from .adapters.llm_openai import OpenAICompatLLM
        from .adapters.llm_route import RoutedLLM

        def make(sp: dict) -> CachedLLM:
            src = "로컬 Ollama" if sp.get("provider") == "ollama" else "사내 OpenAI 호환"
            return CachedLLM(OpenAICompatLLM(label=f"{sp['model']} · {src}", **{"temperature": 0.1, **sp}),
                             cfg.data / "llm_cache.db")
        return RoutedLLM({k: v for k, v in spec.items() if k != "fallback"}, spec.get("fallback"), make)
    return None


class Services:
    def __init__(self, cfg: Settings, backend: str | None = None):
        self.cfg = cfg
        self.backend = backend or cfg.search["backend"]
        self.repo = SqliteRepository(cfg.data / "opspedia.db")

    @cached_property
    def docs(self) -> dict[str, dict[str, Any]]:
        out = {}
        for d in self.repo.all_documents():
            d["body"] = search_text(d["markdown"])
            out[d["id"]] = d
        return out

    @cached_property
    def graph(self) -> NxGraph:
        return NxGraph(self.repo.edges())

    @cached_property
    def retriever(self) -> Retriever:
        vec, emb = None, None
        vpath = self.cfg.data / "vectors.npz"
        if vpath.exists() and self.cfg.embedder.get("provider") != "off":
            import numpy as np
            from .adapters.vectors import NumpyVectorIndex
            z = np.load(vpath, allow_pickle=False)
            vec = NumpyVectorIndex()
            vec.rebuild([str(x) for x in z["ids"]], z["m"])
            emb = embedder(self.cfg)
        return Retriever(keyword_index(self.cfg, self.backend), self.docs, self.cfg.synonyms, vec, emb)

    @cached_property
    def asker(self):
        from .ask import Asker
        return Asker(self, llm(self.cfg, "ask"))

    def url(self, entity: str) -> str | None:
        return f"/e/{entity}" if entity in self.docs else None

    def brief(self, i: str) -> dict[str, Any]:
        d = self.docs.get(i)
        if not d:
            return {"id": i}
        return {"id": i, "title": d["title"], "type": d["type"], "status": d["facts"].get("status_icon", ""),
                "url": f"/e/{i}"}

    def context(self, entity: str) -> dict[str, Any] | None:
        """ADR-015: 호출 한 번으로 장애 대응 컨텍스트."""
        d = self.docs.get(entity)
        if not d:
            return None
        f = d["facts"]
        team = self.cfg.team(d["team"])
        summary = re.search(r"<!-- gen:start summary -->\n(.*?)<!-- gen:end summary -->", d["markdown"], re.S)
        status: dict[str, Any] = {"icon": f.get("status_icon", "")}
        if d["type"] == "dag":
            runs = f.get("runs") or []
            status.update({"last_run": runs[0] if runs else None, "paused": f.get("paused"),
                           "snapshot_at": f.get("fetched_at")})
        if d["type"] == "index":
            status.update({"level": f["health"]["level"], "checks": f["health"]["checks"],
                           "alias_target": f["health"]["alias_target"], "newest": f["health"]["newest"],
                           "snapshot_at": f.get("fetched_at")})
        return {
            "entity": {"id": entity, "type": d["type"], "title": d["title"], "system": d["system"],
                       "url": self.url(entity), "permalink": f"/e/{entity}"},
            "summary": summary.group(1).strip() if summary else "",
            "status": status,
            "oncall": {"team": team.get("title"), "oncall": team.get("oncall"), "channel": team.get("channel")},
            "downstream": [dict(x, **self.brief(x["id"])) for x in f.get("downstream", [])],
            "upstream": [dict(x, **self.brief(x["id"])) for x in f.get("upstream", [])],
            "incidents": [self.brief(i) for i in f.get("incidents", [])],
            "runbooks": [self.brief(i) for i in f.get("runbooks", [])],
            "sources": d["sources"],
        }
