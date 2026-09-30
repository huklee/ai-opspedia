"""파이프라인: 수집 → 합성 → SQLite → 색인. 워커는 파일 락으로 하나만 (ADR-010 단순화).

잡 종류
- full-sync      : 전체 원천 수집 · 전체 페이지 재합성 (매일 06:00 + 수동)
- status-refresh : 상태 스냅샷(Airflow · 검색 엔진)만 다시 읽어 상태 페이지 갱신 (5분)
- metrics-hourly : 서비스 지표 시계열 수집 · 지표/카테고리 페이지 갱신 (매시)
단계마다 run_steps 에, 문서마다 synthesis_log 에 기록 → 관리자 화면의 실행 이력.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from filelock import FileLock

from . import connectors
from .config import Settings
from .model import RawItem
from .services import chunk_docs, embedder, keyword_index, llm, search_text
from .storage import SqliteRepository, now_iso, raw_hash
from .synthesis.build import Synthesizer

log = logging.getLogger("opspedia")

JOBS: dict[str, dict[str, Any]] = {
    "full-sync": {"schedule": "0 6 * * *", "title": "전체 동기화 · 합성", "icon": "🔄",
                  "desc": "모든 원천 수집 → 엔티티 · 엣지 재구성 → 전체 페이지 합성 → 색인",
                  "kinds": None, "targets": None},
    "status-refresh": {"schedule": "*/5 * * * *", "title": "상태 스냅샷 갱신", "icon": "📡",
                       "desc": "Airflow 실행 상태 · 검색 엔진 인덱스 상태만 다시 읽어 DAG · 인덱스 · alias · 시스템 페이지 갱신",
                       "kinds": {"airflow_fixture", "search_fixture"}, "targets": {"dag", "index", "alias", "system"}},
    "undo": {"schedule": "수동", "title": "되돌리기", "icon": "↩️", "desc": "문서 한 개의 최신 버전을 되돌리고 선택 시 원인 원자료를 차단",
             "kinds": None, "targets": set()},
    "metrics-hourly": {"schedule": "5 * * * *", "title": "서비스 지표 갱신", "icon": "📈",
                       "desc": "Prometheus · 웨어하우스 지표 수집 → SLO 판정 · 이상 탐지 → 지표 · 카테고리 페이지 갱신",
                       "kinds": {"metrics_fixture", "airflow_fixture", "search_fixture"}, "targets": {"metric", "category"}},
}


class _Clock:
    """실행 시각. at 이 주어지면(과거 이력 재현) 그 시각부터 실제 경과 시간을 더함."""

    def __init__(self, at: str | None):
        self.base = datetime.fromisoformat(at) if at else None
        self.t0 = time.perf_counter()

    def now(self) -> str:
        if self.base is None:
            return now_iso()
        return (self.base + timedelta(seconds=time.perf_counter() - self.t0)).isoformat(timespec="seconds")


def run(cfg: Settings, backends: tuple[str, ...] | None = None, job: str = "full-sync", trigger: str = "manual",
        actor: str | None = None, at: str | None = None, fail: set[str] | None = None, note: str = "") -> dict:
    with FileLock(str(cfg.data / "worker.lock"), timeout=30):
        return _run(cfg, backends, job, trigger, actor, at, fail or set(), note)


def _sections(md: str) -> dict[str, str]:
    out: dict[str, list[str]] = {"개요": []}
    cur = "개요"
    for line in md.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        else:
            out.setdefault(cur, []).append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _change(old: str | None, new: str) -> tuple[list[str], int, int]:
    """(바뀐 섹션, 추가 줄, 삭제 줄)."""
    if old is None:
        return ["신규"], new.count("\n") + 1, 0
    a, b = _sections(old), _sections(new)
    changed = [k for k in b if a.get(k) != b[k]] + [f"삭제: {k}" for k in a if k not in b]
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return changed, added, removed


def _from_snapshot(repo: SqliteRepository, source: str) -> list[RawItem]:
    return [RawItem(source, r["kind"], r["key"], r["payload"], r["origin"]) for r in repo.last_snapshot(source)]


def _run(cfg: Settings, backends, job: str, trigger: str, actor, at, fail: set[str], note: str) -> dict:
    spec = JOBS[job]
    clock = _Clock(at)
    started = clock.now()
    run_id = "run-" + datetime.fromisoformat(started).strftime("%Y%m%d-%H%M%S") + "-" + job.split("-")[0]
    repo = SqliteRepository(cfg.data / "opspedia.db")
    base, n = run_id, 1
    while repo.db.execute("SELECT 1 FROM runs WHERE id = ?", [run_id]).fetchone():  # 같은 초 실행 충돌 방지
        n += 1
        run_id = f"{base}-{n}"
    repo.start_run(run_id, job, trigger, actor, started, note)
    seq = [0]
    stats: dict[str, Any] = {"run_id": run_id, "job": job}
    status = "success"

    def log_step(stage, target, st, ms, stats_, msg, t_start=None):
        seq[0] += 1
        repo.add_step(run_id, seq[0], stage, target, st, t_start or clock.now(), ms, stats_, msg)

    def step(stage: str, target: str, fn, *, soft: bool = False):
        """단계 실행 + 기록. soft=True 면 실패해도 실행 계속 (status=partial)."""
        nonlocal status
        t_start, t0 = clock.now(), time.perf_counter()
        try:
            res, st, msg = fn()
            log_step(stage, target, "success", (time.perf_counter() - t0) * 1000, st, msg, t_start)
            return res
        except Exception as ex:
            log_step(stage, target, "failed", (time.perf_counter() - t0) * 1000, {}, f"{type(ex).__name__}: {ex}", t_start)
            if not soft:
                repo.finish_run(run_id, "failed", {**stats, "error": str(ex)}, clock.now())
                raise
            status = "partial"
            return None

    # 1) 수집: 커넥터마다 단계 기록. 부분 잡은 대상 외 원천을 마지막 스냅샷으로 사용
    items: list[RawItem] = []
    for src in cfg.sources:
        if spec["kinds"] and src["kind"] not in spec["kinds"]:
            items += _from_snapshot(repo, src["name"])
            continue

        def fetch(src=src):
            if src["name"] in fail:
                raise TimeoutError(f"{src['name']} 응답 없음 (30 s 초과)")
            got = list(connectors.build([src], cfg.path)[0].fetch())
            d = repo.snapshot_diff(src["name"], got, run_id, clock.now())
            return got, {"items": len(got), **d}, f"{src['kind']} · 새로 {d['new']} · 변경 {d['changed']} · 동일 {d['unchanged']}"
        got = step("fetch", src["name"], fetch, soft=True)
        if got is None:
            got = _from_snapshot(repo, src["name"])
            log_step("fallback", src["name"], "success", 0.1, {"items": len(got)}, f"마지막 정상 스냅샷 {len(got)}건 재사용")
        # 차단된 원자료(undo 시 지정) → 직전 원자료로 대체, 없으면 제외
        blocked = repo.blocked_hashes()
        if blocked:
            kept, notes = [], []
            for it in got:
                h = raw_hash(it.payload)
                if (it.source, it.key, h) in blocked:
                    bad = {b[2] for b in blocked if b[0] == it.source and b[1] == it.key}
                    prev = repo.previous_raw(it.source, it.key, bad)
                    if prev:
                        kept.append(RawItem(it.source, prev["kind"], it.key, prev["payload"], prev["origin"]))
                        notes.append(f"{it.key} #{h} → 직전 #{prev['hash']}")
                    else:
                        notes.append(f"{it.key} #{h} 제외")
                else:
                    kept.append(it)
            if notes:
                log_step("block", src["name"], "success", 0.1, {"blocked": len(notes)}, "차단 원자료 " + " · ".join(notes))
            got = kept
        items += got
    stats["raw_items"] = len(items)

    use_llm = spec["targets"] is None
    syn = Synthesizer(cfg, llm(cfg) if use_llm else None)

    def collect():
        ents, edges = syn.collect(items)
        return (ents, edges), {"entities": len(ents), "edges": len(edges)}, f"엔티티 {len(ents)} · 엣지 {len(edges)} · 레지스트리 이름 매칭(Aho–Corasick)"
    ents, edges = step("collect", "entities · edges", collect)

    def render():
        docs = syn.render(ents, edges, spec["targets"])
        calls = [d.gen["llm"] for d in docs if (d.gen.get("llm") or {}).get("status") not in (None, "off")]
        st = {"documents": len(docs), "llm_calls": len(calls),
              "llm_ok": sum(x["status"] == "ok" for x in calls), "llm_rejected": sum(x["status"] == "rejected" for x in calls)}
        stats.update(llm_calls=st["llm_calls"], llm_ok=st["llm_ok"], llm_rejected=st["llm_rejected"])
        msg = f"페이지 {len(docs)}개 결정적 렌더링"
        if calls:
            msg += f" · LLM 서술 {st['llm_calls']}건 (인용 통과 {st['llm_ok']} · 거절 {st['llm_rejected']})"
        return docs, st, msg
    docs = step("synthesize", "templates · llm", render)

    by_origin = {it.origin: it for it in items}
    by_key = {(it.source, it.key): it for it in items}

    def store():
        at_now = clock.now()
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        rows = []
        for d in docs:
            prev = repo.get_document(d.id)
            changed, added, removed = _change(prev["markdown"] if prev else None, d.markdown)
            if prev is None:
                note_ = "신규 생성"
            else:
                note_ = "섹션 " + ", ".join(changed[:3]) + (f" 외 {len(changed) - 3}" if len(changed) > 3 else "") + f" (+{added} −{removed})"
            action, version, _ = repo.upsert_document(d, run_id, at_now, note_)
            counts[action] += 1
            # 이 문서를 만든 원자료(내용 주소) = 출처 origin + 엔티티 추출 근거가 가리키는 원자료
            refs = {}
            for o in d.sources:
                if o in by_origin:
                    it = by_origin[o]
                    refs[(it.source, it.key)] = it
            for r in (d.gen.get("entities") or {}).get("records", []):
                k = (r["raw"]["source"], r["raw"]["key"])
                if k in by_key:
                    refs.setdefault(k, by_key[k])
            raw = [{"source": it.source, "key": it.key, "kind": it.kind, "origin": it.origin, "hash": raw_hash(it.payload)}
                   for it in refs.values()]
            rows.append({"raw": json.dumps(raw, ensure_ascii=False),
                         "timing": json.dumps(d.gen.get("timing") or {}, ensure_ascii=False),
                         "entities": json.dumps(d.gen.get("entities") or {}, ensure_ascii=False),
                         "run_id": run_id, "doc_id": d.id, "action": action, "version": version,
                         "template": f"{d.gen.get('template')}@{d.gen.get('template_hash')}",
                         "llm": json.dumps(d.gen.get("llm"), ensure_ascii=False),
                         "inputs": json.dumps(d.sources, ensure_ascii=False),
                         "input_hash": hashlib.sha256("|".join(d.sources).encode()).hexdigest()[:12],
                         "sections": json.dumps(changed if action != "unchanged" else [], ensure_ascii=False),
                         "added": added if action != "unchanged" else 0, "removed": removed if action != "unchanged" else 0})
        repo.add_synthesis(rows)
        gone: list[str] = []
        if spec["targets"] is None:
            if status == "success":
                gone = repo.delete_missing({d.id for d in docs})
            repo.replace_edges(edges)
        elif spec["targets"] & {"metric", "category"}:
            repo.replace_edges(edges, ("metric:", "category:"))
        counts["removed"] = len(gone)
        return counts, counts, f"생성 {counts['created']} · 갱신 {counts['updated']} · 동일 {counts['unchanged']}" + (f" · 삭제 {len(gone)}" if gone else "")
    stored = step("store", "sqlite", store)
    stats.update(stored)

    if stored["created"] + stored["updated"] + stored["removed"] > 0 or backends:
        all_docs = repo.all_documents()
        search_docs = [{"id": d["id"], "type": d["type"], "system": d["system"], "team": d["team"],
                        "path": d["path"], "title": d["title"], "body": search_text(d["markdown"])} for d in all_docs]
        for b in backends or (cfg.search["backend"],):
            def index(b=b):
                chunks = chunk_docs(search_docs)
                keyword_index(cfg, b).rebuild(chunks, cfg.user_dictionary, cfg.synonyms)
                return None, {"documents": len(search_docs), "chunks": len(chunks)}, f"{b} 재색인 문서 {len(search_docs)} · 섹션 청크 {len(chunks)} · nori 사용자 사전 {len(cfg.user_dictionary)}개"
            step("index", b, index)
        emb = embedder(cfg)
        if emb is not None:
            def embed():
                import numpy as np
                from langchain_text_splitters import MarkdownTextSplitter
                sp = MarkdownTextSplitter(chunk_size=800, chunk_overlap=80)
                ids, texts = [], []
                for d in search_docs:
                    for ch in sp.split_text(f"{d['title']}\n{d['body']}")[:6]:
                        ids.append(d["id"])
                        texts.append(ch)
                np.savez(cfg.data / "vectors.npz", ids=np.array(ids), m=emb.embed(texts))
                return None, {"chunks": len(texts)}, f"청크 {len(texts)}개 임베딩"
            step("embed", cfg.embedder["model"], embed)
    else:
        log_step("index", "skip", "success", 0, {}, "문서 변경 없음 → 재색인 생략")

    stats["status"] = status
    repo.finish_run(run_id, status, stats, clock.now())
    log.info("run %s %s", run_id, stats)
    return stats
