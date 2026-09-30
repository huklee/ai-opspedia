"""LLMClient 결과 캐시: (모델, 프롬프트, 스키마) 해시 → JSON (ADR-018 '입력 해시 결과 캐시')."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class CachedLLM:
    def __init__(self, inner, path: Path):
        self.inner, self.label = inner, getattr(inner, "label", "llm")
        self.path = path
        self.lock = threading.Lock()
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, model TEXT, out TEXT, ms REAL, at REAL)")
        self.last: dict[str, Any] = {}

    def complete_json(self, system: str, user: str, schema):
        k = hashlib.sha256(json.dumps([self.label, system, user, schema.__name__,
                                       schema.model_json_schema()], ensure_ascii=False).encode()).hexdigest()
        with sqlite3.connect(self.path) as c:
            row = c.execute("SELECT out, ms FROM cache WHERE k = ?", [k]).fetchone()
        if row:
            self.last = {"cached": True, "ms": row[1], "key": k[:12]}
            return schema.model_validate(json.loads(row[0]))
        t0 = time.perf_counter()
        out = self.inner.complete_json(system, user, schema)
        ms = (time.perf_counter() - t0) * 1000
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)", [k, self.label, out.model_dump_json(), ms, time.time()])
        self.last = {"cached": False, "ms": round(ms, 1), "key": k[:12]}
        return out
