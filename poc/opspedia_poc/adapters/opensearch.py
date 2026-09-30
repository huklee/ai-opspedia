"""OpenSearch 어댑터: nori 인덱스(SearchIndex) + _analyze(Analyzer). 운영 클러스터 아님, PoC 로컬 전용."""
from __future__ import annotations

from typing import Any

from opensearchpy import OpenSearch, helpers

NORI_TOKENIZER = {"type": "nori_tokenizer", "decompound_mode": "mixed"}
NORI_FILTERS = ["nori_part_of_speech", "nori_readingform", "lowercase"]


def client(url: str) -> OpenSearch:
    return OpenSearch(url, timeout=10)


class NoriAnalyzer:
    """ADR-019 방식: 인덱스 없이 _analyze 인라인 정의로 토큰화 + 캐시."""

    def __init__(self, url: str, user_words: list[str] = ()):
        self.os = client(url)
        self.tok = {**NORI_TOKENIZER, "user_dictionary_rules": list(user_words)}
        self.cache: dict[str, list[str]] = {}

    def tokens(self, text: str) -> list[str]:
        if text not in self.cache:
            r = self.os.indices.analyze(body={"tokenizer": self.tok, "filter": NORI_FILTERS, "text": text})
            self.cache[text] = [t["token"] for t in r["tokens"]]
        return self.cache[text]


class OpenSearchIndex:
    def __init__(self, url: str, index: str):
        self.os = client(url)
        self.index = index

    def rebuild(self, docs: list[dict[str, Any]], user_words: list[str], synonyms: list[list[str]]) -> None:
        settings = {
            "index": {"number_of_shards": 1, "number_of_replicas": 0},
            "analysis": {
                "tokenizer": {
                    "ko_tok": {**NORI_TOKENIZER, "user_dictionary_rules": list(user_words)},
                    "bigram_tok": {"type": "ngram", "min_gram": 2, "max_gram": 2, "token_chars": ["letter", "digit"]},
                    "ident_tok": {"type": "pattern", "pattern": "[^A-Za-z0-9_.\\-]+"},
                },
                "filter": {"ident_split": {"type": "word_delimiter_graph", "preserve_original": True,
                                           "split_on_numerics": False, "split_on_case_change": False}},
                "analyzer": {
                    "ko": {"type": "custom", "tokenizer": "ko_tok", "filter": NORI_FILTERS},
                    "bigram": {"type": "custom", "tokenizer": "bigram_tok", "filter": ["lowercase"]},
                    "ident": {"type": "custom", "tokenizer": "ident_tok", "filter": ["ident_split", "lowercase"]},
                },
            },
        }
        text = {"type": "text", "analyzer": "ko",
                "fields": {"bigram": {"type": "text", "analyzer": "bigram"},
                           "ident": {"type": "text", "analyzer": "ident"}}}
        mappings = {"properties": {
            "id": {"type": "keyword"}, "type": {"type": "keyword"}, "system": {"type": "keyword"},
            "team": {"type": "keyword"}, "path": {"type": "keyword"},
            "title": {**text, "fields": {**text["fields"], "raw": {"type": "keyword", "normalizer": "lc"}}},
            "body": text,
        }}
        settings["analysis"]["normalizer"] = {"lc": {"type": "custom", "filter": ["lowercase"]}}
        # 새 물리 인덱스 → alias 전환 (무중단 재색인)
        import time
        phys = f"{self.index}-{time.time_ns()}"
        self.os.indices.create(index=phys, body={"settings": settings, "mappings": mappings})
        helpers.bulk(self.os, ({"_index": phys, "_id": d["id"], **d} for d in docs), refresh=True)
        old = list(self.os.indices.get_alias(name=self.index, ignore_unavailable=True).keys()) \
            if self.os.indices.exists_alias(name=self.index) else []
        actions = [{"remove": {"index": o, "alias": self.index}} for o in old] + [{"add": {"index": phys, "alias": self.index}}]
        self.os.indices.update_aliases(body={"actions": actions})
        for o in old:
            self.os.indices.delete(index=o)

    def search(self, query: str, k: int = 50, filters: dict[str, str] | None = None) -> list[tuple[str, float]]:
        should = [
            {"multi_match": {"query": query, "type": "most_fields",
                             "fields": ["title^3", "body", "title.ident^3", "body.ident^1.5"]}},
            {"multi_match": {"query": query, "fields": ["title.bigram^0.6", "body.bigram^0.2"],
                             "minimum_should_match": "60%"}},
        ]
        flt = [{"term": {k2: v}} for k2, v in (filters or {}).items() if v]
        body = {"size": k, "_source": False,
                "query": {"bool": {"should": should, "minimum_should_match": 1, "filter": flt}}}
        r = self.os.search(index=self.index, body=body)
        return [(h["_id"], h["_score"]) for h in r["hits"]["hits"]]
