"""FastAPI: 검색 · 컨텍스트 · 그래프 API + MkDocs 정적 사이트 (tailnet 전용)."""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import time

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from .config import Settings
from .services import Services

TAILNET = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]


def _allowed(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return ip.is_loopback or any(ip in n for n in TAILNET)


def create_app(cfg: Settings, backend: str | None = None) -> FastAPI:
    app = FastAPI(title="ai-opspedia PoC API", version="0.1.0",
                  description="검색 · 컨텍스트 · 그래프 (docs/components/05-search-api.md 계약의 PoC 부분집합)")
    state = {"svc": Services(cfg, backend), "loaded": time.time()}
    token = os.environ.get(cfg.server.get("api_token_env", "OPSPEDIA_API_TOKEN"), "")

    def svc() -> Services:
        # 파이프라인 실행 후 새 데이터 반영: 마지막 실행 시각이 바뀌면 다시 로드
        s = state["svc"]
        last = s.repo.last_run()
        if last and (last["id"], last["finished_at"]) != state.get("run"):
            state["svc"], state["run"] = Services(cfg, backend), (last["id"], last["finished_at"])
        return state["svc"]

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _allowed(request.client.host if request.client else None):
            return JSONResponse({"detail": "tailnet only"}, status_code=403)
        p = request.url.path
        # 쓰기 요청은 같은 출처의 fetch 만 (CSRF 방지)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "fetch":
            return JSONResponse({"detail": "X-Requested-With: fetch 필요"}, status_code=403)
        # 에이전트용 엔드포인트는 토큰이 설정된 경우 Bearer 필요 (검색은 위키 화면용으로 개방)
        if token and p.startswith("/api/") and p != "/api/search":
            got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not secrets.compare_digest(got, token):
                return JSONResponse({"detail": "token required"}, status_code=401)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.get("/healthz")
    def healthz():
        s = svc()
        return {"ok": True, "documents": len(s.docs), "backend": s.backend, "last_run": s.repo.last_run()}

    @app.get("/api/search")
    def search(q: str = Query(..., max_length=200), k: int = Query(10, ge=1, le=50),
               type: str | None = None, system: str | None = None):
        s = svc()
        t0 = time.perf_counter()
        res = s.retriever.search(q, k, {"type": type, "system": system})
        return {"query": q, "backend": s.backend, "took_ms": round((time.perf_counter() - t0) * 1000, 1),
                "results": res}

    def actor(request: Request) -> str:
        return request.headers.get("tailscale-user-login") or f"web@{request.client.host if request.client else '?'}"

    @app.post("/api/upload/preview")
    async def upload_preview(file: UploadFile = File(...)):
        from .uploads import preview
        try:
            return preview(svc(), file.filename or "upload.txt", await file.read())
        except ValueError as ex:
            raise HTTPException(400, str(ex))

    @app.post("/api/upload/commit")
    async def upload_commit(request: Request, file: UploadFile = File(...), note: str = Form("")):
        from .uploads import commit
        try:
            return commit(cfg, file.filename or "upload.txt", await file.read(), actor(request), note)
        except ValueError as ex:
            raise HTTPException(400, str(ex))

    @app.get("/api/uploads")
    def uploads_list():
        from .uploads import listing
        return listing(cfg, svc())

    @app.delete("/api/uploads/{name}")
    def uploads_delete(request: Request, name: str):
        from .uploads import delete
        try:
            return delete(cfg, name, actor(request))
        except ValueError as ex:
            raise HTTPException(400, str(ex))

    @app.post("/api/pages/{entity:path}/undo")
    async def undo(request: Request, entity: str):
        """최신 버전 되돌리기. body: {reason, block: true | [{source, key, hash}]}"""
        from .storage import now_iso
        body = await request.json()
        try:
            r = svc().repo.undo(entity, actor(request), body.get("reason") or "사유 미기재", body.get("block", True), now_iso())
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        from .services import keyword_index, search_text
        docs = svc().repo.all_documents()
        from .services import chunk_docs
        keyword_index(cfg).rebuild(chunk_docs([{"id": d["id"], "type": d["type"], "system": d["system"], "team": d["team"],
                                                "path": d["path"], "title": d["title"], "body": search_text(d["markdown"])} for d in docs]),
                                   cfg.user_dictionary, cfg.synonyms)
        return r

    @app.get("/api/pages/{entity:path}/undo-candidates")
    def undo_candidates(entity: str):
        return svc().repo.undo_candidates(entity)

    @app.get("/api/blocks")
    def blocks():
        return svc().repo.blocks(active_only=False)

    @app.delete("/api/blocks/{block_id}")
    def unblock(block_id: int):
        svc().repo.unblock(block_id)
        return {"ok": True}

    @app.get("/api/ask")
    def ask(q: str = Query(..., max_length=300)):
        """자연어 질의 → 해석 · 검색 · 인용 답변 (로컬 Ollama, 본 구현 GPT-OSS-120B)."""
        return svc().asker.ask(q)

    @app.get("/api/context/{entity:path}")
    def context(entity: str):
        c = svc().context(entity)
        if c is None:
            raise HTTPException(404, f"unknown entity {entity}")
        return c

    @app.get("/api/graph/{entity:path}")
    def graph(entity: str, hops: int = Query(3, ge=1, le=6)):
        s = svc()
        if entity not in s.docs:
            raise HTTPException(404, f"unknown entity {entity}")
        g = s.graph
        return {"entity": entity, "downstream": [dict(x, **s.brief(x["id"])) for x in g.downstream(entity, hops)],
                "upstream": [dict(x, **s.brief(x["id"])) for x in g.upstream(entity, hops)],
                "mermaid": g.mermaid(entity, min(hops, 3), label=lambda i: s.docs[i]["title"] if i in s.docs else i)}

    @app.get("/api/pages/{entity:path}/history")
    def history(entity: str):
        return svc().repo.history(entity)

    @app.get("/api/pages/{entity:path}")
    def page(entity: str):
        d = svc().docs.get(entity)
        if not d:
            raise HTTPException(404, f"unknown entity {entity}")
        return {k: d[k] for k in ("id", "type", "title", "path", "system", "team", "status", "version",
                                  "updated_at", "sources", "markdown")}

    @app.get("/api/health/indices")
    def health_indices():
        s = svc()
        return [{"id": d["id"], "level": d["facts"]["health"]["level"], "checks": d["facts"]["health"]["checks"],
                 "alias_target": d["facts"]["health"]["alias_target"], "newest": d["facts"]["health"]["newest"]}
                for d in s.docs.values() if d["type"] == "index"]

    from .viewer import mount
    mount(app, svc)  # 위키 화면: /, /search, /e/<entity>, /metrics, /admin
    return app
