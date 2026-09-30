"""합성: RawItem → 엔티티·엣지 → 결정적 Markdown 페이지 (ADR-008, ADR-018).

사실(스케줄, 태스크, 컬럼, 리니지, alias, 상태)은 파서·API 결과로만 렌더링.
요약은 템플릿 문장. LLM 서술은 설정이 켜진 경우 장애 문서에만 추가.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader

from ..adapters.extract_ac import Extractor
from ..adapters.graph_nx import NxGraph
from ..config import Settings
from ..model import Document, Edge, RawItem
from .health import check_family
from .metrics import evaluate, fmt
from .incident_parse import parse as parse_incident

TEMPLATES = Path(__file__).parent / "templates"
STATE_ICON = {"success": "✅", "failed": "❌", "upstream_failed": "⛔", "running": "⏳", None: "·"}
LEVEL_ICON = {"ok": "✅", "warn": "⚠️", "crit": "🔴"}
TYPE_DIR = {"dag": "dags", "table": "tables", "index": "indices", "alias": "aliases"}


def _hm(ts: str | None) -> str:
    return datetime.fromisoformat(ts).strftime("%m-%d %H:%M") if ts else "—"


def _dur(a: str | None, b: str | None) -> str:
    if not a or not b:
        return "—"
    m = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분"


class Synthesizer:
    def __init__(self, cfg: Settings, llm=None):
        self.cfg = cfg
        self.llm = llm
        self.env = Environment(loader=FileSystemLoader(TEMPLATES),
                               trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
        self.env.filters.update(s3short=lambda p: "s3:…/" + "/".join(str(p).rstrip("/").split("/")[-2:]), hm=_hm, icon=lambda s: STATE_ICON.get(s, "·"), level=lambda s: LEVEL_ICON.get(s, "·"))

    # ---------- 1단계: 엔티티·사실·엣지 ----------
    def collect(self, items: list[RawItem]) -> tuple[dict[str, dict[str, Any]], list[Edge]]:
        cfg = self.cfg
        ents: dict[str, dict[str, Any]] = {}
        edges: list[Edge] = []
        by_kind: dict[str, list[RawItem]] = defaultdict(list)
        for it in items:
            by_kind[it.kind].append(it)
        cfg_text = (cfg.root / "config.yaml").read_text(encoding="utf-8")
        self.evidence: dict[tuple[str, str, str], dict[str, Any]] = {}

        def record(e: Edge, method: str, it: RawItem | None, surface: str, locator: str = "", raw: str | None = None) -> None:
            """엣지 한 개의 추출 근거: 방법 · 원자료 · 위치 · 원문 발췌 · 원본에 실제로 있는지."""
            text = raw if raw is not None else (raw_text(it) if it else cfg_text)
            i = text.lower().find(surface.lower()) if surface else -1
            line = text.count("\n", 0, i) + 1 if i >= 0 else None
            snip = text[max(0, i - 60): i + len(surface) + 60].replace("\n", " ⏎ ") if i >= 0 else ""
            self.evidence.setdefault((e.src, e.rel, e.dst), {
                "src": e.src, "rel": e.rel, "dst": e.dst, "method": method,
                "raw": ({"source": it.source, "key": it.key, "kind": it.kind, "origin": it.origin} if it else
                        {"source": "config", "key": "config.yaml", "kind": "config", "origin": "config.yaml"}),
                "locator": locator or (f"{(it.key if it else 'config.yaml')}:{line}" if line else ""),
                "surface": surface, "snippet": snip, "in_raw": i >= 0})

        def add(e: Edge, method: str, it: RawItem | None, surface: str, locator: str = "", raw: str | None = None) -> None:
            edges.append(e)
            record(e, method, it, surface, locator, raw)

        for s in cfg.systems:
            ents[f"system:{s['id']}"] = {"type": "system", "title": s["title"], "system": s["id"], "team": s["team"],
                                         "facts": s, "sources": ["config.yaml:systems"]}
            add(Edge(f"system:{s['id']}", "owned_by", f"team:{s['team']}", "config"), "설정 systems.team", None, f"team: {s['team']}")
        for t in cfg.teams:
            ents[f"team:{t['id']}"] = {"type": "team", "title": t["title"], "system": None, "team": t["id"],
                                       "facts": t, "sources": ["config.yaml:teams"]}

        rest = {it.key: it for it in by_kind["dag_rest"]}
        for it in by_kind["dag_code"]:
            d = it.payload
            dag_id = d["dag_id"]
            r = rest.get(dag_id)
            system = cfg.dag_systems.get(dag_id) or next((t for t in d["tags"] if cfg.system(t)), None)
            facts = {**d, "rest": r.payload if r else None, "system": system}
            srcs = [it.origin] + ([r.origin] if r else [])
            ents[f"dag:{dag_id}"] = {"type": "dag", "title": dag_id, "system": system, "team": d.get("owner"),
                                     "facts": facts, "sources": srcs}
            csv = d.get("source_kind") == "ops_csv"  # 운영 메타 CSV: 담당은 owner_team 열, 시스템은 sources[].system 설정
            if d.get("owner"):
                add(Edge(f"dag:{dag_id}", "owned_by", f"team:{d['owner']}", it.origin),
                    "wf_task_master · owner_team 열" if csv else "ast · DAG default_args.owner", it, d["owner"] if csv else f'"owner": "{d["owner"]}"')
            if system and csv and not cfg.dag_systems.get(dag_id):
                add(Edge(f"dag:{dag_id}", "part_of", f"system:{system}", it.origin), f"설정 sources[{it.source}].system", None, f"system: {system}")
            elif system:
                add(Edge(f"dag:{dag_id}", "part_of", f"system:{system}", it.origin),
                    "설정 dag_systems" if cfg.dag_systems.get(dag_id) else "ast · DAG tags", None if cfg.dag_systems.get(dag_id) else it,
                    f"{dag_id}: {system}" if cfg.dag_systems.get(dag_id) else f'"{system}"')
            for t in d["tasks"]:
                src = f"{it.origin.split('@')[0]}:{t['line']}"
                tail = "\n".join(d["raw"].splitlines()[t["line"] - 1:])  # 태스크 정의부터 검색
                mapped = t.get("lineage") == "task_data_mapping"
                raw_code = d.get("raw") or ""

                def surf(name: str) -> str:  # 원문에 전체 이름이 없으면(CSV 는 db,table 분리) 테이블 이름으로 대조
                    return name if name.lower() in raw_code.lower() else name.rsplit(".", 1)[-1]
                for tb in t.get("reads", []):
                    add(Edge(f"dag:{dag_id}", "reads", f"table:{tb}", src),
                        f"task_data_mapping · {t['task_id']} input" if mapped else f"sqlglot 리니지 · {t['task_id']} SQL FROM/JOIN", it, surf(tb))
                for tb in t.get("writes", []):
                    add(Edge(f"dag:{dag_id}", "writes", f"table:{tb}", src),
                        f"task_data_mapping · {t['task_id']} output" if mapped else f"sqlglot 리니지 · {t['task_id']} INSERT 대상", it, surf(tb))
                for up in t.get("upstream_dags", []):
                    add(Edge(f"dag:{dag_id}", "depends_on", f"dag:{up['dag']}", src),
                        f"task_data_mapping · {t['task_id']} 가 {up['dag']} 산출 S3 경로를 입력", it, up["via"])
                if t.get("external_dag_id"):
                    add(Edge(f"dag:{dag_id}", "depends_on", f"dag:{t['external_dag_id']}", src), f"ast · ExternalTaskSensor({t['task_id']})", it, t["external_dag_id"])
                if t.get("operator") == "AliasSwapOperator" and t.get("alias"):
                    add(Edge(f"dag:{dag_id}", "swaps", f"alias:{t['alias']}", src), f"ast · AliasSwapOperator({t['task_id']})", it, f'alias="{t["alias"]}"')
                if t.get("operator") == "IndexBuildOperator" and t.get("index_family"):
                    add(Edge(f"dag:{dag_id}", "builds", f"index:{t['index_family']}", src), f"ast · IndexBuildOperator({t['task_id']})", it, f'index_family="{t["index_family"]}"')
                    if t.get("source_table"):
                        add(Edge(f"dag:{dag_id}", "reads", f"table:{t['source_table']}", src), f"ast · IndexBuildOperator.source_table", it, t["source_table"])
                del tail
        edges = _dedupe(edges)

        for it in by_kind["table"]:
            t = it.payload
            system = cfg.schema_systems.get(t["name"].split(".")[0])
            ents[f"table:{t['name']}"] = {"type": "table", "title": t["name"], "system": system,
                                          "team": cfg.system(system).get("team"), "facts": t, "sources": [it.origin]}
            if system:
                add(Edge(f"table:{t['name']}", "part_of", f"system:{system}", it.origin), "규칙 · 스키마 접두사 (schema_systems)", it, t["name"].split(".")[0])

        for it in by_kind["index_meta"]:
            meta = it.payload
            for fam in cfg.index_families:
                builder = rest.get(fam["builder_dag"])
                swappers = [rest[e.src[4:]].payload for e in edges
                            if e.rel == "swaps" and e.dst == f"alias:{fam['alias']}" and e.src[4:] in rest]
                h = check_family(fam, meta, builder.payload if builder else None, cfg.now, swappers)
                eid = f"index:{fam['id']}"
                ents[eid] = {"type": "index", "title": fam["id"], "system": fam["system"],
                             "team": cfg.system(fam["system"]).get("team"),
                             "facts": {**fam, "health": h, "cluster": meta["cluster"], "engine": meta["engine"],
                                       "fetched_at": meta["fetched_at"], "cluster_health": meta["cluster_health"]},
                             "sources": [it.origin, "config.yaml:index_families"]}
                aid = f"alias:{fam['alias']}"
                ents[aid] = {"type": "alias", "title": f"{fam['alias']} alias", "system": fam["system"],
                             "team": ents[eid]["team"],
                             "facts": {"alias": fam["alias"], "family": fam["id"], "target": h["alias_target"],
                                       "newest": h["newest"], "fetched_at": meta["fetched_at"], "level": h["level"]},
                             "sources": [f"{meta['cluster']}:_alias/{fam['alias']} → {h['alias_target']}"]}
                add(Edge(aid, "alias_of", eid, it.origin), "검색 엔진 API · _alias", it, f'"{fam["alias"]}": "{h["alias_target"]}"')
                add(Edge(eid, "part_of", f"system:{fam['system']}", "config"), "설정 index_families", None, f"id: {fam['id']}")

        # 장애·매뉴얼 → 레지스트리 이름 매칭으로 엔티티 연결
        names = {e["title"]: eid for eid, e in ents.items() if e["type"] in ("dag", "table", "index")}
        ex = Extractor(names)
        for it in by_kind["manual"]:
            md = it.payload["markdown"]
            title = next((ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")), it.key)
            spans_ = ex.spans(md)
            hit = [h for h, *_ in spans_]
            systems = [ents[h]["system"] for h in hit if ents[h]["system"]]
            system = max(set(systems), key=systems.count) if systems else None
            ents[f"runbook:{it.key}"] = {"type": "runbook", "title": title, "system": system,
                                         "team": cfg.system(system).get("team"),
                                         "facts": {**it.payload, "mentions": hit}, "sources": [it.origin]}
            for h, a, b, surf in spans_:
                add(Edge(f"runbook:{it.key}", "mentions", h, it.origin), "레지스트리 이름 매칭 (Aho–Corasick)", it, surf, raw=md)

        # 장애: 원자료 묶음(알림 · 슬랙 · 포스트모템)이 있으면 6단계 파서, 없으면 Jira 필드 + 이름 매칭
        bundles = {it.key: it for it in by_kind["incident_bundle"]}
        rb_ids = {}
        for eid, e in ents.items():
            if e["type"] == "runbook":
                m = re.search(r"문서 ID\W+([A-Z]+-[A-Z]+-\d+)", e["facts"]["markdown"])
                if m:
                    rb_ids[m.group(1)] = eid
        rb_texts = {eid: e["facts"]["markdown"] for eid, e in ents.items() if e["type"] == "runbook"}
        rb_mentions: dict[str, list[str]] = defaultdict(list)
        for ed in edges:
            if ed.rel == "mentions":
                rb_mentions[ed.dst].append(ed.src)
        # 엔티티는 아니지만 알려진 식별자: 태스크 id · 컬럼 · 운영 용어집
        known = set(cfg.known_terms)
        for e in ents.values():
            known |= {t["task_id"] for t in e["facts"].get("tasks", [])} if e["type"] == "dag" else set()
            known |= {c["name"] for c in e["facts"].get("columns", [])} if e["type"] == "table" else set()
        simple: dict[str, dict[str, Any]] = {}
        for it in by_kind["incident"]:
            f = it.payload["fields"]
            text = " ".join([f["summary"], f.get("description") or "", f.get("customfield_root_cause") or "",
                             f.get("customfield_resolution") or "", " ".join(f.get("labels", []))]
                            + [c["body"] for c in f["comment"]["comments"]])
            simple[it.key] = {"affected": ex.find(text), "spans": ex.spans(text), "text": text}
        for it in by_kind["incident"]:
            f = it.payload["fields"]
            system = next((l for l in f.get("labels", []) if cfg.system(l)), None)
            eid = f"incident:{it.key}"
            srcs = [it.origin]
            synthesis = None
            hit = simple[it.key]["affected"]
            if it.key in bundles:
                b = bundles[it.key]
                srcs.append(b.origin)
                synthesis = parse_incident(it.key, f"{it.key} {f['summary']}", it.payload, b.payload["files"], ex,
                                           self.llm, rb_ids, simple, rb_texts, rb_mentions, known)
                hit = synthesis["knowledge"]["affected"]
                evmap = {x["id"]: x for x in synthesis["evidence"]}
                for s_ in synthesis["knowledge"]["similar"]:
                    add(Edge(eid, "similar_to", s_["id"], b.origin),
                        "근거에 장애 키 직접 언급" if s_["referenced"] else f"영향 엔티티 {len(s_['overlap'])}개 겹침", b,
                        s_["id"].split(":")[1] if s_["referenced"] else "")
                for r in synthesis["knowledge"]["runbooks"]:
                    add(Edge(eid, "mitigated_by", r["id"], b.origin), "근거에 런북 문서 ID 언급", b, r["ref"])
            ents[eid] = {"type": "incident", "title": f"{it.key} {f['summary']}", "system": system,
                         "team": cfg.system(system).get("team"),
                         "facts": {**it.payload, "affected": hit, "synthesis": synthesis}, "sources": srcs}
            for h in hit:
                e_ = Edge(eid, "affected", h, srcs[-1])
                if synthesis:
                    cites = synthesis["deterministic"]["mentions"].get(h, [])
                    span = next((x for x in synthesis["evidence"] if x["id"] in cites), None)
                    surf = ents[h]["title"] if h in ents else h
                    add(e_, f"레지스트리 매칭 · 근거 조각 {', '.join(cites[:4])}", bundles[it.key] if span and span["src"] != "jira" else it,
                        surf, f"{span['id']} {span['src']}" if span else "", raw=(span["text"] if span else None))
                else:
                    surf = next((x[3] for x in simple[it.key]["spans"] if x[0] == h), h)
                    add(e_, "레지스트리 이름 매칭 (Aho–Corasick) · Jira 필드", it, surf, raw=simple[it.key]["text"])
        # 서비스 지표 · 카테고리 (지표 파이프라인)
        cat_metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for it in by_kind["metric"]:
            m = it.payload
            ev = evaluate(m)
            cat_metrics[m["category"]].append({"id": m["id"], "title": m["title"], "eval": ev})
            eid = f"metric:{m['id']}"
            cat = next((c.payload for c in by_kind["metric_category"] if c.key == m["category"]), {})
            ents[eid] = {"type": "metric", "title": m["title"], "system": None, "team": cat.get("team"),
                         "facts": {**{k: v for k, v in m.items() if k != "values"}, "eval": ev}, "sources": [it.origin, "catalog.yaml"]}
            add(Edge(eid, "part_of", f"category:{m['category']}", it.origin), "지표 카탈로그 category", it, m["category"])
            for p in m.get("producer", []):
                add(Edge(eid, "measures", p, "catalog.yaml"), "지표 카탈로그 producer", it, p)
        for it in by_kind["metric_category"]:
            c = it.payload
            ms = sorted(cat_metrics[c["id"]], key=lambda x: {"breach": 0, "nodata": 1, "near": 2, "ok": 3}[x["eval"]["state"]])
            ents[f"category:{c['id']}"] = {"type": "category", "title": c["title"], "system": None, "team": c.get("team"),
                                           "facts": {**c, "metrics": ms,
                                                     "breaches": sum(x["eval"]["state"] in ("breach", "nodata") for x in ms)},
                                           "sources": [it.origin]}

        # 참조만 있고 정의가 없는 테이블(DDL 누락)은 스텁 엔티티로
        for e in list(edges):
            for x in (e.src, e.dst):
                if x not in ents and x.startswith("table:"):
                    ents[x] = {"type": "table", "title": x[6:], "system": cfg.schema_systems.get(x[6:].split(".")[0]),
                               "team": None, "facts": {"name": x[6:], "comment": "", "columns": [], "partitions": [],
                                                       "stub": True}, "sources": [e.source]}
        return ents, _dedupe(edges)

    # ---------- 2단계: 페이지 렌더링 ----------
    def path_of(self, eid: str, e: dict[str, Any]) -> str:
        kind, name = eid.split(":", 1)
        slug = name.replace(".", "_") if kind == "table" else name
        if kind == "system":
            return f"systems/{name}/index"
        if kind in TYPE_DIR:
            return f"systems/{e['system'] or 'unassigned'}/{TYPE_DIR[kind]}/{slug}"
        if kind == "metric":
            return f"metrics/{e['facts']['category']}/{slug}"
        if kind == "category":
            return f"metrics/{slug}/index"
        return {"team": "teams", "incident": "incidents", "runbook": "runbooks"}[kind] + f"/{slug}"

    def render(self, ents: dict[str, dict[str, Any]], edges: list[Edge], targets: set[str] | None = None) -> list[Document]:
        """targets 가 있으면 해당 타입만 렌더링 (예: 지표 파이프라인은 metric · category 만)."""
        g = NxGraph(edges)
        out_e: dict[str, list[Edge]] = defaultdict(list)
        in_e: dict[str, list[Edge]] = defaultdict(list)
        for e in edges:
            out_e[e.src].append(e)
            in_e[e.dst].append(e)

        def title(i: str) -> str:
            return ents[i]["title"] if i in ents else i

        def link(i: str) -> str:
            return f"[{title(i)}](/e/{i})" if i in ents else f"`{i}`"

        def status_of(i: str) -> str:
            e = ents.get(i)
            if not e:
                return ""
            if e["type"] == "dag":
                runs = (e["facts"].get("rest") or {}).get("runs") or [{}]
                return STATE_ICON.get(runs[0].get("state"), "·")
            if e["type"] in ("index", "alias"):
                lvl = e["facts"]["health"]["level"] if e["type"] == "index" else e["facts"]["level"]
                return LEVEL_ICON[lvl]
            if e["type"] == "metric":
                return e["facts"]["eval"]["icon"]
            if e["type"] == "category":
                return "🔴" if e["facts"]["breaches"] else "✅"
            return ""

        def incidents_for(i: str) -> list[str]:
            return sorted({x.src for x in in_e[i] if x.rel == "affected"}, reverse=True)

        def runbooks_for(ids: list[str]) -> list[str]:
            return sorted({x.src for i in ids for x in in_e[i] if x.rel == "mentions"})

        self.env.filters["ename"] = title
        self._titles = {k: v["title"] for k, v in ents.items()}
        helpers = dict(link=link, title=title, status_of=status_of, cfg=self.cfg, now=self.cfg.now,
                       out_e=out_e, in_e=in_e, dur=_dur, fmt=fmt,
                       mermaid=lambda c, h=2: g.mermaid(c, h, up_hops=2 if ents[c]["type"] == "index" else 1, label=lambda i: f"{status_of(i)} {title(i)}".strip()))
        docs = []
        for eid, e in sorted(ents.items()):
            if targets and e["type"] not in targets:
                continue
            t_doc = time.perf_counter()
            down = g.downstream(eid, 3)
            up = g.upstream(eid, 3)
            related = [eid] + [d["id"] for d in up if d["distance"] == 1]
            if e["type"] == "metric":
                related = e["facts"].get("producer", [])
            gen: dict[str, Any] = {"template": f"{e['type']}.md.j2", "template_hash": self._thash(e["type"]),
                                   "renderer": "deterministic", "llm": None}
            ctx = dict(helpers, id=eid, e=e, f=e["facts"], down=down, up=up,
                       incidents=incidents_for(eid), runbooks=runbooks_for(related),
                       team=self.cfg.team(e["team"]), system=self.cfg.system(e["system"]),
                       members=sorted(x.src for x in in_e[eid] if x.rel in ("part_of", "owned_by")))
            syn = e["facts"].get("synthesis") if e["type"] == "incident" else None
            if syn:
                ctx["narrative"] = None
                li = syn["llm"]
                gen["llm"] = {"status": li["status"] if li["status"] != "ok" else ("ok" if syn["merged"].get("summary") else "rejected"),
                              "model": li.get("model"), "cached": li.get("cached"), "ms": li.get("ms"),
                              "requested": li.get("requested"), "used": li.get("used"), "fallback_reason": li.get("fallback_reason"),
                              "detail": f"구조화 추출 · 검증 통과 {sum(c['status'] == 'ok' for c in syn['checks'])} · 보정 {sum(c['status'] == 'fixed' for c in syn['checks'])} · 제외 {sum(c['status'] == 'dropped' for c in syn['checks'])}",
                              "kind": "extract"}
                gen["renderer"] = "6단계 장애 파서"
            elif e["type"] == "incident" and self.llm is not None:
                ctx["narrative"], gen["llm"] = self._narrate(e)
            elif e["type"] == "incident":
                ctx["narrative"] = None
                gen["llm"] = {"status": "off", "detail": "llm=off → 템플릿 요약"}
            if e["type"] == "metric":  # 지표는 산출 경로 엔티티의 장애를 표시
                ctx["incidents"] = sorted({x.src for p in related for x in in_e[p] if x.rel == "affected"}, reverse=True)
            body = self.env.get_template(f"{e['type']}.md.j2").render(**ctx)
            if e["type"] == "runbook":  # 매뉴얼 안의 상대 링크 · Confluence 페이지 링크 → 고정 URL
                body = re.sub(r"\]\(([\w\-]+)\.(?:md|html)\)", lambda mm: f"](/e/runbook:{mm.group(1)})", body)
                rb_by_title = {x["title"]: k for k, x in ents.items() if x["type"] == "runbook"}
                body = re.sub(r"\]\(confluence:([^)]+)\)",
                              lambda mm: f"](/e/{rb_by_title[mm.group(1)]})" if mm.group(1) in rb_by_title else f"](#confluence-{mm.group(1)})", body)
            fm = yaml.safe_dump({"id": eid, "type": e["type"], "title": e["title"], "system": e["system"],
                                 "team": e["team"], "status": "generated", "sources": e["sources"]},
                                allow_unicode=True, sort_keys=False)
            body = f"---\n{fm}---\n\n{body}"
            gen["entities"] = self._trace(eid, e, body, down, up, related)
            gen["timing"] = {"render_ms": round((time.perf_counter() - t_doc) * 1000 - ((gen.get("llm") or {}).get("ms") or 0 if not syn else 0), 1),
                             "llm_ms": (gen.get("llm") or {}).get("ms"),
                             "stages": [{"name": x["name"], "ms": (syn["llm"].get("ms") if x["name"] == "LLM 구조화" and syn["llm"].get("ms") else x["ms"])} for x in syn["stages"]] if syn else None}
            facts = {k: v for k, v in e["facts"].items() if k not in ("tasks", "markdown", "rest", "raw")}
            if e["type"] == "dag":
                rest = e["facts"].get("rest") or {}
                facts.update(runs=rest.get("runs", []), paused=rest.get("is_paused"), fetched_at=rest.get("fetched_at"))
            docs.append(Document(id=eid, type=e["type"], title=e["title"], path=self.path_of(eid, e),
                                 system=e["system"], team=e["team"], markdown=body, sources=e["sources"], gen=gen,
                                 facts={**facts, "status_icon": status_of(eid),
                                        "downstream": down, "upstream": up,
                                        "incidents": ctx["incidents"], "runbooks": runbooks_for(related)}))
        return docs

    def _trace(self, eid: str, e: dict[str, Any], body: str, down: list, up: list, related: list[str]) -> dict[str, Any]:
        """이 문서의 엔티티 추출 추적: 추출 근거 → 원본 존재 여부 → 문서 사용 여부 · 문서 링크의 출처."""
        sections: dict[str, str] = {}
        cur = "개요"
        for line in body.splitlines():
            if line.startswith("## "):
                cur = line[3:].strip()
            sections[cur] = sections.get(cur, "") + line + "\n"
        recs = []
        for (s_, r_, d_), evd in getattr(self, "evidence", {}).items():
            if eid not in (s_, d_):
                continue
            other = d_ if s_ == eid else s_
            linked = [h for h, txt in sections.items() if f"/e/{other})" in txt]
            name = other.split(":", 1)[1]
            mentioned = [h for h, txt in sections.items() if h not in linked and name in txt]
            recs.append({**evd, "entity": other, "direction": "out" if s_ == eid else "in",
                         "in_raw": evd["in_raw"] if evd["surface"] else None,  # None = 계산된 관계 (원문 표면형 없음)
                         "used": "link" if linked else "mention" if mentioned else None,
                         "sections": linked + mentioned})
        direct = {r["entity"] for r in recs}
        graph = {x["id"] for x in down} | {x["id"] for x in up}
        derived = set()  # 이웃 엔티티를 거친 관계: 업스트림을 언급한 런북 · 이웃에 영향 준 장애
        for (s_, r_, d_), evd in getattr(self, "evidence", {}).items():
            if d_ in related and d_ != eid and r_ in ("mentions", "affected"):
                derived.add(s_)
        conf = {f"team:{e['team']}", f"system:{e['system']}"}
        links = []
        raw_md = (e["facts"].get("raw") or e["facts"].get("markdown") or "") if e["type"] == "runbook" else ""
        for other in dict.fromkeys(re.findall(r"\]\(/e/([^)\s]+)\)", body)):
            if other.startswith(eid + "/"):
                continue  # 자기 페이지의 하위 화면 (합성 과정 · 이력)
            other_title = (getattr(self, "_titles", {}) or {}).get(other, "")
            if raw_md and other.startswith("runbook:") and (re.search(re.escape(other.split(":", 1)[1]) + r"\.(md|html)", raw_md)
                                                          or (other_title and other_title in raw_md)):
                links.append({"id": other, "provenance": "source",
                              "sections": [h for h, txt in sections.items() if f"/e/{other})" in txt]})
                continue
            prov = ("direct" if other in direct else "graph" if other in graph else
                    "derived" if other in derived else "config" if other in conf else "none")
            links.append({"id": other, "provenance": prov,
                          "sections": [h for h, txt in sections.items() if f"/e/{other})" in txt]})
        return {"records": recs, "links": links,
                "summary": {"extracted": len(recs), "in_raw": sum(r["in_raw"] is True for r in recs),
                            "computed": sum(r["in_raw"] is None for r in recs),
                            "not_in_raw": sum(r["in_raw"] is False for r in recs),
                            "used": sum(bool(r["used"]) for r in recs), "links": len(links),
                            **{f"links_{k}": sum(l_["provenance"] == k for l_ in links)
                               for k in ("direct", "graph", "derived", "config", "source", "none")}}}

    def _thash(self, t: str) -> str:
        import hashlib
        return hashlib.sha256((TEMPLATES / f"{t}.md.j2").read_bytes()).hexdigest()[:8]

    def _narrate(self, e: dict[str, Any]):
        from .narrate import narrate_incident
        return narrate_incident(self.llm, e)


def raw_text(it: RawItem) -> str:
    """원자료의 원문 (엔티티가 실제로 원본에 있었는지 대조용)."""
    import json as _json
    p = it.payload
    if isinstance(p.get("raw"), str):
        return p["raw"]
    if "files" in p:
        return "\n".join(p["files"].values())
    if "markdown" in p:
        return p["markdown"]
    return _json.dumps(p, ensure_ascii=False, indent=1)


def _dedupe(edges: list[Edge]) -> list[Edge]:
    seen, out = set(), []
    for e in edges:
        k = (e.src, e.rel, e.dst)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
