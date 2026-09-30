"""운영 메타데이터(배치 이력 · 리니지 · 적재 · 행동 로그)에서 이상 징후를 규칙으로 찾고 장애 단위로 묶기.

LLM 없음. 모든 탐지는 CSV 원본 행을 근거로 남김 (rows = (파일, 행 번호, 원문)).

규칙
  R1 workflow_failed   : workflow_run_history.run_status = failed (+ 같은 run_id 의 task error_message)
  R2 sla_delay         : 실제 종료 - 기대 종료 > delay_threshold_min (batch_master, 가장 가까운 기대 시작에 맞춤)
  R3 run_missing       : 다른 워크플로가 모두 실행한 base_dt 에 실행 기록 없음
  R4 volume_drop       : row_count < 31일 중앙값 × 0.5 (ingest_load_status · query_load_status)
  R5 quality_outlier   : distinct_count 가 중앙값 대비 30 % 넘게 벗어나고 MAD × 5 초과 · null_count 가 평소 0 인데 > 0
  R6 behavior_drop     : 행동 로그 일별 노출 합계 < 중앙값 × 0.6
장애 묶기: 날짜 차이 1일 이내이고 같은 엔티티이거나 리니지로 이어진 탐지를 union-find 로 합침.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

KST = "+09:00"


@dataclass
class Finding:
    rule: str
    severity: str            # crit | warn | info
    base_dt: str
    at: str                  # 탐지 기준 시각 (KST ISO)
    entities: list[str]      # dag:<wf> · table:<db.table>
    summary: str
    rows: list[tuple[str, int, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace(" ", "T")[:19])


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + KST


def _anchor(start: datetime, hhmmss: str) -> datetime:
    cands = [datetime.fromisoformat(f"{(start + timedelta(days=k)).date()}T{hhmmss}") for k in (-1, 0, 1)]
    return min(cands, key=lambda c: abs((c - start).total_seconds()))


def sla_window(start: datetime, exp_start: str, exp_end: str) -> tuple[datetime, datetime]:
    es = _anchor(start, exp_start)
    ee = datetime.fromisoformat(f"{es.date()}T{exp_end}")
    if ee < es:
        ee += timedelta(days=1)
    return es, ee


def detect(t: dict[str, list[dict[str, Any]]], table_of) -> list[Finding]:
    """t: 파일별 행 목록(각 행에 _file · _line · _raw). table_of(db, table) → 'db.table'."""
    out: list[Finding] = []
    wrh, trh, bm = t["workflow_run_history"], t["task_run_history"], t["batch_master"]
    master = {r["workflow_id"]: r for r in bm if r["check_level"] == "workflow"}

    # R1 실패 + 태스크 오류
    for r in wrh:
        if r["run_status"] != "failed":
            continue
        errs = [e for e in trh if e["workflow_id"] == r["workflow_id"] and e["run_id"] == r["run_id"] and e["error_message"]]
        msg = "; ".join(f"{e['task_id']}(try {e['try_number']}): {e['error_message']}" for e in errs) or "오류 메시지 없음"
        out.append(Finding("workflow_failed", "crit", r["base_dt"], _iso(_ts(r["end_time"])), [f"dag:{r['workflow_id']}"],
                           f"{r['workflow_id']} 실행 실패 ({int(r['duration_sec'])}초) · {msg}",
                           [(r["_file"], r["_line"], r["_raw"])] + [(e["_file"], e["_line"], e["_raw"]) for e in errs],
                           {"errors": [e["error_message"] for e in errs]}))
    # 실패 실행에 속하지 않은 태스크 오류
    failed_runs = {(r["workflow_id"], r["run_id"]) for r in wrh if r["run_status"] == "failed"}
    for e in trh:
        if e["error_message"] and (e["workflow_id"], e["run_id"]) not in failed_runs:
            out.append(Finding("workflow_failed", "warn", e["base_dt"], _iso(_ts(e["end_time"])), [f"dag:{e['workflow_id']}"],
                               f"{e['workflow_id']}.{e['task_id']} 태스크 오류 (try {e['try_number']}): {e['error_message']}",
                               [(e["_file"], e["_line"], e["_raw"])]))

    # R2 SLA 지연
    for r in wrh:
        m = master.get(r["workflow_id"])
        if not m:
            continue
        st, et = _ts(r["start_time"]), _ts(r["end_time"])
        _, ee = sla_window(st, m["expected_start_time"], m["expected_end_time"])
        late = (et - ee).total_seconds() / 60
        thr = float(m["delay_threshold_min"])
        if late > thr:
            out.append(Finding("sla_delay", "crit" if late >= 60 else "warn", r["base_dt"], _iso(et), [f"dag:{r['workflow_id']}"],
                               f"{r['workflow_id']} SLA 지연 {late:.0f}분 (기대 종료 {ee:%H:%M} + 허용 {thr:.0f}분, 실제 종료 {et:%m-%d %H:%M})",
                               [(r["_file"], r["_line"], r["_raw"]), (m["_file"], m["_line"], m["_raw"])],
                               {"late_min": round(late, 1), "threshold": thr}))

    # R3 실행 누락
    days_all = sorted({r["base_dt"] for r in wrh})
    by_wf: dict[str, set[str]] = {}
    for r in wrh:
        by_wf.setdefault(r["workflow_id"], set()).add(r["base_dt"])
    for wf, days in by_wf.items():
        if len(days) < 0.8 * len(days_all):
            continue  # 주기가 다른 워크플로(학습 등)는 제외
        for d in days_all:
            if d not in days:
                out.append(Finding("run_missing", "warn", d, f"{d}T23:59:00{KST}", [f"dag:{wf}"],
                                   f"{wf} 실행 기록 없음 (base_dt {d}) — 같은 날 다른 워크플로는 실행", []))

    # R4 적재량 급감
    def volume(rows: list[dict[str, Any]], key, label, entity) -> None:
        groups: dict[Any, list[dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        for k, g in groups.items():
            vals = [float(r["row_count"]) for r in g]
            med = statistics.median(vals)
            for r in g:
                v = float(r["row_count"])
                if med > 0 and v < 0.5 * med:
                    day = r.get("base_dt") or r["part_dt"]
                    out.append(Finding("volume_drop", "crit", day, f"{day}T09:00:00{KST}", entity(k),
                                       f"{label(k)} 적재 행 수 {v:,.0f} — 31일 중앙값 {med:,.0f} 의 {v / med:.0%}",
                                       [(r["_file"], r["_line"], r["_raw"])], {"ratio": round(v / med, 3)}))
    volume(t["ingest_load_status"], lambda r: (r["database_name"], r["table_name"]),
           lambda k: table_of(*k), lambda k: [f"table:{table_of(*k)}"])
    volume(t["query_load_status"], lambda r: (r["workflow_id"], r["task_id"], r["query_name"]),
           lambda k: f"{k[0]}.{k[1]} 쿼리 {k[2]}", lambda k: [f"dag:{k[0]}"])

    # R5 품질 지표 이상
    def quality(rows: list[dict[str, Any]], key, label, entity) -> None:
        groups: dict[Any, list[dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        for k, g in groups.items():
            vals = [float(r["metric_value"]) for r in g]
            med = statistics.median(vals)
            mad = statistics.median([abs(v - med) for v in vals]) or 1.0
            name = g[0]["metric_name"]
            for r in g:
                v = float(r["metric_value"])
                day = r.get("base_dt") or r["part_dt"]
                if name == "null_count" and med == 0 and v > 0:
                    out.append(Finding("quality_outlier", "warn", day, f"{day}T09:00:00{KST}", entity(k),
                                       f"{label(k)} null {v:,.0f}건 — 평소 0", [(r["_file"], r["_line"], r["_raw"])], {"value": v}))
                elif name == "distinct_count" and med > 0 and abs(v - med) > 5 * mad and abs(v - med) / med > 0.3:
                    out.append(Finding("quality_outlier", "warn", day, f"{day}T09:00:00{KST}", entity(k),
                                       f"{label(k)} distinct {v:,.0f} — 중앙값 {med:,.0f} 대비 {v / med - 1:+.0%}",
                                       [(r["_file"], r["_line"], r["_raw"])], {"value": v, "median": med}))
    quality(t["ingest_metric_agg"], lambda r: (r["database_name"], r["table_name"], r["metric_name"], r["metric_col"]),
            lambda k: f"{table_of(k[0], k[1])} {k[3]}", lambda k: [f"table:{table_of(k[0], k[1])}"])
    quality(t["query_metric_stats"], lambda r: (r["workflow_id"], r["task_id"], r["metric_name"], r["metric_col"]),
            lambda k: f"{k[0]}.{k[1]} {k[3]}", lambda k: [f"dag:{k[0]}"])

    # R6 행동 로그 일별 노출 급감
    daily: dict[str, float] = {}
    rows_by_day: dict[str, list[dict[str, Any]]] = {}
    for r in t["behavior_pattern_agg"]:
        if r["event_type"] == "imp":
            daily[r["base_dt"]] = daily.get(r["base_dt"], 0) + float(r["event_count"])
            rows_by_day.setdefault(r["base_dt"], []).append(r)
    if daily:
        med = statistics.median(daily.values())
        for d, v in sorted(daily.items()):
            if v < 0.6 * med:
                rs = rows_by_day[d][:4]
                out.append(Finding("behavior_drop", "crit", d, f"{d}T09:00:00{KST}", ["dag:temp_log_agg"],
                                   f"행동 로그 일별 노출 합계 {v:,.0f} — 중앙값 {med:,.0f} 의 {v / med:.0%}",
                                   [(r["_file"], r["_line"], r["_raw"]) for r in rs], {"ratio": round(v / med, 3)}))
    return out


def group(findings: list[Finding], adjacent, same_day=lambda a, b: False) -> list[list[Finding]]:
    """날짜 차이 ≤ 1일이고 같은 엔티티이거나 리니지로 이웃(adjacent(a, b))인 탐지를 하나의 장애로.
    same_day(a, b): 리니지와 무관하게 합칠 규칙 (예: 같은 날의 실패와 실행 누락)."""
    parent = list(range(len(findings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(findings):
        for j in range(i + 1, len(findings)):
            b = findings[j]
            if abs((datetime.fromisoformat(a.base_dt) - datetime.fromisoformat(b.base_dt)).days) > 1:
                continue
            if (set(a.entities) & set(b.entities) or same_day(a, b)
                    or any(adjacent(x, y) for x in a.entities for y in b.entities)):
                parent[find(i)] = find(j)
    groups: dict[int, list[Finding]] = {}
    for i, f in enumerate(findings):
        groups.setdefault(find(i), []).append(f)
    return sorted(groups.values(), key=lambda g: min(f.at for f in g))
