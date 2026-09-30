"""비교군 B: SQLite FTS5 + nori(_analyze) 토큰 + 한글 바이그램 + 식별자 (본래 설계 ADR-005/019)."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

HANGUL = re.compile(r"[가-힣]+")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


def bigrams(text: str) -> list[str]:
    out = []
    for run in HANGUL.findall(text):
        out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return out


def idents(text: str) -> list[str]:
    out = []
    for w in IDENT.findall(text.lower()):
        out.append(w.replace(".", "_").replace("-", "_"))
        out += [p for p in re.split(r"[_.\-]", w) if p]
    return out


class SqliteFtsIndex:
    """analyzer=None 이면 바이그램 + 식별자만 (비교군 '바이그램 단독')."""

    WEIGHTS = (3.0, 1.0, 0.3, 2.0)  # title, body(nori), bigram, ident

    def __init__(self, path: Path, analyzer=None):
        self.path, self.analyzer = path, analyzer

    def _ko(self, text: str) -> str:
        return " ".join(self.analyzer.tokens(text)) if self.analyzer else ""

    def rebuild(self, docs: list[dict[str, Any]], user_words: list[str], synonyms: list[list[str]]) -> None:
        con = sqlite3.connect(self.path)
        con.execute("DROP TABLE IF EXISTS fts")
        con.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, title, body, bi, ident, "
                    "tokenize=\"unicode61 tokenchars '_'\")")
        rows = []
        for d in docs:
            text = f"{d['title']}\n{d['body']}"
            rows.append((d["id"], self._ko(d["title"]) + " " + " ".join(idents(d["title"])),
                         self._ko(d["body"]), " ".join(bigrams(text)), " ".join(idents(text))))
        con.executemany("INSERT INTO fts VALUES (?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def search(self, query: str, k: int = 50, filters: dict[str, str] | None = None) -> list[tuple[str, float]]:
        toks = set(self._ko(query).split()) | set(bigrams(query)) | set(idents(query))
        toks = {t for t in toks if t and '"' not in t}
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in sorted(toks))
        con = sqlite3.connect(self.path)
        w = ", ".join(str(x) for x in self.WEIGHTS)
        rows = con.execute(f"SELECT id, bm25(fts, 0, {w}) AS s FROM fts WHERE fts MATCH ? ORDER BY s LIMIT ?",
                           (match, k * 3)).fetchall()
        con.close()
        return [(i, -s) for i, s in rows]
