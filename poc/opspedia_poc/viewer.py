"""위키 뷰어: FastAPI + Jinja2 + markdown-it-py (ADR-009 방식, PoC 단계에서 MkDocs 대체).

화면: 홈 · 검색(구글식 탭) · 엔티티 페이지(노션식) · 서비스 지표 · 관리자(실행 이력 · 합성 내역 · 버전 diff).
"""
from __future__ import annotations

import difflib
import html
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from mdit_py_plugins.admon import admon_plugin
from mdit_py_plugins.anchors import anchors_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .mdutil import strip_frontmatter
from .pipeline import JOBS
from .services import Services

WEB = Path(__file__).parent / "web"
ICON = {"system": "🏢", "dag": "🔁", "table": "🗄️", "index": "📇", "alias": "🔗", "incident": "🚨",
        "runbook": "📕", "team": "👥", "metric": "📈", "category": "📊"}
TYPE_LABEL = {"system": "시스템", "dag": "DAG", "table": "테이블", "index": "인덱스", "alias": "alias",
              "incident": "장애", "runbook": "런북", "team": "팀", "metric": "지표", "category": "지표 카테고리"}
TABS = [("all", "전체"), ("dag", "DAG"), ("table", "테이블"), ("index", "인덱스"), ("incident", "장애"),
        ("runbook", "런북"), ("metric", "지표"), ("etc", "기타")]
TRIGGER = {"schedule": "🕐 스케줄", "manual": "👤 수동", "webhook": "🔔 웹훅", "upload": "📤 업로드", "undo": "↩️ 되돌리기"}
STAGE_LABEL = {"block": "차단", "fetch": "수집", "fallback": "대체", "collect": "엔티티 구성", "synthesize": "합성",
               "store": "저장", "index": "색인", "embed": "임베딩"}


def _highlight(code: str, lang: str, _attrs: str) -> str:
    if lang == "mermaid":
        return f'<pre class="mermaid">{html.escape(code)}</pre>'
    try:
        lexer = get_lexer_by_name(lang or "text")
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    return highlight(code, lexer, HtmlFormatter(nowrap=False, cssclass="hl"))


MD = (MarkdownIt("commonmark", {"html": False, "highlight": _highlight, "linkify": False})
      .enable("table").enable("strikethrough").use(admon_plugin).use(anchors_plugin, max_level=3))


def render_md(markdown: str) -> str:
    _, body = strip_frontmatter(markdown)
    body = re.sub(r"^# .*\n", "", body, count=1, flags=re.M)  # 제목은 페이지 헤더에서 표시
    body = re.sub(r"<!-- gen:(start|end)[^>]*-->\n?", "", body)
    out = MD.render(body)
    # 생성 요약 구역 표시 (노션 callout 처럼)
    return out


def _when(ts: str | None) -> str:
    if not ts:
        return "—"
    return datetime.fromisoformat(ts).strftime("%m-%d %H:%M")


def _ago(ts: str | None, now: datetime) -> str:
    if not ts:
        return ""
    d = now - datetime.fromisoformat(ts)
    s = d.total_seconds()
    if s < 3600:
        return f"{max(1, int(s // 60))}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    return f"{int(s // 86400)}일 전"


def sparkline(points: list[dict[str, Any]], slo: float | None = None, w: int = 120, h: int = 32, bad: bool = False) -> str:
    vals = [p["v"] for p in points if p["v"] is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals + ([slo] if slo is not None else [])), max(vals + ([slo] if slo is not None else []))
    span = (hi - lo) or 1

    def y(v: float) -> float:
        return h - 3 - (v - lo) / span * (h - 6)
    n = len(points) - 1
    coords = [(i / n * (w - 4) + 2, y(p["v"])) for i, p in enumerate(points) if p["v"] is not None]
    path = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in coords)
    color = "var(--bad)" if bad else "var(--accent)"
    slo_line = f'<line x1="0" x2="{w}" y1="{y(slo):.1f}" y2="{y(slo):.1f}" class="slo"/>' if slo is not None else ""
    lx, ly = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">{slo_line}'
            f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.6"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.4" fill="{color}"/></svg>')


def highlight_terms(text: str, q: str) -> str:
    esc = html.escape(text)
    terms = sorted({t for t in re.split(r"\s+", q.strip()) if len(t) >= 2}, key=len, reverse=True)
    for t in terms:
        esc = re.sub(re.escape(html.escape(t)), lambda m: f"<b>{m.group(0)}</b>", esc, flags=re.I)
    return esc


def diff_lines(a: str, b: str) -> list[tuple[str, str]]:
    out = []
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=2):
        if line.startswith(("+++", "---")):
            continue
        kind = "hunk" if line.startswith("@@") else "add" if line.startswith("+") else "del" if line.startswith("-") else "ctx"
        out.append((kind, line))
    return out


def mount(app: FastAPI, get_svc) -> None:
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape(["html"]))
    env.globals.update(hl=lambda text, surf: re.sub(re.escape(html.escape(surf)), lambda m: f"<mark>{m.group(0)}</mark>", html.escape(text), flags=re.I) if surf else html.escape(text),
                       ICON=ICON, TYPE_LABEL=TYPE_LABEL, when=_when, TRIGGER=TRIGGER, STAGE_LABEL=STAGE_LABEL,
                       sparkline=sparkline, JOBS=JOBS)
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    def page(name: str, request: Request, **ctx) -> HTMLResponse:
        svc: Services = get_svc()
        now = datetime.fromisoformat(svc.cfg.now)
        lr = svc.repo.last_run()
        if lr and lr["finished_at"] and datetime.fromisoformat(lr["finished_at"]) > now:
            now = datetime.fromisoformat(lr["finished_at"])
        ctx.setdefault("q", "")
        return HTMLResponse(env.get_template(name).render(
            request=request, tree=_tree(svc), svc=svc, now=now, ago=lambda ts: _ago(ts, now), **ctx))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        svc = get_svc()
        docs = svc.docs
        alerts = sorted((d for d in docs.values() if d["facts"].get("status_icon") in ("🔴", "❌", "⛔")
                         and d["type"] in ("dag", "index", "metric")),
                        key=lambda d: (["🔴", "❌", "⛔"].index(d["facts"]["status_icon"]), d["type"], d["id"]))
        cats = [d for d in docs.values() if d["type"] == "category"]
        cats.sort(key=lambda d: d["id"])
        updates = svc.repo.recent_updates(12)
        mruns = svc.repo.runs(1, job="metrics-hourly")
        return page("home.html", request, alerts=alerts, cats=cats, updates=updates, metrics=_metric_map(svc),
                    mrun=mruns[0] if mruns else None)

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", tab: str = "all"):
        svc = get_svc()
        t0 = time.perf_counter()
        res = svc.retriever.search(q, 50) if q.strip() else []
        took = time.perf_counter() - t0
        counts: dict[str, int] = defaultdict(int)
        for r in res:
            counts[r["type"] if r["type"] in dict(TABS) else "etc"] += 1
        counts["all"] = len(res)
        shown = [r for r in res if tab == "all" or (r["type"] if r["type"] in dict(TABS) else "etc") == tab][:20]
        for r in shown:
            r["snippet_html"] = highlight_terms(r["snippet"], q)
            r["crumbs"] = ["ai-opspedia"] + [p for p in r["path"].split("/") if p != "index"][:-1]
        panel = None
        if res and tab == "all":  # 구글 지식 패널: 상위 3개 중 첫 엔티티
            top = next((svc.docs[r["id"]] for r in res[:3]
                        if svc.docs[r["id"]]["type"] in ("dag", "index", "table", "metric", "incident")), None)
            if top:
                panel = {"doc": top, "ctx": svc.context(top["id"])}
        from .ask import looks_natural
        return page("search.html", request, q=q, tab=tab, tabs=TABS, counts=counts, results=shown, natural=looks_natural(q),
                    took=took, backend=svc.backend, panel=panel)

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request):
        svc = get_svc()
        cats = sorted((d for d in svc.docs.values() if d["type"] == "category"), key=lambda d: d["id"])
        runs = svc.repo.runs(8, job="metrics-hourly")
        return page("metrics.html", request, cats=cats, metrics=_metric_map(svc), runs=runs)

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, job: str | None = None):
        svc = get_svc()
        runs = svc.repo.runs(200)
        shown = [r for r in runs if not job or r["job"] == job]
        last_by_job = {}
        for r in runs:
            last_by_job.setdefault(r["job"], r)
        kpi = {"runs": len(runs), "ok": sum(r["status"] == "success" for r in runs),
               "partial": sum(r["status"] == "partial" for r in runs), "failed": sum(r["status"] == "failed" for r in runs),
               "docs": len(svc.docs),
               "versions": svc.repo.db.execute("SELECT count(*) FROM document_versions").fetchone()[0],
               "llm": svc.repo.db.execute("SELECT count(*) FROM synthesis_log WHERE llm LIKE '%\"status\": \"ok\"%' AND action != 'unchanged'").fetchone()[0],
               "llm_rej": svc.repo.db.execute("SELECT count(*) FROM synthesis_log WHERE llm LIKE '%\"status\": \"rejected\"%' AND action != 'unchanged'").fetchone()[0]}
        return page("admin.html", request, runs=shown, job=job, last_by_job=last_by_job, kpi=kpi,
                    durations={r["id"]: _dur_s(r) for r in shown})

    @app.get("/admin/upload", response_class=HTMLResponse)
    def upload_page(request: Request):
        from .uploads import listing
        svc = get_svc()
        samples = sorted(p.name for p in (WEB / "static" / "samples").iterdir())
        return page("upload.html", request, files=listing(svc.cfg, svc), samples=samples)

    @app.get("/admin/runs/{run_id}", response_class=HTMLResponse)
    def admin_run(request: Request, run_id: str):
        svc = get_svc()
        r = svc.repo.run(run_id)
        if not r:
            raise HTTPException(404, "unknown run")
        total = sum(s["ms"] for s in r["steps"]) or 1
        acc, bars = 0.0, []
        for s in r["steps"]:
            bars.append({"left": acc / total * 100, "width": max(s["ms"] / total * 100, 0.6), **s})
            acc += s["ms"]
        changed = [{**x, "total_ms": ((x.get("timing") or {}).get("render_ms") or 0) + ((x.get("timing") or {}).get("llm_ms") or 0)}
                   for x in r["synthesis"] if x["action"] != "unchanged"]
        changed.sort(key=lambda x: (x["action"] != "created", x["doc_id"]))
        gen_types: dict[str, int] = {}
        for x in changed:
            t = x["doc_id"].split(":")[0]
            gen_types[t] = gen_types.get(t, 0) + 1
        same = [x for x in r["synthesis"] if x["action"] == "unchanged"]
        llm_rows = [x for x in r["synthesis"] if x["llm"] and x["llm"].get("status") not in (None, "off")]
        return page("run.html", request, r=r, bars=bars, total=total, changed=changed, same=same, gen_types=sorted(gen_types.items()),
                    llm_rows=llm_rows, dur=_dur_s(r))

    @app.get("/e/{entity:path}/synthesis")
    def synthesis_redirect(entity: str):
        return RedirectResponse(f"/e/{entity}/history#synthesis", status_code=301)

    @app.get("/e/{entity:path}/history", response_class=HTMLResponse)
    def history(request: Request, entity: str, v: int | None = None):
        svc = get_svc()
        d = svc.docs.get(entity)
        if not d:
            raise HTTPException(404, "unknown entity")
        versions = svc.repo.history(entity)
        v = v or d["version"]
        cur = svc.repo.version(entity, v)
        prev = svc.repo.version(entity, v - 1) if v > 1 else None
        lines = diff_lines(prev["markdown"], cur["markdown"]) if (cur and prev) else []
        syn = svc.repo.synthesis_for(entity)
        sv = next((x for x in syn if x["version"] == v), None)
        steps = (svc.repo.run(cur["run_id"]) or {}).get("steps", []) if cur else []
        raws = []
        if sv:
            recs = (sv.get("entities") or {}).get("records", [])
            for rp in svc.repo.raw_payloads(sv.get("raw") or []):
                surfaces = sorted({r["surface"] for r in recs if r["raw"]["source"] == rp["source"] and r["raw"]["key"] == rp["key"] and r["surface"]},
                                  key=len, reverse=True)
                raws.append({**rp, "html": _raw_html(rp["payload"], surfaces), "surfaces": surfaces})
        total_ms = sum(x["ms"] for x in steps) or 1
        return page("history.html", request, d=d, versions=versions, v=v, cur=cur, prev=prev, lines=lines,
                    syn=syn, syn_by_v={s["version"]: s for s in syn}, sv=sv, steps=steps, total_ms=total_ms, raws=raws,
                    PROV={"direct": ("원자료에서 직접 추출", "ok"), "graph": ("그래프 탐색 (N홉)", ""), "derived": ("이웃 엔티티 경유", ""),
                          "config": ("설정", ""), "source": ("원문에 있던 링크", "ok"), "none": ("근거 없음", "bad")},
                    **_synthesis_ctx(svc, d))

    @app.get("/e/{entity:path}", response_class=HTMLResponse)
    def entity_page(request: Request, entity: str):
        svc = get_svc()
        d = svc.docs.get(entity)
        if not d:
            raise HTTPException(404, f"unknown entity {entity}")
        body = render_md(d["markdown"])
        crumbs = _crumbs(svc, d)
        extra: dict[str, Any] = {}
        if d["type"] == "category":
            extra["metrics"] = _metric_map(svc)
        return page("entity.html", request, d=d, body=body, props=_props(svc, d), crumbs=crumbs, **extra)


def _raw_html(payload: dict[str, Any], surfaces: list[str]) -> str:
    """원자료 원문을 보여주고, 추출된 엔티티 표면형을 강조."""
    import json as _json
    if isinstance(payload.get("raw"), str):
        text = payload["raw"]
    elif "files" in payload:
        text = "\n\n".join(f"── {k} ──\n{v}" for k, v in payload["files"].items())
    else:
        text = _json.dumps({k: v for k, v in payload.items() if k not in ("markdown",)}, ensure_ascii=False, indent=1)
        if "markdown" in payload:
            text = payload["markdown"]
    out = html.escape(text[:20000])
    for sf in surfaces:
        esc = html.escape(sf)
        out = re.sub(re.escape(esc), lambda m: f"<mark>{m.group(0)}</mark>", out, flags=re.I)
    return out



def _synthesis_ctx(svc, d: dict[str, Any]) -> dict[str, Any]:
    """생성 이력에 포함되는 장애 합성 과정 화면용 데이터."""
    import json as _json
    sy = d["facts"].get("synthesis")
    if not sy:
        return {}
    names = sorted(((svc.docs[i]["title"], i) for i in sy["deterministic"]["mentions"] if i in svc.docs),
                   key=lambda x: -len(x[0]))

    def mark(text: str) -> str:
        out = html.escape(text)
        for t, _ in names:
            out = out.replace(html.escape(t), f"<mark>{html.escape(t)}</mark>")
        return out
    g = ["flowchart LR", f'    I["🚨 {d["facts"]["key"]}"]']
    for n, ent in enumerate(sy["knowledge"]["affected"][:10]):
        t = svc.docs[ent]["title"] if ent in svc.docs else ent
        g += [f'    A{n}["{ICON.get(ent.split(":")[0], "")} {t}"]', f"    I -->|affected| A{n}"]
    for n, s_ in enumerate(sy["knowledge"]["similar"]):
        g += [f'    S{n}["🚨 {s_["id"].split(":")[1]}"]', f"    I -.->|similar_to| S{n}"]
    for n, r in enumerate(sy["knowledge"]["runbooks"]):
        t = svc.docs[r["id"]]["title"] if r["id"] in svc.docs else r["id"]
        g += [f'    R{n}["📕 {t}"]', f"    I ==>|mitigated_by| R{n}"]
    return {"mark": mark, "graph": "\n".join(g), "raw_json": _json.dumps(sy["llm"].get("raw"), ensure_ascii=False, indent=1),
            "SRC": {"jira": "Jira", "alert": "알림", "slack": "슬랙", "postmortem": "포스트모템"}}

def _dur_s(r: dict[str, Any]) -> str:
    if not r.get("finished_at"):
        return "—"
    s = (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds()
    return f"{s:.0f}초" if s >= 1 else "<1초"


def _metric_map(svc: Services) -> dict[str, dict[str, Any]]:
    return {d["id"]: d for d in svc.docs.values() if d["type"] == "metric"}


def _crumbs(svc: Services, d: dict[str, Any]) -> list[tuple[str, str | None]]:
    t = d["type"]
    if t in ("dag", "table", "index", "alias") and d["system"]:
        s = svc.docs.get(f"system:{d['system']}")
        return [("🏢 " + (s["title"] if s else d["system"]), f"/e/system:{d['system']}"), (TYPE_LABEL[t], None)]
    if t == "metric":
        c = svc.docs.get(f"category:{d['facts']['category']}")
        return [("📈 서비스 지표", "/metrics"), ((c["facts"]["icon"] + " " + c["title"]) if c else "", f"/e/category:{d['facts']['category']}")]
    if t == "category":
        return [("📈 서비스 지표", "/metrics")]
    return [({"incident": "🚨 장애", "runbook": "📕 런북 · 매뉴얼", "team": "👥 팀", "system": "🏢 시스템"}[t], None)]


def _props(svc: Services, d: dict[str, Any]) -> list[tuple[str, str, str]]:
    """노션식 속성: (아이콘, 이름, HTML 값)."""
    f, t, e = d["facts"], d["type"], html.escape

    def link(i: str) -> str:
        x = svc.docs.get(i)
        return f'<a class="mention" href="/e/{e(i)}">{ICON.get(x["type"], "") if x else ""} {e(x["title"] if x else i)}</a>'

    def chip(text: str, tone: str) -> str:
        return f'<span class="chip {tone}">{e(text)}</span>'
    team = svc.cfg.team(d["team"])
    p: list[tuple[str, str, str]] = []
    if t == "dag":
        runs = f.get("runs") or [{}]
        st = runs[0].get("state") or "—"
        tone = {"success": "ok", "failed": "bad", "upstream_failed": "bad"}.get(st, "warn" if f.get("paused") else "")
        p.append(("◉", "상태", chip(("⏸ paused · " if f.get("paused") else "") + st, tone) + f' <span class="muted">{_when(runs[0].get("start_date"))}</span>'))
        p.append(("⏰", "스케줄", f"<code>{e(str(f.get('schedule')))}</code>"))
        p.append(("🏷️", "태그", " ".join(chip(x, "") for x in f.get("tags", []))))
    elif t == "index":
        h = f["health"]
        p.append(("◉", "상태", chip({"crit": "조치 필요", "warn": "주의", "ok": "정상"}[h["level"]], {"crit": "bad", "warn": "warn", "ok": "ok"}[h["level"]])))
        p.append(("🔗", "alias", f"<code>{e(f['alias'])}</code> → <code>{e(str(h['alias_target']))}</code> · 최신 <code>{e(str(h['newest']))}</code>"))
        p.append(("🏗️", "빌더", link(f"dag:{f['builder_dag']}")))
    elif t == "alias":
        p.append(("🎯", "대상", f"<code>{e(str(f['target']))}</code>"))
        p.append(("📇", "패밀리", link(f"index:{f['family']}")))
    elif t == "table":
        p.append(("📝", "설명", e(f.get("comment") or "—")))
        p.append(("🧱", "컬럼", f"{len(f.get('columns', []))}개" + (f" · 파티션 <code>{e(', '.join(f['partitions']))}</code>" if f.get("partitions") else "")))
    elif t == "incident":
        x = f["fields"]
        p.append(("◉", "상태", chip(x["status"]["name"], "ok" if x["status"]["name"] == "Resolved" else "bad") + " " + chip(x["priority"]["name"], "warn")))
        p.append(("🕐", "발생", _when(x["created"]) + (f" → 해결 {_when(x['resolutiondate'])}" if x.get("resolutiondate") else "")))
        p.append(("🎯", "영향", " ".join(link(a) for a in f.get("affected", [])) or "—"))
    elif t == "runbook":
        m = re.search(r"문서 ID\W+([A-Z]+-[A-Z]*-?\d+)", d["markdown"])
        p.append(("🪪", "문서 ID", f"<code>{e(m.group(1))}</code>" if m else "—"))
        p.append(("🎯", "관련", " ".join(link(i) for i in f.get("mentions", [])[:6]) or "—"))
    elif t == "metric":
        ev = f["eval"]
        tone = {"breach": "bad", "nodata": "bad", "near": "warn", "ok": "ok"}[ev["state"]]
        p.append(("◉", "판정", chip(ev["reason"], tone) + (" " + chip("이상 탐지 3σ", "bad") if ev["anomaly"] else "")))
        p.append(("🎯", "SLO", e(ev["slo_fmt"]) + f' <span class="muted">· 7일 평균 {e(ev["avg7_fmt"])}</span>'))
        p.append(("🧮", "산출", f"<code>{e(f['source'])}</code>"))
    if team:
        p.append(("👥", "담당", link(f"team:{team['id']}") + f' <span class="muted">· 온콜 {e(team.get("oncall", ""))} · {e(team.get("channel", ""))}</span>'))
    if d["system"] and t not in ("system",):
        p.append(("🏢", "시스템", link(f"system:{d['system']}")))
    if f.get("fetched_at"):
        p.append(("📡", "스냅샷", f"{_when(f['fetched_at'])} 기준"))
    gen = d.get("gen") or {}
    how = "결정적 템플릿 " + f"<code>{e(gen.get('template', ''))}</code>"
    llm = gen.get("llm") or {}
    if llm.get("status") == "ok":
        how += " + " + chip("LLM 서술 · 인용 통과", "ok")
    elif llm.get("status") == "rejected":
        how += " + " + chip("LLM 서술 거절 → 템플릿 요약", "warn")
    p.append(("⚙️", "생성", how))
    if f.get("synthesis"):
        p.append(("🧪", "합성 과정", f'<a class="mention" href="/e/{e(d["id"])}/history#synthesis">생성 이력에서 원자료 → 근거 → 추출 → 검증 → 지식 보기</a>'))
    p.append(("🕘", "버전", f'v{d["version"]} · {_when(d["updated_at"])} <a class="muted" href="/e/{e(d["id"])}/history">생성 이력 →</a>'))
    return p


def _tree(svc: Services) -> dict[str, Any]:
    docs = list(svc.docs.values())
    systems = []
    for s in sorted((d for d in docs if d["type"] == "system"), key=lambda d: d["id"]):
        groups = []
        for t in ("dag", "table", "index", "alias"):
            items = sorted((d for d in docs if d["type"] == t and d["system"] == s["system"]), key=lambda d: d["title"])
            if items:
                groups.append((t, items))
        systems.append((s, groups))
    by = lambda t: sorted((d for d in docs if d["type"] == t), key=lambda d: d["id"], reverse=(t == "incident"))  # noqa: E731
    return {"systems": systems, "categories": by("category"), "incidents": by("incident"),
            "runbooks": sorted(by("runbook"), key=lambda d: d["title"]), "teams": by("team")}
