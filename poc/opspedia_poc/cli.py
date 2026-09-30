"""opspedia-poc CLI: run | serve | eval | mcp | context"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="opspedia-poc")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집 → 합성 → 저장 → 색인 → 사이트 빌드")
    r.add_argument("--all-backends", action="store_true", help="평가용 비교군 색인도 함께 생성")
    r.add_argument("--job", default="full-sync", choices=["full-sync", "status-refresh", "metrics-hourly"])
    s = sub.add_parser("serve", help="API + 위키 서버")
    s.add_argument("--http", action="store_true", help="TLS 없이 (로컬 개발용)")
    s.add_argument("--port", type=int)
    s.add_argument("--host", help="바인딩 주소 (기본: 설정 server.host)")
    sh = sub.add_parser("seed-history", help="지난 1주 실행 이력 재현 (DB 초기화 후 dummy/history.yaml 순서대로 실행)")
    sh.add_argument("--keep", action="store_true", help="기존 DB 유지")
    sub.add_parser("eval", help="한국어 검색 평가 (opensearch · sqlite_fts · sqlite_bigram)")
    sub.add_parser("eval-ask", help="자연어 질의 평가 (검색 3방식 비교 · 답변 품질)")
    sub.add_parser("mcp", help="MCP 도구 서버 (stdio)")
    c = sub.add_parser("context", help="엔티티 컨텍스트 JSON 출력")
    c.add_argument("entity")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("opensearch").setLevel(logging.WARNING)

    from .config import load
    cfg = load(a.config)
    if a.cmd == "run":
        from .pipeline import run
        backends = ("opensearch", "sqlite_fts", "sqlite_bigram") if a.all_backends else None
        print(json.dumps(run(cfg, backends, job=a.job, trigger="manual", actor="cli"), ensure_ascii=False))
    elif a.cmd == "seed-history":
        from .history import seed
        if not a.keep:
            for f in ("opspedia.db", "opspedia.db-wal", "opspedia.db-shm"):
                (cfg.data / f).unlink(missing_ok=True)
        res = seed(cfg, ("opensearch", "sqlite_fts", "sqlite_bigram"))
        for r in res:
            print(f"{r['run_id']:34s} {r['status']:8s} 생성 {r.get('created', 0):3d} · 갱신 {r.get('updated', 0):3d} · 동일 {r.get('unchanged', 0):3d}")
    elif a.cmd == "serve":
        import uvicorn
        from .api import create_app
        port = a.port or cfg.server["port"]
        kw = {}
        if not a.http:
            import os
            from pathlib import Path
            certs = Path(os.environ["OPSPEDIA_CERTS"]) if os.environ.get("OPSPEDIA_CERTS") else cfg.path(cfg.server["certs"])
            crt, key = certs / "tailscale.crt", certs / "tailscale.key"
            if not (crt.exists() and key.exists()):
                sys.exit(f"TLS 인증서 없음: {crt} · {key}\n  로컬 개발은 'serve --http', tailnet HTTPS 는 OPSPEDIA_CERTS 설정 (docs: README)")
            kw = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
        uvicorn.run(create_app(cfg), host=a.host or cfg.server["host"], port=port, workers=1, log_level="info", **kw)
    elif a.cmd == "eval":
        from .evaluate import evaluate
        rep = evaluate(cfg, ["opensearch", "sqlite_fts", "sqlite_bigram"])
        print(f"질의 {rep['queries']}개 (한국어 {rep['korean']}개) · 기준 recall@5 ≥ 0.85, MRR@10 ≥ 0.7, 무결과율 < 5 %")
        for b, m in rep["backends"].items():
            print(f"  {b:14s} recall@5 {m['recall@5']:.3f}  MRR@10 {m['mrr@10']:.3f}  무결과 {m['zero_rate']:.1%}  "
                  f"{'PASS' if m['pass'] else 'FAIL'}")
            for x in m["misses"]:
                print(f"      miss: {x['q']} → {x['got']} (정답 {x['want']})")
    elif a.cmd == "eval-ask":
        from .eval_ask import evaluate as ev
        rep = ev(cfg)
        print(f"자연어 질의 {rep['queries']}개 · 모델 {rep['model']}")
        print("  검색 recall@5: " + " · ".join(f"{k} {v:.3f}" for k, v in rep["recall@5"].items()))
        print(f"  답변 핵심어 적중 {rep['answer']['fact_hit']:.0%} · 상태 {rep['answer']['status']} · p50 {rep['answer']['p50_ms']} ms")
        for r in rep["rows"]:
            print(f"    {'✓' if r['fact_hit'] else '✗'} [{r['status']:8s}] {r['q']} → {(r['answer'] or [''])[0][:70]}")
    elif a.cmd == "mcp":
        from .mcp_server import create
        logging.getLogger().handlers[0].stream = sys.stderr
        create(cfg).run("stdio")
    elif a.cmd == "context":
        from .services import Services
        print(json.dumps(Services(cfg).context(a.entity), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
