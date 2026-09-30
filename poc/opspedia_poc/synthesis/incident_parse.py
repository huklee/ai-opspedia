"""장애 원자료 → 표준 장애 지식 (docs/incident-synthesis.md).

6단계, 단계마다 결과를 남겨 생성 이력 화면(/e/incident:X/history#synthesis)에서 그대로 시각화.
  ① 근거 분해   : Jira · 알림 · 슬랙 · 포스트모템 → 근거 조각 E1..En (출처 · 시각 · 작성자)
  ② 결정적 추출 : 시각 · 엔티티(레지스트리 매칭) · 명령어 · 수치 · Jira 필드 · 알림 이벤트
  ③ LLM 구조화  : JSON schema 강제, 모든 항목에 근거 id 인용 (Ollama → 본 구현 GPT-OSS-120B)
  ④ 검증        : 근거 존재 · 시각 일치 · 수치 근거 · 엔티티 해석 → 통과 / 보정 / 제외
  ⑤ 병합        : 결정적 사실 우선, LLM 은 서술 · 빈칸만
  ⑥ 지식 합성   : 영향 엔티티 엣지 · 유사 장애 · 런북 연결 · 런북 공백 제안
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from typing import Annotated

from pydantic import BaseModel, Field

SRC_LABEL = {"jira": "Jira", "alert": "알림", "slack": "슬랙", "postmortem": "포스트모템"}
NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(%|GB|시간|분|억|만|h)")
CMD = re.compile(r"`([^`]{6,})`")


# ---------- ① 근거 분해 ----------
def evidence(jira: dict[str, Any] | None, files: dict[str, str]) -> list[dict[str, Any]]:
    ev: list[dict[str, Any]] = []

    def add(src: str, text: str, at: str | None = None, who: str | None = None, where: str = "") -> None:
        text = text.strip()
        if text:
            ev.append({"id": f"E{len(ev) + 1}", "src": src, "at": at, "who": who, "where": where, "text": text})
    if jira:
        f = jira["fields"]
        add("jira", f["summary"], f["created"], None, "summary")
        add("jira", f.get("description") or "", f["created"], None, "description")
        if f.get("customfield_root_cause"):
            add("jira", "근본 원인: " + f["customfield_root_cause"], None, None, "root_cause 필드")
        if f.get("customfield_resolution"):
            add("jira", "조치: " + f["customfield_resolution"], f.get("resolutiondate"), None, "resolution 필드")
        for c in f["comment"]["comments"]:
            add("jira", c["body"], c["created"], c["author"]["displayName"], "comment")
    if "alerts.json" in files:
        for a in json.loads(files["alerts.json"])["alerts"]:
            lb = a["labels"]
            target = lb.get("dag_id") or lb.get("index") or ""
            add("alert", f"{lb['alertname']} {target} ({lb.get('severity', '')}): {a['annotations']['summary']}",
                a["receivedAt"], a["source"], lb["alertname"])
    if "slack.md" in files:
        day = re.search(r"\((\d{4}-\d{2}-\d{2})\)", files["slack.md"])
        for line in files["slack.md"].splitlines():
            m = re.match(r"\[(\d{2}:\d{2})\] ([^:]+): (.+)", line)
            if m:
                at = f"{day.group(1)}T{m.group(1)}:00+09:00" if day else None
                add("slack", m.group(3), at, m.group(2), m.group(1))
    if "postmortem.md" in files:
        sec = ""
        for line in files["postmortem.md"].splitlines():
            if line.startswith("## "):
                sec = line[3:].strip()
            elif line.strip() and not line.startswith("# "):
                add("postmortem", re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line), None, None, sec)
    return ev


# ---------- ② 결정적 추출 ----------
def deterministic(jira: dict[str, Any] | None, ev: list[dict[str, Any]], extractor) -> dict[str, Any]:
    mentions: dict[str, list[str]] = {}
    for e in ev:
        for ent in extractor.find(e["text"]):
            mentions.setdefault(ent, []).append(e["id"])
    alerts = [e for e in ev if e["src"] == "alert"]
    humans = [e for e in ev if e["src"] == "slack"]
    timeline = [{"at": e["at"], "event": e["text"].split(": ", 1)[-1][:90], "cites": [e["id"]], "kind": "alert",
                 "origin": "결정적 · 알림"} for e in alerts]
    cmds = [{"cmd": m.group(1), "cites": [e["id"]]} for e in ev for m in CMD.finditer(e["text"])]
    nums = [{"value": m.group(0), "cites": [e["id"]]} for e in ev for m in NUM.finditer(e["text"])]
    refs = sorted({m for e in ev for m in re.findall(r"INC-\d+", e["text"])})
    runbook_ids = sorted({m for e in ev for m in re.findall(r"RB-[A-Z]+-\d+|SOP-[A-Z]+-\d+", e["text"])})
    f = (jira or {}).get("fields", {})
    first_alert = min((e["at"] for e in alerts if e["at"]), default=None)
    first_human = min((e["at"] for e in humans if e["at"]), default=None)
    return {
        "jira": {"priority": (f.get("priority") or {}).get("name"), "status": (f.get("status") or {}).get("name"),
                 "created": f.get("created"), "resolved": f.get("resolutiondate"),
                 "assignee": (f.get("assignee") or {}).get("displayName")},
        "mentions": mentions, "timeline": timeline, "commands": cmds, "numbers": nums,
        "incident_refs": refs, "runbook_refs": runbook_ids,
        "detected_at": first_alert, "acknowledged_at": first_human,
        "tta_min": _minutes(first_alert, first_human),
    }


def _minutes(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)


# ---------- ③ LLM 구조화 ----------
EvId = Annotated[str, Field(pattern=r"^E[0-9]{1,3}$")]
Cites = Annotated[list[EvId], Field(min_length=1, max_length=4, description="근거 id (예: E3)")]


class Cited(BaseModel):
    cites: Cites
    text: str


class TimelineItem(BaseModel):
    cites: Cites
    time: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$", description="HH:MM")
    event: str


class FollowUp(BaseModel):
    cites: Cites
    task: str
    owner: str
    done: bool


class IncidentExtract(BaseModel):
    """cites 를 text 앞에 두어 근거를 먼저 고르고 문장을 쓰게 함 (제약 디코딩 순서)."""
    summary: Cited
    impact: Cited
    detection: Cited
    root_cause: Cited
    timeline: list[TimelineItem] = Field(max_length=10)
    actions: list[Cited] = Field(max_length=6)
    follow_ups: list[FollowUp] = Field(max_length=5)


SYSTEM = ("너는 장애 보고서를 표준 형식으로 정리하는 운영 엔지니어. 주어진 근거 조각(E1..)만 사용. "
          "모든 항목의 cites 에 실제 근거 id 를 넣고, 근거에 없는 사실 · 숫자 · 시각은 절대 만들지 않음. "
          "한국어, 명사형 종결로 짧게. timeline 은 근거에 시각이 있는 핵심 사건만 HH:MM 으로 5–10개. "
          '예: {"cites": ["E7"], "text": "score_candidates 메모리 초과로 실패"}')


def prompt(title: str, ev: list[dict[str, Any]]) -> str:
    lines = []
    for e in ev:
        t = datetime.fromisoformat(e["at"]).strftime("%H:%M") if e["at"] else "--:--"
        who = f" {e['who']}" if e["who"] else ""
        lines.append(f"[{e['id']} {SRC_LABEL[e['src']]} {t}{who}] {e['text']}")
    return f"장애: {title}\n\n근거 조각:\n" + "\n".join(lines)


# ---------- ④ 검증 ----------
def validate(x: IncidentExtract, ev: list[dict[str, Any]], extractor, known: set[str] = frozenset()) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    byid = {e["id"]: e for e in ev}
    checks: list[dict[str, Any]] = []
    kept: dict[str, Any] = {}

    def cited_text(cites: list[str]) -> str:
        return " ".join(byid[c]["text"] for c in cites if c in byid)

    def check(field: str, text: str, cites: list[str]) -> str:
        bad = [c for c in cites if c not in byid]
        if not cites:
            checks.append({"field": field, "status": "dropped", "reason": "근거 인용 없음", "text": text})
            return "dropped"
        if bad:
            checks.append({"field": field, "status": "dropped", "reason": f"없는 근거 {', '.join(bad)}", "text": text})
            return "dropped"
        src = cited_text(cites)
        for m in NUM.finditer(text):
            if m.group(1) not in src:
                checks.append({"field": field, "status": "dropped", "reason": f"수치 {m.group(0)} 가 인용 근거에 없음", "text": text})
                return "dropped"
        unknown = [w for w in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", text) if not extractor.find(w) and w not in known]
        if unknown:
            checks.append({"field": field, "status": "warn", "reason": f"레지스트리에 없는 식별자 {', '.join(unknown[:3])}", "text": text})
            return "warn"
        checks.append({"field": field, "status": "ok", "reason": f"근거 {', '.join(cites)}", "text": text})
        return "ok"

    for f in ("summary", "impact", "detection", "root_cause"):
        c = getattr(x, f)
        if check(f, c.text, c.cites) != "dropped":
            kept[f] = {"text": c.text, "cites": c.cites}
    kept["timeline"], kept["actions"], kept["follow_ups"] = [], [], []
    for i, t in enumerate(x.timeline):
        st = check(f"timeline[{i}]", f"{t.time} {t.event}", t.cites)
        if st == "dropped":
            continue
        times = {datetime.fromisoformat(byid[c]["at"]).strftime("%H:%M") for c in t.cites if c in byid and byid[c]["at"]}
        item = {"time": t.time, "event": t.event, "cites": t.cites}
        if times and t.time not in times:
            fixed = sorted(times)[0]
            checks.append({"field": f"timeline[{i}]", "status": "fixed", "reason": f"시각 {t.time} → 근거 시각 {fixed}", "text": t.event})
            item["time"] = fixed
        kept["timeline"].append(item)
    for i, a in enumerate(x.actions):
        if check(f"actions[{i}]", a.text, a.cites) != "dropped":
            kept["actions"].append({"text": a.text, "cites": a.cites})
    for i, u in enumerate(x.follow_ups):
        if check(f"follow_ups[{i}]", u.task, u.cites) != "dropped":
            kept["follow_ups"].append(u.model_dump())
    return kept, checks


# ---------- 전체 ----------
def parse(key: str, title: str, jira: dict[str, Any] | None, files: dict[str, str], extractor, llm,
          runbook_ids: dict[str, str], incidents: dict[str, dict[str, Any]],
          runbook_texts: dict[str, str] | None = None, runbook_mentions: dict[str, list[str]] | None = None,
          known_terms: set[str] = frozenset()) -> dict[str, Any]:
    """runbook_ids: 문서 ID(RB-RECO-001) → 엔티티 id · runbook_texts: 엔티티 id → 본문 · runbook_mentions: 엔티티 → 런북 id 목록."""
    stages = []

    def stage(name: str, t0: float, **stats) -> None:
        stages.append({"name": name, "ms": round((time.perf_counter() - t0) * 1000, 1), **stats})

    t = time.perf_counter()
    ev = evidence(jira, files)
    stage("근거 분해", t, spans=len(ev), sources={s: sum(e["src"] == s for e in ev) for s in SRC_LABEL})

    t = time.perf_counter()
    det = deterministic(jira, ev, extractor)
    stage("결정적 추출", t, entities=len(det["mentions"]), commands=len(det["commands"]), numbers=len(det["numbers"]),
          alerts=len(det["timeline"]))

    llm_info: dict[str, Any] = {"status": "off"}
    kept: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    t = time.perf_counter()
    if llm is not None:
        try:
            x = llm.complete_json(SYSTEM, prompt(title, ev), IncidentExtract)
            last = getattr(llm, "last", {})
            llm_info = {"status": "ok", "model": getattr(llm, "label", "llm"), **last,
                        "ms": last.get("ms") or round((time.perf_counter() - t) * 1000, 1), "raw": x.model_dump()}
        except Exception as ex:  # LLM 실패해도 결정적 결과로 페이지 생성
            llm_info = {"status": "error", "model": getattr(llm, "label", "llm"), "detail": str(ex)[:200]}
    stage("LLM 구조화", t, status=llm_info["status"], cached=llm_info.get("cached"))

    t = time.perf_counter()
    if llm_info["status"] == "ok":
        kept, checks = validate(IncidentExtract.model_validate(llm_info["raw"]), ev, extractor, known_terms)
    stage("검증", t, ok=sum(c["status"] == "ok" for c in checks), fixed=sum(c["status"] == "fixed" for c in checks),
          warn=sum(c["status"] == "warn" for c in checks), dropped=sum(c["status"] == "dropped" for c in checks))

    # ⑤ 병합: 결정적 사실 우선
    t = time.perf_counter()
    f = (jira or {}).get("fields", {})
    merged: dict[str, Any] = {}
    for k in ("summary", "impact", "detection"):
        if k in kept:
            merged[k] = {**kept[k], "origin": "LLM · 검증 통과"}
    if f.get("customfield_root_cause"):
        merged["root_cause"] = {"text": f["customfield_root_cause"], "cites": [e["id"] for e in ev if e["where"] == "root_cause 필드"],
                                "origin": "Jira 필드 (결정적)"}
        if "root_cause" in kept:
            merged["root_cause_llm"] = {**kept["root_cause"], "origin": "LLM · 보조"}
    elif "root_cause" in kept:
        merged["root_cause"] = {**kept["root_cause"], "origin": "LLM · 리뷰 필요"}
    tl = [{"time": datetime.fromisoformat(x["at"]).strftime("%H:%M"), "event": x["event"], "cites": x["cites"],
           "origin": x["origin"]} for x in det["timeline"]]
    seen = {x["time"] for x in tl}
    for x in kept.get("timeline", []):
        if x["time"] not in seen:
            tl.append({**x, "origin": "LLM"})
            seen.add(x["time"])
    merged["timeline"] = sorted(tl, key=lambda x: x["time"])
    merged["actions"] = [{**a, "cmd": next((c["cmd"] for c in det["commands"] if set(c["cites"]) & set(a["cites"])), None)}
                         for a in kept.get("actions", [])]
    cmd_cited = {c for a in merged["actions"] for c in a["cites"]}
    merged["actions"] += [{"text": "명령 실행", "cmd": c["cmd"], "cites": c["cites"], "origin": "결정적 · 명령어"}
                          for c in det["commands"] if not set(c["cites"]) & cmd_cited]
    merged["follow_ups"] = kept.get("follow_ups", [])
    stage("병합", t, fields=len([k for k in merged if merged[k]]))

    # ⑥ 지식 합성
    t = time.perf_counter()
    affected = [ent for ent, cs in sorted(det["mentions"].items(), key=lambda kv: -len(kv[1]))]
    edges = [{"src": f"incident:{key}", "rel": "affected", "dst": ent, "cites": det["mentions"][ent]} for ent in affected]
    runbooks = [{"id": runbook_ids[r], "ref": r, "cites": [e["id"] for e in ev if r in e["text"]]}
                for r in det["runbook_refs"] if r in runbook_ids]
    similar = []
    for other, o in incidents.items():
        if other == key:
            continue
        overlap = sorted(set(affected) & set(o.get("affected", [])))
        referenced = other in det["incident_refs"]
        if referenced or len(overlap) >= 2:
            similar.append({"id": f"incident:{other}", "overlap": overlap, "referenced": referenced})
    similar.sort(key=lambda s: (-s["referenced"], -len(s["overlap"])))
    linked = {r["id"] for r in runbooks}
    for ent in affected:
        linked |= set((runbook_mentions or {}).get(ent, []))
    texts = " ".join((runbook_texts or {}).get(r, "") for r in linked)
    gaps = []
    for c in det["commands"]:
        head = " ".join(re.sub(r"<[^>]+>|'[^']*'", "", c["cmd"]).split()[:3])  # 명령 골격 (인자 제외)
        if head and head not in texts:
            gaps.append({"cmd": c["cmd"], "pattern": head, "cites": c["cites"]})
    stage("지식 합성", t, edges=len(edges), similar=len(similar), runbooks=len(linked), gaps=len(gaps))
    return {"stages": stages, "evidence": ev, "deterministic": det, "llm": llm_info, "checks": checks,
            "merged": merged, "knowledge": {"affected": affected, "edges": edges, "similar": similar,
                                            "runbooks": runbooks, "linked_runbooks": sorted(linked), "command_gaps": gaps}}
