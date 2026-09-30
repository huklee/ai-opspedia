"""인덱스 패밀리 상태 점검 (ADR-016). 결정적 규칙만."""
from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def check_family(fam: dict[str, Any], meta: dict[str, Any], builder: dict[str, Any] | None, now: str,
                 swappers: list[dict[str, Any]] = ()) -> dict[str, Any]:
    versions = sorted((i for i in meta["indices"] if fnmatch.fnmatch(i["index"], fam["pattern"])),
                      key=lambda i: i["creation_date"])
    target = meta["aliases"].get(fam["alias"])
    cur = next((v for v in versions if v["index"] == target), None)
    newest = versions[-1] if versions else None
    checks: list[dict[str, str]] = []

    def add(level: str, msg: str) -> None:
        checks.append({"level": level, "msg": msg})

    for v in versions:
        if v["health"] == "red":
            add("crit", f"{v['index']} health red")
        elif v["health"] == "yellow":
            add("warn", f"{v['index']} health yellow (레플리카 미할당 가능)")
    if cur is None:
        add("crit", f"alias {fam['alias']} 가 패밀리 인덱스를 가리키지 않음")
    else:
        age_h = (_ts(now) - _ts(cur["creation_date"])).total_seconds() / 3600
        if age_h > fam["freshness_hours"]:
            add("crit", f"alias 대상 {cur['index']} 경과 {age_h:.0f}시간 > 기준 {fam['freshness_hours']}시간")
        if newest and newest["index"] != cur["index"]:
            add("warn", f"alias 가 최신 인덱스 {newest['index']} 가 아닌 {cur['index']} 를 가리킴 (전환 미완료)")
            if cur["docs_count"]:
                drop = 1 - newest["docs_count"] / cur["docs_count"]
                if drop > 0.2:
                    add("crit", f"최신 인덱스 문서 수 {drop:.0%} 감소 ({cur['docs_count']:,} → {newest['docs_count']:,})")
    if builder:
        last = (builder.get("runs") or [{}])[0]
        if builder.get("is_paused"):
            add("warn", f"빌더 DAG {builder['dag_id']} 일시정지(paused)")
        if last.get("state") not in (None, "success"):
            add("crit" if last["state"] == "failed" else "warn", f"빌더 DAG {builder['dag_id']} 최근 실행 {last['state']}")
    for sw in swappers:
        last = (sw.get("runs") or [{}])[0]
        if last.get("state") not in (None, "success"):
            add("crit" if last["state"] == "failed" else "warn", f"alias 전환 DAG {sw['dag_id']} 최근 실행 {last['state']}")
    mapping_diff: dict[str, list[str]] = {}
    if len(versions) >= 2:
        a, b = versions[-2]["mappings"], versions[-1]["mappings"]
        mapping_diff = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
                        "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}
    checks.sort(key=lambda c: c["level"] != "crit")  # 심각한 항목 먼저
    level = "crit" if any(c["level"] == "crit" for c in checks) else "warn" if checks else "ok"
    return {"level": level, "checks": checks, "versions": versions, "alias_target": target,
            "newest": newest["index"] if newest else None, "mapping_diff": mapping_diff}
