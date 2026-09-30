"""Retriever: 동의어 쿼리 확장 + 정확 일치 승격 + (키워드 ⊕ 벡터) RRF (ADR-005, ADR-019)."""
from __future__ import annotations

import re
from typing import Any

RRF_K = 60


def expand(query: str, synonyms: list[list[str]]) -> str:
    """운영 용어 동의어를 쿼리 뒤에 덧붙임 (OR 확장)."""
    low = query.lower()
    extra = []
    for group in synonyms:
        if any(s.lower() in low for s in group):
            extra += [s for s in group if s.lower() not in low]
    return query + (" " + " ".join(extra) if extra else "")


def rrf(*ranked: list[tuple[str, float]], k: int = RRF_K) -> list[tuple[str, float]]:
    score: dict[str, float] = {}
    for lst in ranked:
        for rank, (i, _) in enumerate(lst):
            score[i] = score.get(i, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score.items(), key=lambda x: -x[1])


def snippet(body: str, query: str, width: int = 90) -> str:
    text = re.sub(r"```.*?```", " ", body, flags=re.S)
    text = re.sub(r"^(!!!|\?\?\?) *\w+ *", "", text, flags=re.M)  # admonition 헤더
    text = re.sub(r"<!--.*?-->|[#>*`|!\[\]\"]|\(/e/[^)]*\)|-{3,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()
    terms = sorted({t for t in re.split(r"\s+", query.lower()) if len(t) >= 2}, key=len, reverse=True)
    pos = -1
    for t in terms:
        for cand in (t, t[:2]):
            pos = low.find(cand)
            if pos >= 0:
                break
        if pos >= 0:
            break
    start = max(0, pos - width // 3) if pos >= 0 else 0
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


class Retriever:
    def __init__(self, keyword, docs: dict[str, dict[str, Any]], synonyms: list[list[str]],
                 vectors=None, embedder=None):
        self.kw, self.docs, self.synonyms = keyword, docs, synonyms
        self.vectors, self.embedder = vectors, embedder
        self.exact = {}
        for d in docs.values():
            self.exact[d["id"].lower()] = d["id"]
            self.exact.setdefault(d["title"].lower(), d["id"])

    def search(self, query: str, k: int = 10, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        q = query.strip()
        if not q:
            return []
        kw = _group(self.kw.search(expand(q, self.synonyms), 100, filters))
        lists = [kw]
        if self.vectors is not None and self.embedder is not None:
            lists.append(self.vectors.search(self.embedder.embed([q])[0], 50))
        fused = rrf(*lists) if len(lists) > 1 else kw
        ids = [i for i, _ in fused if i in self.docs and _match(self.docs[i], filters)]
        hit = self.exact.get(q.lower())
        if hit and _match(self.docs[hit], filters):
            ids = [hit] + [i for i in ids if i != hit]
        out = []
        for i in ids[:k]:
            d = self.docs[i]
            out.append({"id": i, "title": d["title"], "type": d["type"], "system": d["system"],
                        "path": d["path"], "url": f"/e/{i}", "status": d["facts"].get("status_icon", ""),
                        "snippet": snippet(d["body"], q), "citation": d["sources"][:2]})
        return out


def _group(hits: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """청크 결과(문서id#n)를 문서 단위로 묶기: 문서의 최고 순위 청크 기준."""
    seen: dict[str, float] = {}
    for i, sc in hits:
        seen.setdefault(i.split("#", 1)[0], sc)
    return list(seen.items())


def _match(d: dict[str, Any], filters: dict[str, str] | None) -> bool:
    return all(d.get(k) == v for k, v in (filters or {}).items() if v)
