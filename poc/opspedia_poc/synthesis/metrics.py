"""지표 평가: 최신값 · 7일 평균 대비 변화 · SLO 판정 · 이상 탐지(z-score). 결정적 규칙만."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))


def fmt(v: float | None, m: dict[str, Any]) -> str:
    if v is None:
        return "—"
    if m["unit"] == "시":
        h = int(v)
        return f"{h:02d}:{round((v - h) * 60):02d}"
    return f"{v:,.{m.get('decimals', 1)}f}{'' if m['unit'] in ('%',) else ' '}{m['unit']}"


def _ok(v: float, slo: dict[str, Any]) -> bool:
    return v >= slo["value"] if slo["op"] == ">=" else v <= slo["value"]


def evaluate(m: dict[str, Any]) -> dict[str, Any]:
    pts = [(t, v) for t, v in m["values"]]
    vals = [v for _, v in pts if v is not None]
    latest = pts[-1][1] if pts else None
    hist = [v for _, v in pts[-8:-1] if v is not None]
    avg7 = statistics.mean(hist) if hist else None
    delta = ((latest - avg7) / avg7 * 100) if latest is not None and avg7 else None
    slo = m["slo"]
    if latest is None:
        state, reason = "nodata", "오늘 값 없음 (산출 DAG 미완료)"
    elif not _ok(latest, slo):
        state, reason = "breach", f"SLO {slo['op']} {fmt(slo['value'], m)} 위반"
    else:
        margin = abs(latest - slo["value"]) / (abs(slo["value"]) or 1)
        state, reason = ("near", "SLO 경계 5 % 이내") if margin < 0.05 else ("ok", "SLO 충족")
    anomaly = False
    if len(hist) >= 5 and latest is not None:
        sd = statistics.pstdev(hist)
        anomaly = sd > 0 and abs(latest - avg7) / sd > 3
    icon = {"breach": "🔴", "nodata": "⛔", "near": "⚠️", "ok": "✅"}[state]
    return {"latest": latest, "latest_fmt": fmt(latest, m), "avg7": avg7, "avg7_fmt": fmt(avg7, m),
            "delta_pct": None if delta is None else round(delta, 1),
            "delta_good": None if delta is None else ((delta >= 0) == (m.get("better") == "higher")), "state": state, "reason": reason,
            "anomaly": anomaly, "icon": icon, "slo_fmt": f"{slo['op']} {fmt(slo['value'], m)}",
            "points": [{"t": datetime.fromtimestamp(t, KST).strftime("%m-%d"), "v": v} for t, v in pts],
            "min": min(vals) if vals else None, "max": max(vals) if vals else None}
