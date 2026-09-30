"""PoC 가설 H1 · H3 · H5 와 어댑터 경계 테스트. OpenSearch 없이 sqlite_bigram 백엔드로 실행."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from opspedia_poc.adapters.graph_nx import NxGraph
from opspedia_poc.adapters.sql_lineage import lineage
from opspedia_poc.config import load
from opspedia_poc.connectors import parse_dag_file
from opspedia_poc.model import Edge
from opspedia_poc.retrieval import expand, rrf
from opspedia_poc.synthesis.narrate import IncidentSummary, validate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg(tmp_path_factory):
    c = load(ROOT / "config.yaml")
    return c.model_copy(update={"data_dir": str(tmp_path_factory.mktemp("data")),
                                "search": {**c.search, "backend": "sqlite_bigram"},
                                "llm": {"provider": "mock"}, "ask": {"provider": "off"}})


@pytest.fixture(scope="session")
def built(cfg):
    from opspedia_poc.pipeline import run
    return run(cfg, backends=("sqlite_bigram",))


@pytest.fixture(scope="session")
def client(cfg, built):
    from opspedia_poc.api import create_app
    return TestClient(create_app(cfg), client=("127.0.0.1", 5000))


# ---------- 파서 (H1) ----------
def test_lineage_excludes_target_and_ctes():
    r, w = lineage("WITH x AS (SELECT * FROM a.b) INSERT OVERWRITE TABLE c.d SELECT * FROM x JOIN e.f ON 1=1")
    assert w == {"c.d"} and r == {"a.b", "e.f"}


def test_dag_parser_extracts_sensor_and_chain():
    info = parse_dag_file((ROOT / "dummy/dags/reco_feed_publish.py").read_text())
    assert info["dag_id"] == "reco_feed_publish" and info["schedule"] == "0 6 * * *"
    ops = {t["task_id"]: t for t in info["tasks"]}
    assert ops["wait_ranking"]["external_dag_id"] == "ranking_score_daily"
    assert ops["build_reco_feed_index"]["index_family"] == "reco-feed"
    assert ["wait_ranking", "build_reco_feed_index"] in info["deps"]


def test_every_entity_has_page(built, cfg):
    from opspedia_poc.services import Services
    s = Services(cfg)
    n_dags = len(list((ROOT / "dummy/dags").glob("*.py")))
    assert sum(d["type"] == "dag" for d in s.docs.values()) == n_dags
    assert {"index:products", "index:reco-feed", "index:query-suggest", "alias:products"} <= set(s.docs)
    assert built["created"] == len(s.docs)


def test_rerun_is_idempotent(cfg, built):
    from opspedia_poc.pipeline import run
    again = run(cfg)
    assert again["created"] == 0 and again["updated"] == 0


# ---------- 상태 점검 ----------
def test_products_health_flags_doc_drop_and_failed_swap(client):
    h = {x["id"]: x for x in client.get("/api/health/indices").json()}
    msgs = " ".join(c["msg"] for c in h["index:products"]["checks"])
    assert h["index:products"]["level"] == "crit"
    assert "30% 감소" in msgs and "product_index_alias_swap" in msgs
    assert any("일시정지" in c["msg"] for c in h["index:query-suggest"]["checks"])


# ---------- 그래프 ----------
def test_graph_flow_direction():
    g = NxGraph([Edge("dag:a", "writes", "table:t"), Edge("dag:b", "reads", "table:t"),
                 Edge("dag:b", "builds", "index:i"), Edge("alias:x", "alias_of", "index:i")])
    assert [d["id"] for d in g.downstream("dag:a", 5)] == ["table:t", "dag:b", "index:i", "alias:x"]
    assert [d["id"] for d in g.upstream("alias:x", 1)] == ["index:i"]


# ---------- 검색 ----------
def test_expand_and_rrf():
    assert "리코" in expand("추천 피드", [["추천", "리코", "reco"]])
    assert rrf([("a", 1), ("b", 1)], [("b", 1), ("c", 1)])[0][0] == "b"


def test_exact_identifier_ranks_first(client):
    r = client.get("/api/search", params={"q": "feature_store_daily"}).json()
    assert r["results"][0]["id"] == "dag:feature_store_daily"


def test_korean_query_with_particles(client):
    ids = [x["id"] for x in client.get("/api/search", params={"q": "추천 피드가 갱신되지 않음"}).json()["results"][:5]]
    assert "runbook:reco-feed-recovery" in ids  # 정답 런북이 상위 5위 안


# ---------- 드릴 시나리오 (H3 · H5) ----------
def test_drill1_failed_dag_context(client):
    """"ranking_score_daily 가 실패했다. 뭘 하는 DAG, 다운스트림, 온콜은?" → 호출 한 번."""
    c = client.get("/api/context/dag:ranking_score_daily").json()
    assert c["status"]["last_run"]["state"] == "failed"
    down = {d["id"] for d in c["downstream"]}
    assert {"dag:reco_feed_publish", "index:reco-feed", "alias:reco-feed"} <= down
    assert "reco-oncall" in c["oncall"]["oncall"]
    assert "incident:INC-2291" in {i["id"] for i in c["incidents"]}
    assert "runbook:reco-feed-recovery" in {r["id"] for r in c["runbooks"]}
    assert c["summary"]


def test_drill2_index_built_and_swapped(client):
    """"오늘 products 인덱스가 빌드되고 alias 전환까지 끝났나?" → 아니오 + 이유 + 런북."""
    c = client.get("/api/context/index:products").json()
    assert c["status"]["alias_target"] == "products_v41" and c["status"]["newest"] == "products_v42"
    assert "runbook:alias-swap-runbook" in {r["id"] for r in c["runbooks"]}


def test_permalink_renders_page(client):
    page = client.get("/e/dag:ranking_score_daily")
    assert page.status_code == 200 and "ranking_score_daily" in page.text and "다운스트림" in page.text


@pytest.mark.parametrize("url", ["/", "/search?q=추천 피드 미갱신", "/search?q=alias&tab=runbook", "/metrics", "/admin",
                                 "/e/index:products", "/e/metric:reco_ctr", "/e/category:reco-quality",
                                 "/e/runbook:reco-feed-recovery", "/e/incident:INC-2291", "/e/dag:feature_store_daily/history"])
def test_viewer_pages(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.text[:300]


def test_search_tabs_and_knowledge_panel(client):
    t = client.get("/search", params={"q": "ranking_score_daily"}).text
    assert 'class="gtabs"' in t and 'class="kpanel"' in t


def test_admin_run_detail(client, cfg):
    from opspedia_poc.storage import SqliteRepository
    rid = SqliteRepository(cfg.data / "opspedia.db").runs(1)[0]["id"]
    t = client.get(f"/admin/runs/{rid}").text
    assert "단계" in t and "wf-r" in t


# ---------- 지표 ----------
def test_metric_evaluation_breach_and_anomaly():
    from opspedia_poc.synthesis.metrics import evaluate
    vals = [[i, 4.1 + (i % 2) * 0.02] for i in range(13)] + [[13, 3.6]]
    ev = evaluate({"values": vals, "slo": {"op": ">=", "value": 3.8}, "unit": "%", "decimals": 2, "better": "higher"})
    assert ev["state"] == "breach" and ev["anomaly"] and ev["delta_good"] is False


def test_metrics_job_updates_only_metric_pages(cfg, built):
    from opspedia_poc.pipeline import run
    from opspedia_poc.storage import SqliteRepository
    st = run(cfg, job="metrics-hourly", trigger="schedule")
    r = SqliteRepository(cfg.data / "opspedia.db").run(st["run_id"])
    assert {x["doc_id"].split(":")[0] for x in r["synthesis"]} <= {"metric", "category"}


def test_connector_failure_falls_back_to_snapshot(cfg, built):
    from opspedia_poc.pipeline import run
    from opspedia_poc.storage import SqliteRepository
    st = run(cfg, fail={"reco-manuals"})
    repo = SqliteRepository(cfg.data / "opspedia.db")
    r = repo.run(st["run_id"])
    assert st["status"] == "partial"
    assert any(s["stage"] == "fallback" and s["target"] == "reco-manuals" for s in r["steps"])
    assert repo.get_document("runbook:reco-feed-recovery") is not None  # 스냅샷으로 유지


def test_seed_history_produces_versions(tmp_path):
    from opspedia_poc.history import seed
    c = load(ROOT / "config.yaml")
    c = c.model_copy(update={"data_dir": str(tmp_path), "search": {**c.search, "backend": "sqlite_bigram"},
                             "llm": {"provider": "mock"}, "ask": {"provider": "off"}})
    res = seed(c, ("sqlite_bigram",))
    from opspedia_poc.storage import SqliteRepository
    repo = SqliteRepository(tmp_path / "opspedia.db")
    assert len(res) >= 20 and any(r["status"] == "partial" for r in res)
    h = repo.history("table:reco.users")
    assert len(h) >= 2  # 09-24 segment 컬럼 추가
    inc = repo.synthesis_for("incident:INC-2330")
    assert inc and inc[-1]["llm"]["status"] == "rejected"
    assert repo.get_document("incident:INC-2341") is not None  # 09:41 웹훅 실행에서 생성


def test_non_tailnet_client_rejected(cfg, built):
    from opspedia_poc.api import create_app
    outside = TestClient(create_app(cfg), client=("203.0.113.5", 5000))
    assert outside.get("/healthz").status_code == 403


def test_mcp_context_tool(cfg, built):
    from opspedia_poc.mcp_server import create
    mcp = create(cfg)
    out = asyncio.run(mcp.call_tool("context", {"entity": "dag:ranking_score_daily"}))
    blob = json.dumps(out, ensure_ascii=False, default=str)
    assert "reco_feed_publish" in blob and "reco-oncall" in blob


# ---------- LLM 경계 (H4 준비) ----------
def test_citation_validator():
    assert validate(["원인은 OOM [S2]"], 3)
    assert not validate(["원인은 OOM"], 3)
    assert not validate(["원인은 OOM [S9]"], 3)


def test_openai_adapter_retries_on_invalid_json():
    from opspedia_poc.adapters.llm_openai import OpenAICompatLLM
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        content = "not json" if len(calls) == 1 else '{"bullets": [{"cites": [1], "text": "OOM 으로 실패"}]}'
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-oss-120b",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": content}}]})

    llm = OpenAICompatLLM("http://llm.test/v1", "gpt-oss-120b", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = llm.complete_json("sys", "user", IncidentSummary)
    assert out.bullets[0].cites == [1] and out.bullets[0].text == "OOM 으로 실패" and len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"


def test_hybrid_rrf_with_vectors(cfg, built):
    """벡터 경로(numpy + RRF)를 결정적 가짜 임베더로 검증. 실제 KURE-v1 은 embedder.provider 설정."""
    import hashlib

    import numpy as np

    from opspedia_poc.adapters.vectors import NumpyVectorIndex
    from opspedia_poc.retrieval import Retriever
    from opspedia_poc.services import Services, keyword_index

    class HashEmbedder:
        dim = 64

        def embed(self, texts):
            out = np.zeros((len(texts), self.dim), dtype=np.float32)
            for i, t in enumerate(texts):
                for w in t.split():
                    out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
            return out

    s = Services(cfg)
    ids = list(s.docs)
    emb = HashEmbedder()
    vec = NumpyVectorIndex()
    vec.rebuild(ids, emb.embed([s.docs[i]["title"] + " " + s.docs[i]["body"] for i in ids]))
    r = Retriever(keyword_index(cfg, "sqlite_bigram"), s.docs, cfg.synonyms, vec, emb)
    got = [x["id"] for x in r.search("랭킹 모델 재학습", 5)]
    assert "dag:ranking_train_weekly" in got


# ---------- 장애 원자료 파서 ----------
def test_incident_evidence_and_validation():
    from opspedia_poc.adapters.extract_ac import Extractor
    from opspedia_poc.synthesis import incident_parse as ip
    files = {p.name: p.read_text() for p in (ROOT / "dummy/incidents_raw/INC-2341").iterdir()}
    jira = json.loads((ROOT / "dummy/incidents/INC-2341.json").read_text())
    ev = ip.evidence(jira, files)
    assert {e["src"] for e in ev} == {"jira", "alert", "slack"} and all(e["id"].startswith("E") for e in ev)
    ex = Extractor({"ranking_score_daily": "dag:ranking_score_daily", "candidate_gen_hourly": "dag:candidate_gen_hourly"})
    det = ip.deterministic(jira, ev, ex)
    assert det["detected_at"].startswith("2026-09-29T04:00") and det["tta_min"] is not None
    assert "INC-2291" in det["incident_refs"] and "RB-RECO-002" in det["runbook_refs"]
    x = ip.IncidentExtract.model_validate({
        "summary": {"cites": ["E1"], "text": "추천 피드 미갱신"},
        "impact": {"cites": ["E99"], "text": "없는 근거"},
        "detection": {"cites": ["E1"], "text": "탐지 99분"},
        "root_cause": {"cites": ["E1"], "text": "ranking_score_daily 실패"},
        "timeline": [{"cites": ["E5"], "time": "01:23", "event": "시각 틀림"}], "actions": [], "follow_ups": []})
    kept, checks = ip.validate(x, ev, ex)
    st = {c["field"]: c["status"] for c in checks}
    assert st["summary"] == "ok" and st["impact"] == "dropped" and st["detection"] == "dropped"
    assert any(c["status"] == "fixed" for c in checks)  # 시각 보정


def test_synthesis_in_history(client):
    r = client.get("/e/incident:INC-2291/history")
    assert r.status_code == 200 and "근거 분해" in r.text and 'class="span"' in r.text
    old = client.get("/e/incident:INC-2291/synthesis", follow_redirects=False)
    assert old.status_code in (301, 307) and old.headers["location"].endswith("/history#synthesis")


def test_ask_rule_answer_without_llm(cfg, built):
    from opspedia_poc.services import Services
    r = Services(cfg).asker.ask("ranking_score_daily 담당이 누구야?")
    assert r["answer"]["status"] == "rule" and "추천 플랫폼팀" in r["answer"]["sentences"][0]


# ---------- 정적 변환 · 업로드 · 되돌리기 · 검증 ----------
def test_static_converters():
    from opspedia_poc.adapters.static_convert import convert
    samples = ROOT / "dummy/upload_samples"
    md, rep = convert("a.xml", (samples / "confluence-hive-partition.xml").read_text())
    assert md.startswith("# Hive 파티션 중복 정리 가이드") and "```sql" in md and "!!! warning" in md and "- [x]" in md
    assert any("gliffy" in w for w in rep["warnings"]) and rep["stats"]["tables"] == 1
    md, rep = convert("b.html", (samples / "airflow-scheduler-restart.html").read_text())
    assert "console.log" not in md and "| 중복 실행 | 0건 |" in md and rep["stats"]["removed"] >= 2
    md, rep = convert("c.txt", (samples / "es-reindex-guide.txt").read_text())
    assert "## 실행" in md and "POST _reindex" in md and "| 문서 ID | RB-SRCH-003 |" in md


@pytest.fixture
def iso(tmp_path):
    """격리된 데이터 · 업로드 폴더 (다른 테스트와 DB 공유 안 함)."""
    c = load(ROOT / "config.yaml")
    up = tmp_path / "uploads"
    up.mkdir()
    srcs = [{**s, "path": str(up)} if s["name"] == "uploads" else s for s in c.sources]
    return c.model_copy(update={"data_dir": str(tmp_path / "data"), "sources": srcs,
                                "search": {**c.search, "backend": "sqlite_bigram"},
                                "llm": {"provider": "mock"}, "ask": {"provider": "off"}})


def test_upload_commit_trace_and_delete(iso):
    from opspedia_poc.api import create_app
    from opspedia_poc.pipeline import run
    from opspedia_poc.storage import SqliteRepository
    run(iso, backends=("sqlite_bigram",))
    c = TestClient(create_app(iso), client=("127.0.0.1", 5000))
    h = {"X-Requested-With": "fetch"}
    data = (ROOT / "dummy/upload_samples/confluence-hive-partition.xml").read_bytes()
    assert c.post("/api/upload/commit", files={"file": ("x.xml", data)}).status_code == 403  # CSRF
    pv = c.post("/api/upload/preview", files={"file": ("hive.xml", data)}, headers=h).json()
    repo = SqliteRepository(iso.data / "opspedia.db")
    assert repo.get_document("runbook:hive") is None  # 미리보기는 DB 쓰기 없음
    r = c.post("/api/upload/commit", files={"file": ("hive.xml", data)}, headers=h).json()
    assert r["run"]["status"] == "success" and pv["doc_id"] == r["doc_id"] == "runbook:hive"
    log = repo.synthesis_for("runbook:hive")[0]
    sm = log["entities"]["summary"]
    assert sm["extracted"] >= 3 and sm["in_raw"] == sm["extracted"] and sm["links_none"] == 0
    assert log["raw"][0]["source"] == "uploads" and c.get("/e/runbook:hive/history").status_code == 200
    d = c.delete("/api/uploads/hive.xml", headers=h).json()
    assert d["purged"]["documents"] == 1
    assert repo.get_document("runbook:hive") is None
    assert repo.db.execute("SELECT count(*) FROM raw_versions WHERE source='uploads'").fetchone()[0] == 0


def test_undo_with_block_survives_reingest(iso):
    from opspedia_poc.pipeline import run
    from opspedia_poc.storage import SqliteRepository, now_iso
    up = next(iso.path(s["path"]) for s in iso.sources if s["name"] == "uploads")
    good = (ROOT / "dummy/upload_samples/es-reindex-guide.txt").read_text()
    (up / "guide.txt").write_text(good)
    run(iso, backends=("sqlite_bigram",))
    (up / "guide.txt").write_text(good.replace("±5%", "±50%"))  # 잘못된 수정본 업로드
    run(iso)
    repo = SqliteRepository(iso.data / "opspedia.db")
    assert "±50%" in repo.get_document("runbook:guide")["markdown"]
    cands = repo.undo_candidates("runbook:guide")
    assert any(c_["changed"] and c_["own"] for c_ in cands)
    u = repo.undo("runbook:guide", "tester", "잘못된 허용 오차", True, now_iso())
    assert u["action"] == "restored" and len(u["blocked"]) == 1
    assert "±5%" in repo.get_document("runbook:guide")["markdown"]
    st = run(iso)  # 같은 잘못된 파일이 그대로 있어도 차단 → 직전 원자료로 합성
    assert "±5%" in repo.get_document("runbook:guide")["markdown"]
    steps = repo.run(st["run_id"])["steps"]
    assert any(s["stage"] == "block" for s in steps)
