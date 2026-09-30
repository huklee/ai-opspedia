"""Repository: SQLite 단일 기준 원본 (ADR-020). sqlite-utils 어댑터.

문서 · 버전 · 엣지 외에 운영 기록(실행 · 단계 · 원자료 해시 · 합성 내역)도 같은 파일에 보관.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_utils

from .model import Document, Edge

KEEP_GENERATED_VERSIONS = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def raw_hash(payload: dict[str, Any]) -> str:
    """의미 기반 해시 (조회 시각 제외, ADR-013)."""
    semantic = json.dumps({k: v for k, v in payload.items() if k != "fetched_at"}, sort_keys=True, default=str)
    return hashlib.sha256(semantic.encode()).hexdigest()[:16]


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class SqliteRepository:
    def __init__(self, path: Path):
        # API 스레드풀에서도 사용 (서버는 읽기 전용, 쓰기는 파이프라인 워커 하나)
        self.db = sqlite_utils.Database(sqlite3.connect(path, check_same_thread=False))
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        db, names = self.db, set(self.db.table_names())
        if "documents" not in names:
            db["documents"].create({
                "id": str, "type": str, "title": str, "path": str, "system": str, "team": str,
                "status": str, "markdown": str, "content_hash": str, "facts": str, "sources": str,
                "gen": str, "version": int, "created_at": str, "updated_at": str,
            }, pk="id")
            db["documents"].create_index(["type"])
        if "document_versions" not in names:
            db["document_versions"].create({
                "doc_id": str, "version": int, "markdown": str, "content_hash": str,
                "run_id": str, "change_note": str, "created_at": str,
                "type": str, "title": str, "path": str, "system": str, "team": str,
                "facts": str, "sources": str, "gen": str,  # 되돌리기(undo)용 전체 스냅샷
            }, pk=("doc_id", "version"))
            db["document_versions"].create_index(["created_at"])
        if "edges" not in names:
            db["edges"].create({"src": str, "rel": str, "dst": str, "source": str})
            db["edges"].create_index(["src"])
            db["edges"].create_index(["dst"])
        if "runs" not in names:
            db["runs"].create({"id": str, "job": str, "trigger": str, "actor": str, "status": str,
                               "started_at": str, "finished_at": str, "stats": str, "note": str}, pk="id")
        if "run_steps" not in names:
            db["run_steps"].create({"run_id": str, "seq": int, "stage": str, "target": str, "status": str,
                                    "started_at": str, "ms": float, "stats": str, "message": str})
            db["run_steps"].create_index(["run_id"])
        if "raw_snapshot" not in names:
            db["raw_snapshot"].create({"source": str, "key": str, "kind": str, "hash": str, "origin": str,
                                       "payload": str, "run_id": str, "seen_at": str}, pk=("source", "key"))
        if "ingest_blocks" not in names:  # 잘못 들어온 원자료 차단 (undo 시 선택)
            db["ingest_blocks"].create({"id": int, "source": str, "key": str, "hash": str, "doc_id": str,
                                        "reason": str, "actor": str, "at": str, "active": int}, pk="id")
        if "raw_versions" not in names:  # 원자료 내용 주소 저장: 버전별 "원본 데이터" 조회용
            db["raw_versions"].create({"source": str, "key": str, "hash": str, "kind": str, "origin": str,
                                       "payload": str, "run_id": str, "seen_at": str}, pk=("source", "key", "hash"))
        if "synthesis_log" not in names:
            db["synthesis_log"].create({"run_id": str, "doc_id": str, "action": str, "version": int,
                                        "template": str, "llm": str, "inputs": str, "input_hash": str,
                                        "sections": str, "added": int, "removed": int, "raw": str, "timing": str,
                                        "entities": str})
            db["synthesis_log"].create_index(["run_id"])
            db["synthesis_log"].create_index(["doc_id"])

    # ---------- 문서 ----------
    def upsert_document(self, doc: Document, run_id: str, at: str | None = None,
                        note: str = "generated") -> tuple[str, int, str | None]:
        """(action, version, 이전 markdown). action = created | updated | unchanged."""
        at = at or now_iso()
        h = hashlib.sha256(doc.markdown.encode()).hexdigest()
        cur = self.get_document(doc.id)
        if cur and cur["content_hash"] == h:
            return "unchanged", cur["version"], cur["markdown"]
        version = (cur["version"] + 1) if cur else 1
        with self.db.conn:
            self.db["documents"].upsert({
                "id": doc.id, "type": doc.type, "title": doc.title, "path": doc.path,
                "system": doc.system, "team": doc.team, "status": doc.status, "markdown": doc.markdown,
                "content_hash": h, "facts": _j(doc.facts), "sources": _j(doc.sources), "gen": _j(doc.gen),
                "version": version, "created_at": cur["created_at"] if cur else at, "updated_at": at,
            }, pk="id")
            self.db["document_versions"].insert({
                "doc_id": doc.id, "version": version, "markdown": doc.markdown, "content_hash": h,
                "run_id": run_id, "change_note": note, "created_at": at,
                "type": doc.type, "title": doc.title, "path": doc.path, "system": doc.system, "team": doc.team,
                "facts": _j(doc.facts), "sources": _j(doc.sources), "gen": _j(doc.gen),
            })
            self.db.execute(
                "DELETE FROM document_versions WHERE doc_id = ? AND change_note != 'human' AND version <= ?",
                [doc.id, version - KEEP_GENERATED_VERSIONS])
        return ("updated" if cur else "created"), version, cur["markdown"] if cur else None

    def _row(self, r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        for k, d in (("facts", "{}"), ("sources", "[]"), ("gen", "{}")):
            r[k] = json.loads(r.get(k) or d)
        return r

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        rows = list(self.db["documents"].rows_where("id = ?", [doc_id]))
        return self._row(rows[0]) if rows else None

    def all_documents(self) -> list[dict[str, Any]]:
        return [self._row(r) for r in self.db["documents"].rows_where(order_by="id")]

    def delete_missing(self, keep: set[str], types: set[str] | None = None) -> list[str]:
        gone = [r["id"] for r in self.db["documents"].rows
                if r["id"] not in keep and (types is None or r["type"] in types)]
        for i in gone:
            self.db["documents"].delete(i)
        return gone

    def replace_edges(self, edges: list[Edge], only_src_prefixes: tuple[str, ...] | None = None) -> None:
        with self.db.conn:
            if only_src_prefixes:
                for p in only_src_prefixes:
                    self.db.execute("DELETE FROM edges WHERE src LIKE ?", [p + "%"])
                edges = [e for e in edges if e.src.startswith(only_src_prefixes)]
            else:
                self.db.execute("DELETE FROM edges")
            self.db["edges"].insert_all([e.__dict__ for e in edges])

    def edges(self) -> list[Edge]:
        return [Edge(**r) for r in self.db["edges"].rows]

    def history(self, doc_id: str) -> list[dict[str, Any]]:
        return list(self.db.query(
            "SELECT v.doc_id, v.version, v.content_hash, v.run_id, v.change_note, v.created_at, r.job, r.trigger "
            "FROM document_versions v LEFT JOIN runs r ON r.id = v.run_id WHERE v.doc_id = ? ORDER BY v.version DESC",
            [doc_id]))

    def version(self, doc_id: str, version: int) -> dict[str, Any] | None:
        rows = list(self.db["document_versions"].rows_where("doc_id = ? AND version = ?", [doc_id, version]))
        return rows[0] if rows else None

    def recent_updates(self, limit: int = 12, exclude_types: tuple[str, ...] = ("metric", "category")) -> list[dict[str, Any]]:
        """문서별 최신 변경 (지표처럼 매시 바뀌는 타입은 제외, 홈에서 별도 요약)."""
        ph = ",".join("?" * len(exclude_types))
        return list(self.db.query(
            "SELECT v.doc_id, v.version, v.change_note, v.created_at, v.run_id, d.title, d.type "
            "FROM document_versions v JOIN documents d ON d.id = v.doc_id "
            f"WHERE d.type NOT IN ({ph}) AND v.version = (SELECT max(version) FROM document_versions x WHERE x.doc_id = v.doc_id) "
            "ORDER BY v.created_at DESC, v.doc_id LIMIT ?", [*exclude_types, limit]))

    # ---------- 원자료 스냅샷 (변경 감지) ----------
    def snapshot_diff(self, source: str, items: list, run_id: str, at: str) -> dict[str, int]:
        """items: RawItem 목록. 새로 · 변경 · 동일 · 사라짐 개수 + 최신 스냅샷 저장(커넥터 장애 시 재사용)."""
        prev = {r["key"]: r["hash"] for r in self.db["raw_snapshot"].rows_where("source = ?", [source])}
        rows = []
        for it in items:
            payload = _j(it.payload)
            rows.append({"source": source, "key": it.key, "kind": it.kind,
                         "hash": raw_hash(it.payload), "origin": it.origin,
                         "payload": payload, "run_id": run_id, "seen_at": at})
        new = sum(r["key"] not in prev for r in rows)
        changed = sum(r["key"] in prev and prev[r["key"]] != r["hash"] for r in rows)
        keys = {r["key"] for r in rows}
        with self.db.conn:
            if rows:
                self.db["raw_snapshot"].upsert_all(rows, pk=("source", "key"))
                self.db["raw_versions"].insert_all(rows, pk=("source", "key", "hash"), ignore=True)
            for k in set(prev) - keys:
                self.db["raw_snapshot"].delete((source, k))
        return {"new": new, "changed": changed, "unchanged": len(rows) - new - changed, "gone": len(set(prev) - keys)}

    def last_snapshot(self, source: str) -> list[dict[str, Any]]:
        return [dict(r, payload=json.loads(r["payload"])) for r in self.db["raw_snapshot"].rows_where("source = ?", [source])]

    # ---------- 실행 기록 ----------
    def start_run(self, run_id: str, job: str, trigger: str, actor: str | None, at: str, note: str = "") -> None:
        self.db["runs"].upsert({"id": run_id, "job": job, "trigger": trigger, "actor": actor or "scheduler",
                                "status": "running", "started_at": at, "finished_at": None, "stats": "{}",
                                "note": note}, pk="id")

    def finish_run(self, run_id: str, status: str, stats: dict[str, Any], at: str) -> None:
        self.db["runs"].update(run_id, {"status": status, "finished_at": at, "stats": _j(stats)})

    def add_step(self, run_id: str, seq: int, stage: str, target: str, status: str, started_at: str,
                 ms: float, stats: dict[str, Any], message: str = "") -> None:
        self.db["run_steps"].insert({"run_id": run_id, "seq": seq, "stage": stage, "target": target, "status": status,
                                     "started_at": started_at, "ms": round(ms, 1), "stats": _j(stats), "message": message})

    def add_synthesis(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.db["synthesis_log"].insert_all(rows)

    def runs(self, limit: int = 50, job: str | None = None) -> list[dict[str, Any]]:
        where, args = ("WHERE job = ?", [job]) if job else ("", [])
        out = []
        for r in self.db.query(f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?", args + [limit]):
            r["stats"] = json.loads(r["stats"] or "{}")
            out.append(r)
        return out

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = list(self.db["runs"].rows_where("id = ?", [run_id]))
        if not rows:
            return None
        r = rows[0]
        r["stats"] = json.loads(r["stats"] or "{}")
        r["steps"] = [dict(s, stats=json.loads(s["stats"] or "{}"))
                      for s in self.db["run_steps"].rows_where("run_id = ?", [run_id], order_by="seq")]
        r["synthesis"] = [self._syn(s) for s in self.db["synthesis_log"].rows_where("run_id = ?", [run_id], order_by="doc_id")]
        return r

    @staticmethod
    def _syn(s: dict[str, Any]) -> dict[str, Any]:
        return dict(s, llm=json.loads(s["llm"] or "null"), inputs=json.loads(s["inputs"] or "[]"),
                    sections=json.loads(s["sections"] or "[]"), raw=json.loads(s.get("raw") or "[]"),
                    timing=json.loads(s.get("timing") or "{}"), entities=json.loads(s.get("entities") or "{}"))

    def raw_payloads(self, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in refs:
            rows = list(self.db["raw_versions"].rows_where("source = ? AND key = ? AND hash = ?", [r["source"], r["key"], r["hash"]]))
            if rows:
                out.append(dict(rows[0], payload=json.loads(rows[0]["payload"])))
        return out

    def synthesis_for(self, doc_id: str) -> list[dict[str, Any]]:
        return [self._syn(s) for s in self.db.query(
            "SELECT l.*, r.started_at, r.job, r.trigger FROM synthesis_log l JOIN runs r ON r.id = l.run_id "
            "WHERE l.doc_id = ? AND l.action != 'unchanged' ORDER BY r.started_at DESC", [doc_id])]

    def purge(self, doc_id: str, source: str | None = None, key: str | None = None) -> dict[str, int]:
        """문서와 그 흔적 전부 삭제 (데모 · 잘못 올린 업로드 정리): 문서 · 버전 · 합성 내역 · 엣지 · 원자료."""
        n: dict[str, int] = {}
        with self.db.conn:
            for t, where, args in (("documents", "id = ?", [doc_id]), ("document_versions", "doc_id = ?", [doc_id]),
                                   ("synthesis_log", "doc_id = ?", [doc_id]), ("edges", "src = ? OR dst = ?", [doc_id, doc_id])):
                n[t] = self.db.execute(f"DELETE FROM {t} WHERE {where}", args).rowcount
            if source and key:
                for t in ("raw_snapshot", "raw_versions"):
                    n[t] = self.db.execute(f"DELETE FROM {t} WHERE source = ? AND key = ?", [source, key]).rowcount
        return n

    # ---------- 되돌리기 · 차단 ----------
    def undo_candidates(self, doc_id: str) -> list[dict[str, Any]]:
        """최신 버전을 만든 원자료 중 직전 버전 대비 해시가 바뀐 것 = 변경의 원인 후보."""
        cur = self.get_document(doc_id)
        if not cur:
            return []
        logs = {x["version"]: x for x in self.synthesis_for(doc_id)}
        now_raw = (logs.get(cur["version"]) or {}).get("raw", [])
        prev_raw = {(r["source"], r["key"]): r["hash"] for r in (logs.get(cur["version"] - 1) or {}).get("raw", [])}
        return [{**r, "changed": prev_raw.get((r["source"], r["key"])) != r["hash"], "own": r["origin"] in cur["sources"]}
                for r in now_raw]

    def undo(self, doc_id: str, actor: str, reason: str, block: bool | list[dict[str, Any]], at: str) -> dict[str, Any]:
        """최신 버전을 되돌림: v>1 이면 직전 버전 내용을 새 버전으로 복원, v1 이면 문서 삭제(툼스톤).
        block=True 면 되돌린 버전을 만든 원자료(해시)를 차단 → 다음 수집에서 그 원자료 대신 직전 원자료 사용."""
        cur = self.get_document(doc_id)
        if not cur:
            raise ValueError(f"문서 없음: {doc_id}")
        v = cur["version"]
        run_id = f"undo-{datetime.fromisoformat(at).strftime('%Y%m%d-%H%M%S')}-{doc_id.split(':')[1][:20]}"
        blocked = []
        if block:
            cands = self.undo_candidates(doc_id)
            wanted = ({(b["source"], b["key"], b["hash"]) for b in block} if isinstance(block, list)
                      else {(r["source"], r["key"], r["hash"]) for r in cands if r["changed"] and r["own"]})
            for r in cands:
                if (r["source"], r["key"], r["hash"]) in wanted:
                    self.db["ingest_blocks"].insert({"source": r["source"], "key": r["key"], "hash": r["hash"], "doc_id": doc_id,
                                                     "reason": reason, "actor": actor, "at": at, "active": 1})
                    blocked.append(r)
        self.start_run(run_id, "undo", "undo", actor, at, f"{doc_id} v{v} 되돌리기 · {reason}")
        with self.db.conn:
            if v == 1:
                self.db["documents"].delete(doc_id)
                self.db.execute("DELETE FROM edges WHERE src = ?", [doc_id])
                action, note = "deleted", f"undo v1 → 문서 삭제 · {reason}"
                self.db["document_versions"].insert({"doc_id": doc_id, "version": v + 1, "markdown": "", "content_hash": "",
                                                     "run_id": run_id, "change_note": note, "created_at": at})
            else:
                prev = self.version(doc_id, v - 1)
                note = f"undo v{v} → v{v - 1} 내용 복원 · {reason}"
                self.db["documents"].update(doc_id, {
                    "markdown": prev["markdown"], "content_hash": prev["content_hash"], "version": v + 1, "updated_at": at,
                    **{k: prev[k] for k in ("facts", "sources", "gen", "title", "path", "system", "team") if prev.get(k) is not None}})
                self.db["document_versions"].insert({**{k: prev.get(k) for k in ("markdown", "content_hash", "type", "title", "path",
                                                                                  "system", "team", "facts", "sources", "gen")},
                                                     "doc_id": doc_id, "version": v + 1, "run_id": run_id,
                                                     "change_note": note, "created_at": at})
                action = "restored"
            self.db["synthesis_log"].insert({"run_id": run_id, "doc_id": doc_id, "action": "undo", "version": v + 1,
                                             "template": "", "llm": "null", "inputs": "[]", "input_hash": "",
                                             "sections": _j([note]), "added": 0, "removed": 0, "raw": _j(blocked),
                                             "timing": "{}", "entities": "{}"})
        self.finish_run(run_id, "success", {"undo": doc_id, "from": v, "action": action, "blocked": len(blocked)}, at)
        return {"doc_id": doc_id, "from_version": v, "new_version": v + 1, "action": action, "blocked": blocked, "run_id": run_id}

    def blocks(self, active_only: bool = True) -> list[dict[str, Any]]:
        return list(self.db["ingest_blocks"].rows_where("active = 1" if active_only else None, order_by="id desc"))

    def blocked_hashes(self) -> set[tuple[str, str, str]]:
        return {(b["source"], b["key"], b["hash"]) for b in self.blocks()}

    def unblock(self, block_id: int) -> None:
        self.db["ingest_blocks"].update(block_id, {"active": 0})

    def previous_raw(self, source: str, key: str, exclude: set[str]) -> dict[str, Any] | None:
        rows = [r for r in self.db["raw_versions"].rows_where("source = ? AND key = ?", [source, key], order_by="seen_at desc")
                if r["hash"] not in exclude]
        return dict(rows[0], payload=json.loads(rows[0]["payload"])) if rows else None

    def last_run(self) -> dict[str, Any] | None:
        rows = list(self.db.query("SELECT * FROM runs WHERE status != 'running' ORDER BY finished_at DESC, rowid DESC LIMIT 1"))
        return rows[0] if rows else None
