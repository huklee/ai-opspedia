"""운영 메타데이터 CSV 커넥터 (dummy/yunbin: GPCC 카드 추천 · 서비스 3f).

폴더 구조
  ops_meta/            wf_task_master (워크플로 · 태스크) · task_data_mapping (태스크 입출력 = 리니지)
  batch_history/       batch_master (기대 시각 · 지연 허용) · workflow_run_history · task_run_history
  ingest_aggregation/  ingest_data_master · ingest_load_status · ingest_metric_agg · query_load_status · query_metric_stats
  behavior_log/        behavior_pattern_def · behavior_pattern_agg

지식 모델로 매핑 (기존 파이프라인이 그대로 합성)
  워크플로  → dag_code + dag_rest   (태스크 · 입출력 테이블 · 공유 S3 로 이어진 업스트림 · SLA · 실행 이력)
  테이블    → table                 (파티션 · 원천 담당 · 적재 행 수 · 품질 지표)
  지표      → metric_category + metric (파이프라인 · 행동 로그 일별 시계열)
  행동 패턴 → manual                (패턴 정의 · 수집 누락)
  이상 징후 → incident + incident_bundle (ops_findings 규칙 탐지 → 알림 형태 근거 → 6단계 장애 파서)
모든 RawItem 의 payload['raw'] 는 근거가 된 CSV 원문 행 → 엔티티 추출 추적이 원본과 대조됨.
"""
from __future__ import annotations

import csv
import hashlib
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..model import RawItem
from ..synthesis.ops_findings import KST, Finding, detect, group, sla_window

FILES = {
    "wf_task_master": "ops_meta/wf_task_master.csv",
    "task_data_mapping": "ops_meta/task_data_mapping.csv",
    "batch_master": "batch_history/batch_master.csv",
    "workflow_run_history": "batch_history/workflow_run_history.csv",
    "task_run_history": "batch_history/task_run_history.csv",
    "ingest_data_master": "ingest_aggregation/ingest_data_master.csv",
    "ingest_load_status": "ingest_aggregation/ingest_load_status.csv",
    "ingest_metric_agg": "ingest_aggregation/ingest_metric_agg.csv",
    "query_load_status": "ingest_aggregation/query_load_status.csv",
    "query_metric_stats": "ingest_aggregation/query_metric_stats.csv",
    "behavior_pattern_def": "behavior_log/behavior_pattern_def.csv",
    "behavior_pattern_agg": "behavior_log/behavior_pattern_agg.csv",
}
# 워크플로 이름의 단어 → 역할 (규칙 기반 설명, LLM 없음)
ROLE_WORDS = [("mthly_execute_trigger", "월간 실행 트리거"), ("mthly_rec_result", "월간 추천 결과 생성"),
              ("item_feature", "아이템 피처 생성"), ("stage2_select", "추천 후보 선택"),
              ("interaction", "유저-아이템 상호작용 피처"), ("post_proc", "추천 결과 후처리 · 적재"),
              ("train_dq_mail", "학습 데이터 품질 메일"), ("select_dq_mail", "후보 선택 품질 메일"),
              ("dq_mail", "데이터 품질 메일"), ("train", "추천 모델 학습"), ("dd_transfer", "추천 결과 외부 전송"),
              ("mark_success", "일일 완료 표시"), ("duplicate", "추천 결과 중복 검사"),
              ("log_agg", "행동 로그 집계"), ("svc_sg", "서비스 세그먼트 생성")]
RULE_LABEL = {"workflow_failed": "WorkflowFailed", "sla_delay": "SlaDelay", "run_missing": "RunMissing",
              "volume_drop": "VolumeDrop", "quality_outlier": "QualityOutlier", "behavior_drop": "BehaviorDrop"}
RULE_KO = {"workflow_failed": "실패", "sla_delay": "SLA 지연", "run_missing": "실행 누락",
           "volume_drop": "적재량 급감", "quality_outlier": "품질 이상", "behavior_drop": "행동 로그 급감"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def load_tables(root: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """CSV → 행 목록. 각 행에 _file · _line(1 = 헤더) · _raw(원문 한 줄)."""
    tables, digests = {}, {}
    for key, rel in FILES.items():
        text = (root / rel).read_text(encoding="utf-8-sig")
        digests[key] = _sha(text.encode())
        lines = text.splitlines()
        rows = []
        for n, rec in enumerate(csv.DictReader(lines), start=2):
            rec = {k: (v if v != "" else None) for k, v in rec.items()}
            rec.update(_file=rel, _line=n, _raw=lines[n - 1])
            rows.append(rec)
        tables[key] = rows
    return tables, digests


def table_of(db: str | None, table: str | None) -> str:
    return f"{db}.{table}".lower()


def _asset(r: dict[str, Any]) -> tuple[str, str]:
    """(kind, 이름): 테이블이면 db.table, 아니면 S3 경로."""
    if r.get("table_name"):
        return "table", table_of(r["database_name"], r["table_name"])
    return "s3", (r.get("s3_path") or "").rstrip("/")


def _role(wf: str) -> str:
    low = wf.lower()
    return next((ko for key, ko in ROLE_WORDS if key in low), "워크플로")


def _raw(rows: Iterable[dict[str, Any]], header_of: dict[str, str]) -> str:
    """근거 행을 파일별로 헤더와 함께 묶은 원문."""
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(r["_file"], []).append(f"{r['_line']}: {r['_raw']}")
    return "\n\n".join(f"── {f} ──\n{header_of[f]}\n" + "\n".join(v) for f, v in by.items())


def _iso(s: str) -> str:
    return s.replace(" ", "T")[:19] + KST


class OpsMetaCsv:
    def __init__(self, name: str, path: Path, service: str = "3f", system: str = "gpcc", team: str = "ds2t", **_: Any):
        self.name, self.path, self.service, self.system, self.team = name, path, service, system, team

    def fetch(self) -> Iterable[RawItem]:
        t, dig = load_tables(self.path)
        header = {rel: (self.path / rel).read_text(encoding="utf-8-sig").splitlines()[0] for rel in FILES.values()}
        wtm, tdm, bm = t["wf_task_master"], t["task_data_mapping"], t["batch_master"]
        wrh, trh = t["workflow_run_history"], t["task_run_history"]
        workflows = sorted({r["workflow_id"] for r in wtm} | {r["workflow_id"] for r in wrh}, key=str.lower)
        fetched = max(r["end_time"] for r in wrh)

        # ---- 리니지: 태스크별 입력 · 출력, 공유 S3 경로로 이어진 워크플로 ----
        producers: dict[str, set[str]] = {}
        for r in tdm:
            kind, name = _asset(r)
            if r["asset_role"] == "output" and name:
                producers.setdefault(name, set()).add(r["workflow_id"])
        upstream: dict[str, set[str]] = {}
        for r in tdm:
            kind, name = _asset(r)
            if r["asset_role"] == "input" and kind == "s3":
                for p in producers.get(name, set()) - {r["workflow_id"]}:
                    upstream.setdefault(r["workflow_id"], set()).add(p)
        self.lineage = {"producers": producers, "upstream": upstream}

        master = {r["workflow_id"]: r for r in bm if r["check_level"] == "workflow"}
        for wf in workflows:
            tasks_rows = sorted((r for r in wtm if r["workflow_id"] == wf), key=lambda r: int(r["task_order"]))
            maps = [r for r in tdm if r["workflow_id"] == wf]
            runs = sorted((r for r in wrh if r["workflow_id"] == wf), key=lambda r: r["start_time"], reverse=True)
            m = master.get(wf)
            tasks = []
            for r in tasks_rows:
                mine = [x for x in maps if x["task_id"] == r["task_id"]]
                task = {"task_id": r["task_id"], "operator": r["task_type"], "line": r["_line"],
                        "lineage": "task_data_mapping",
                        "reads": sorted({_asset(x)[1] for x in mine if x["asset_role"] == "input" and _asset(x)[0] == "table"}),
                        "writes": sorted({_asset(x)[1] for x in mine if x["asset_role"] == "output" and _asset(x)[0] == "table"}),
                        "s3_reads": sorted({_asset(x)[1] for x in mine if x["asset_role"] == "input" and _asset(x)[0] == "s3"}),
                        "s3_writes": sorted({_asset(x)[1] for x in mine if x["asset_role"] == "output" and _asset(x)[0] == "s3"})}
                ups = sorted({p for s in task["s3_reads"] for p in producers.get(s, set()) - {wf}})
                if ups:
                    task["upstream_dags"] = [{"dag": p, "via": next(s for s in task["s3_reads"] if p in producers.get(s, set()))} for p in ups]
                tasks.append(task)
            # SLA 이력
            sla_rows = []
            for r in runs:
                if not m:
                    break
                st, et = datetime.fromisoformat(r["start_time"].replace(" ", "T")[:19]), datetime.fromisoformat(r["end_time"].replace(" ", "T")[:19])
                _, ee = sla_window(st, m["expected_start_time"], m["expected_end_time"])
                late = round((et - ee).total_seconds() / 60, 1)
                sla_rows.append({"base_dt": r["base_dt"], "late_min": late, "violated": late > float(m["delay_threshold_min"]),
                                 "status": r["run_status"]})
            durations = [float(r["duration_sec"]) for r in runs if r["run_status"] == "success"]
            sla = {"cycle": m["batch_cycle"] if m else "미등록", "expected_start": m["expected_start_time"] if m else None,
                   "expected_end": m["expected_end_time"] if m else None,
                   "threshold_min": float(m["delay_threshold_min"]) if m else None,
                   "runs": len(runs), "failed": sum(r["run_status"] == "failed" for r in runs),
                   "manual": sum(r["run_type"] == "manual" for r in runs),
                   "violations": [x for x in sla_rows if x["violated"]],
                   "p50_duration_min": round(statistics.median(durations) / 60, 1) if durations else None,
                   "max_duration_min": round(max(durations) / 60, 1) if durations else None,
                   "task_errors": [{"task_id": e["task_id"], "base_dt": e["base_dt"], "try": e["try_number"],
                                    "error": e["error_message"]} for e in trh if e["workflow_id"] == wf and e["error_message"]]}
            n_in = len({x for tk in tasks for x in tk["reads"]})
            n_out = len({x for tk in tasks for x in tk["writes"]})
            desc = (f"GPCC(서비스 {self.service}) {_role(wf)} · 태스크 {len(tasks)}개 · 입력 테이블 {n_in}개 · 출력 테이블 {n_out}개"
                    + (f" · {m['batch_cycle']} {m['expected_start_time'][:5]} 시작 예정" if m else " · 배치 마스터 미등록"))
            code_rows = tasks_rows + maps + ([m] if m else [])
            yield RawItem(self.name, "dag_code", wf, {
                "dag_id": wf, "description": desc,
                "schedule": f"{m['batch_cycle']} {m['expected_start_time']}–{m['expected_end_time']} KST" if m else "미등록",
                "tags": [self.system, self.service], "owner": next((r["owner_team"] for r in tasks_rows if r.get("owner_team")), self.team), "retries": 0, "tasks": tasks,
                "deps": [[a["task_id"], b["task_id"]] for a, b in zip(tasks, tasks[1:])],
                "dag_airflow_id": tasks_rows[0]["dag_id"] if tasks_rows else None, "sla": sla, "source_kind": "ops_csv",
                "raw": _raw(code_rows, header)}, f"yunbin:ops_meta/wf_task_master.csv#{wf}@{dig['wf_task_master']}")
            yield RawItem(self.name, "dag_rest", wf, {
                "dag_id": wf, "is_paused": False, "owners": [self.team], "tags": [{"name": self.system}],
                "timetable_description": (f"{m['batch_cycle']} · {m['expected_start_time']} 시작 · {m['expected_end_time']} 종료 예정 · 지연 허용 {m['delay_threshold_min']}분" if m else "배치 마스터 미등록"),
                "runs": [{"state": r["run_status"], "start_date": _iso(r["start_time"]), "end_date": _iso(r["end_time"]),
                          "run_type": r["run_type"], "base_dt": r["base_dt"]} for r in runs[:14]],
                "fetched_at": _iso(fetched), "raw": _raw(runs[:14], header)},
                f"yunbin:batch_history/workflow_run_history.csv#{wf}@{dig['workflow_run_history']}")

        # ---- 테이블 ----
        idm = t["ingest_data_master"]
        ils, ima = t["ingest_load_status"], t["ingest_metric_agg"]
        names = {table_of(r["database_name"], r["table_name"]) for r in idm}
        names |= {_asset(r)[1] for r in tdm if _asset(r)[0] == "table"}
        for name in sorted(names):
            db, tb = name.split(".", 1)
            src = [r for r in idm if table_of(r["database_name"], r["table_name"]) == name]
            mp = [r for r in tdm if _asset(r) == ("table", name)]
            loads = sorted((r for r in ils if table_of(r["database_name"], r["table_name"]) == name), key=lambda r: r["base_dt"])
            mets = [r for r in ima if table_of(r["database_name"], r["table_name"]) == name]
            part = next((r["partition_col"] for r in src + mp if r.get("partition_col")), None)
            owner = next((r["owner_team"] for r in src if r.get("owner_team")), None)
            writers = sorted({r["workflow_id"] for r in mp if r["asset_role"] == "output"})
            load = None
            if loads:
                vals = [float(r["row_count"]) for r in loads]
                med = statistics.median(vals)
                load = {"days": len(loads), "median": med, "min": min(vals), "max": max(vals),
                        "latest": vals[-1], "latest_dt": loads[-1]["base_dt"],
                        "drops": [{"base_dt": r["base_dt"], "rows": float(r["row_count"]), "ratio": round(float(r["row_count"]) / med, 3)}
                                  for r in loads if med and float(r["row_count"]) < 0.5 * med],
                        "series": [[r["base_dt"], float(r["row_count"])] for r in loads]}
            quality = []
            for (mn, mc) in sorted({(r["metric_name"], r["metric_col"]) for r in mets}):
                g = sorted((r for r in mets if r["metric_name"] == mn and r["metric_col"] == mc), key=lambda r: r["base_dt"])
                vals = [float(r["metric_value"]) for r in g]
                quality.append({"metric": mn, "col": mc, "median": statistics.median(vals), "latest": vals[-1],
                                "max": max(vals), "min": min(vals), "days": len(g)})
            comment = (f"원천 테이블 · 담당 {owner}" if owner else
                       f"GPCC 산출 테이블 · 쓰기 {', '.join(writers)}" if writers else "GPCC 참조 테이블")
            evid = src + mp[:12] + loads[-5:] + mets[:6]
            yield RawItem(self.name, "table", name, {
                "name": name, "comment": comment,
                "columns": [{"name": q["col"], "type": "", "comment": "품질 지표 수집 컬럼"} for q in quality if q["metric"] == "distinct_count"],
                "partitions": [part] if part else [], "source_owner": owner,
                "partition_rule": next((f"{r['partition_base_unit']} {float(r['partition_start_offset']):+.0f} ~ {float(r['partition_end_offset']):+.0f}"
                                        for r in src + mp if r.get("partition_base_unit") and r.get("partition_start_offset")), None),
                "load": load, "quality": quality, "source_kind": "ops_csv",
                "raw": _raw(evid, header) if evid else ""}, f"yunbin:ingest_aggregation#{name}@{dig['ingest_load_status']}")

        # ---- 지표 ----
        yield from self._metrics(t, dig, fetched)
        # ---- 행동 패턴 정의 문서 ----
        yield self._behavior_doc(t, dig, header)
        # ---- 이상 징후 → 장애 ----
        yield from self._incidents(t, dig)

    # ------------------------------------------------------------------
    def _metrics(self, t, dig, fetched) -> Iterable[RawItem]:
        def epoch(d: str) -> int:
            return int(datetime.fromisoformat(f"{d}T09:00:00{KST}").timestamp())
        days = sorted({r["base_dt"] for r in t["workflow_run_history"]})
        cats = [
            {"id": "gpcc-pipeline", "icon": "🏭", "title": "GPCC 파이프라인", "team": self.team,
             "description": "GPCC 카드 추천 배치의 성공률 · SLA 지연 · 핵심 테이블 적재량 (yunbin 운영 메타데이터)"},
            {"id": "gpcc-behavior", "icon": "📱", "title": "GPCC 사용자 행동", "team": self.team,
             "description": "GPCC 화면의 노출 · 클릭 · 전환 · 페이지뷰 일별 합계와 클릭률 (behavior_pattern_agg)"},
        ]
        origin_cat = f"yunbin:catalog@{dig['batch_master']}"
        for c in cats:
            yield RawItem(self.name, "metric_category", c["id"], c, origin_cat)
        wrh = t["workflow_run_history"]
        master = {r["workflow_id"]: r for r in t["batch_master"] if r["check_level"] == "workflow"}

        def success_rate(d):
            rs = [r for r in wrh if r["base_dt"] == d]
            return round(100 * sum(r["run_status"] == "success" for r in rs) / len(rs), 1) if rs else None

        def sla_count(d):
            n = 0
            for r in wrh:
                m = master.get(r["workflow_id"])
                if r["base_dt"] != d or not m:
                    continue
                st, et = (datetime.fromisoformat(r[k].replace(" ", "T")[:19]) for k in ("start_time", "end_time"))
                _, ee = sla_window(st, m["expected_start_time"], m["expected_end_time"])
                n += (et - ee).total_seconds() / 60 > float(m["delay_threshold_min"])
            return float(n)

        def table_rows(name, scale):
            m = {r["base_dt"]: float(r["row_count"]) / scale for r in t["ingest_load_status"]
                 if table_of(r["database_name"], r["table_name"]) == name}
            return lambda d: m.get(d)

        def distinct(name, col, scale):
            m = {r["base_dt"]: float(r["metric_value"]) / scale for r in t["ingest_metric_agg"]
                 if table_of(r["database_name"], r["table_name"]) == name and r["metric_col"] == col and r["metric_name"] == "distinct_count"}
            return lambda d: m.get(d)
        beh: dict[str, dict[str, float]] = {}
        for r in t["behavior_pattern_agg"]:
            beh.setdefault(r["base_dt"], {}).setdefault(r["event_type"], 0.0)
            beh[r["base_dt"]][r["event_type"]] += float(r["event_count"])

        def ev(kind, scale=1.0):
            return lambda d: (beh.get(d, {}).get(kind, 0.0) / scale) if d in beh else None

        def ctr(d):
            b = beh.get(d)
            return round(100 * b["click"] / b["imp"], 3) if b and b.get("imp") else None
        rec, trgt, logt = ("wdp_bdp_db_dlk_l1_msk_view.ft_svc_f13_rec_result", "wdp_bdp_db_dlk_l2_msk_view.f13_svc_gpcc_trgt",
                           "wdp_bdp_db_dlk_l1_msk_view.hcc_cust_bhve_data")
        specs = [
            ("gpcc_success_rate", "gpcc-pipeline", "워크플로 성공률", "%", 1, "higher", success_rate, 95.0, ">=", "workflow_run_history (success / 전체)", ["system:gpcc"]),
            ("gpcc_sla_violations", "gpcc-pipeline", "SLA 지연 워크플로 수", "건", 0, "lower", sla_count, 0.0, "<=", "batch_master 기대 종료 + 허용 지연 초과", ["system:gpcc"]),
            ("gpcc_rec_result_rows", "gpcc-pipeline", "추천 결과 적재 행 수", "백만 행", 1, "higher", table_rows(rec, 1e6), None, ">=", "ingest_load_status · ft_svc_f13_rec_result", [f"table:{rec}", "dag:GPCC_stage4_post_proc"]),
            ("gpcc_target_users", "gpcc-pipeline", "추천 대상 고객 수", "만 명", 1, "higher", distinct(trgt, "csno", 1e4), None, ">=", "ingest_metric_agg · f13_svc_gpcc_trgt distinct(csno)", [f"table:{trgt}"]),
            ("gpcc_behavior_log_rows", "gpcc-pipeline", "행동 로그 원천 적재 행 수", "백만 행", 1, "higher", table_rows(logt, 1e6), None, ">=", "ingest_load_status · hcc_cust_bhve_data", [f"table:{logt}", "dag:temp_log_agg"]),
            ("gpcc_imp", "gpcc-behavior", "일별 노출", "만 건", 1, "higher", ev("imp", 1e4), None, ">=", "behavior_pattern_agg · event_type=imp 합계", ["dag:temp_log_agg"]),
            ("gpcc_click", "gpcc-behavior", "일별 클릭", "건", 0, "higher", ev("click"), None, ">=", "behavior_pattern_agg · event_type=click 합계", ["dag:temp_log_agg"]),
            ("gpcc_conv", "gpcc-behavior", "일별 전환", "건", 0, "higher", ev("conv"), None, ">=", "behavior_pattern_agg · event_type=conv 합계", ["dag:temp_log_agg"]),
            ("gpcc_pv", "gpcc-behavior", "일별 페이지뷰", "만 건", 1, "higher", ev("pv", 1e4), None, ">=", "behavior_pattern_agg · event_type=pv 합계", ["dag:temp_log_agg"]),
            ("gpcc_ctr", "gpcc-behavior", "클릭률 (클릭 / 노출)", "%", 3, "higher", ctr, None, ">=", "behavior_pattern_agg · click / imp", ["dag:temp_log_agg"]),
        ]
        for mid, cat, title, unit, dec, better, fn, slo, op, source, producer in specs:
            vals = [[epoch(d), fn(d)] for d in days]
            nums = [v for _, v in vals if v is not None]
            if slo is None:  # 기준값이 없는 지표: 31일 중앙값의 60 % (규칙, 문서화)
                slo = round(0.6 * statistics.median(nums), dec) if nums else 0
            yield RawItem(self.name, "metric", mid, {
                "id": mid, "category": cat, "title": title, "unit": unit, "decimals": dec, "better": better,
                "slo": {"op": op, "value": slo}, "source": source, "producer": producer,
                "description": f"{title} — {source}. 기준은 {'고정값' if mid in ('gpcc_success_rate', 'gpcc_sla_violations') else '31일 중앙값의 60 % (데이터에서 산출)'}",
                "values": vals, "fetched_at": _iso(fetched)},
                f"yunbin:{source.split(' ')[0]}#{mid}@{_sha(json.dumps(vals).encode())}")

    def _behavior_doc(self, t, dig, header) -> RawItem:
        defs, agg = t["behavior_pattern_def"], t["behavior_pattern_agg"]
        tot: dict[tuple[str, str, str], float] = {}
        for r in agg:
            k = (r["event_type"], r["page_cd"], r["area_cd"])
            tot[k] = tot.get(k, 0) + float(r["event_count"])
        lines = ["# GPCC 행동 로그 패턴 정의", "",
                 f"> 문서 ID RB-GPCC-001 · 담당 {self.team} · behavior_pattern_def {len(defs)}건 · behavior_pattern_agg 31일 집계로 자동 생성", "",
                 "화면(page) · 영역(area) · 이벤트(pv · imp · click · conv)별 로그 패턴과 31일 누적 이벤트 수.", "",
                 "## 패턴 정의와 수집 현황", "", "| 이벤트 | 페이지 | 영역 | 패턴 | 31일 이벤트 |", "|---|---|---|---|---|"]
        gaps = []
        for r in defs:
            k = (r["event_type"], r["page_id"], r["area_cd"])
            n = tot.get(k)
            if n is None:
                gaps.append(r)
            lines.append(f"| {r['event_type']} | {r['page_id']} | {r['area_cd']} | `{r['pattern_expr']}` | {f'{n:,.0f}' if n is not None else '**수집 없음**'} |")
        lines += ["", "## 수집 누락 (정의는 있으나 집계 0건)", ""]
        lines += [f"- `{g['event_type']}` · {g['page_id']} · {g['area_cd']} — 패턴 `{g['pattern_expr']}`" for g in gaps] or ["- 없음"]
        funnel = {}
        for (e, p, a), n in tot.items():
            funnel.setdefault(p, {}).setdefault(e, 0.0)
            funnel[p][e] += n
        lines += ["", "## 페이지별 퍼널 (31일 합계)", "", "| 페이지 | 노출 | 클릭 | 전환 | 페이지뷰 | 클릭률 |", "|---|---|---|---|---|---|"]
        for p, f in sorted(funnel.items(), key=lambda x: -x[1].get("imp", 0)):
            imp = f.get("imp", 0)
            lines.append(f"| {p} | {imp:,.0f} | {f.get('click', 0):,.0f} | {f.get('conv', 0):,.0f} | {f.get('pv', 0):,.0f} | "
                         + (f"{100 * f.get('click', 0) / imp:.2f}%" if imp else "—") + " |")
        lines += ["", "## 관련", "", "- 집계 워크플로 temp_log_agg · 원천 wdp_bdp_db_dlk_l1_msk_view.hcc_cust_bhve_data"]
        md = "\n".join(lines) + "\n"
        return RawItem(self.name, "manual", "gpcc-behavior-patterns", {"markdown": md, "file": FILES["behavior_pattern_def"],
                       "raw": _raw(defs, header)}, f"yunbin:{FILES['behavior_pattern_def']}@{dig['behavior_pattern_def']}")

    def _incidents(self, t, dig) -> Iterable[RawItem]:
        findings = detect(t, table_of)
        tdm = t["task_data_mapping"]
        edges: set[tuple[str, str]] = set()
        for r in tdm:
            kind, name = _asset(r)
            if kind == "table":
                edges.add((f"dag:{r['workflow_id']}", f"table:{name}"))
        for r in t["ingest_data_master"]:
            if r["table_name"]:
                edges.add((f"dag:{r['workflow_id']}", f"table:{table_of(r['database_name'], r['table_name'])}"))
        # 워크플로 ↔ 워크플로: S3 경로 또는 테이블을 한쪽이 쓰고 다른 쪽이 읽음
        for c, ps in self.lineage["upstream"].items():
            for p in ps:
                edges.add((f"dag:{p}", f"dag:{c}"))
        for r in tdm:
            kind, name = _asset(r)
            if r["asset_role"] == "input" and kind == "table":
                for p in self.lineage["producers"].get(name, set()) - {r["workflow_id"]}:
                    edges.add((f"dag:{p}", f"dag:{r['workflow_id']}"))

        def adjacent(a: str, b: str) -> bool:
            return (a, b) in edges or (b, a) in edges
        self.findings = findings
        def same_day(a, b) -> bool:
            # 같은 날의 실패 ↔ 실행 누락, 같은 날 여러 곳의 적재량 · 품질 · 행동 로그 급감 (한 원천의 전파)
            if a.base_dt != b.base_dt:
                return False
            data = {"volume_drop", "quality_outlier", "behavior_drop"}
            return {a.rule, b.rule} == {"run_missing", "workflow_failed"} or (a.rule in data and b.rule in data)
        groups = group(findings, adjacent, same_day=same_day)
        keys: set[str] = set()
        for g in groups:
            if all(f.rule == "sla_delay" and f.severity == "warn" for f in g):
                continue  # 단독 소규모 지연은 장애가 아닌 DAG 페이지의 SLA 이력으로만
            g = sorted(g, key=lambda f: f.at)
            first = min(g, key=lambda f: (f.severity != "crit", f.at))
            day = min(f.base_dt for f in g)
            key = f"YB-{day[5:7]}{day[8:10]}"
            while key in keys:
                key += "b"
            keys.add(key)
            counts: dict[str, int] = {}
            for f in g:
                counts[f.rule] = counts.get(f.rule, 0) + 1
            ents = sorted({e for f in g for e in f.entities})
            label = " · ".join(f"{RULE_KO[k]} {v}" for k, v in counts.items())
            summary = f"[자동 탐지] {day[5:]} {first.entities[0].split(':', 1)[1]} {RULE_KO[first.rule]} 외 — {label}"
            desc = (f"yunbin 운영 메타데이터 규칙 탐지 {len(g)}건이 날짜 · 리니지로 묶임. 관련 엔티티 {len(ents)}개. "
                    f"첫 탐지 {first.at[5:16].replace('T', ' ')} {first.summary}")
            prio = "P2" if any(f.severity == "crit" for f in g) else "P3"
            jira = {"key": key, "fields": {
                "summary": summary, "status": {"name": "Detected"}, "priority": {"name": prio},
                "created": min(f.at for f in g), "resolutiondate": None,
                "labels": [self.system] + [e.split(":", 1)[1].split(".")[-1] for e in ents][:8],
                "assignee": {"displayName": "자동 탐지 (yunbin)"}, "description": desc,
                "customfield_root_cause": "", "customfield_resolution": "", "comment": {"comments": []}}}
            alerts = {"alerts": [{
                "source": f"yunbin-detector:{f.rows[0][0] if f.rows else 'rule'}", "receivedAt": f.at,
                "labels": {"alertname": RULE_LABEL[f.rule], "dag_id": f.entities[0].split(":", 1)[1], "severity": f.severity},
                "annotations": {"summary": f.summary + (" · 근거 " + ", ".join(f"{p}:{n}" for p, n, _ in f.rows[:3]) if f.rows else "")}}
                for f in g]}
            evidence_rows = "\n".join(f"- {p}:{n} — {raw}" for f in g for p, n, raw in f.rows[:3])  # 코드 표기 없음: 명령어로 오인하지 않게
            files = {"alerts.json": json.dumps(alerts, ensure_ascii=False, indent=1),
                     "postmortem.md": f"# {key} 탐지 근거 (자동 생성)\n\n## 탐지 규칙\n\n"
                                      + "\n".join(f"- {RULE_KO[f.rule]}: {f.summary}" for f in g)
                                      + f"\n\n## 원본 행\n\n{evidence_rows}\n"}
            origin = f"yunbin-detector:{key}#{_sha(json.dumps(alerts).encode())}"
            yield RawItem(self.name, "incident", key, {**jira, "raw": json.dumps(jira, ensure_ascii=False)}, origin)
            yield RawItem(self.name, "incident_bundle", key, {"files": files}, f"bundle:{key}/alerts.json+postmortem.md#{_sha(json.dumps(files).encode())}")
