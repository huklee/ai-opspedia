"""지난 실행 이력 재현: dummy 를 실행 시점 상태로 되돌린 복사본에서 실제 파이프라인을 순서대로 실행.

버전 · diff · 합성 내역은 전부 실제 파이프라인 결과 (가짜 로그 아님). 실행 시각만 과거로 기록.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .pipeline import run

FINAL_DAY = "2026-09-29"


def _apply(root: Path, op: dict[str, Any]) -> None:
    if "delete" in op:
        t = root / op["delete"]
        shutil.rmtree(t) if t.is_dir() else t.unlink(missing_ok=True)
        return
    p = root / op["file"]
    s = p.read_text(encoding="utf-8")
    if "replace" in op:
        old, new = op["replace"]
        assert old in s, f"patch target not found in {op['file']}: {old[:40]}"
        s = s.replace(old, new)
    if "cut_between" in op:
        a, b = op["cut_between"]
        i, j = s.index(a), s.index(b)
        s = s[:i] + s[j:]
    p.write_text(s, encoding="utf-8")


def _airflow_for(root: Path, at: datetime) -> None:
    """실행 시각 기준 Airflow 상태: 그 시각 이전 마지막 정기 실행은 모두 성공 (최종일은 원본 유지)."""
    p = root / "airflow.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["fetched_at"] = (at - timedelta(seconds=30)).isoformat()
    if at.date().isoformat() < FINAL_DAY:
        for dag, runs in d["dag_runs"].items():
            if dag == "query_suggest_build":
                continue  # 09-13 이후 일시정지
            s0, e0 = datetime.fromisoformat(runs[0]["start_date"]), datetime.fromisoformat(runs[0]["end_date"])
            day = at.date() if s0.time() < at.time() else (at - timedelta(days=1)).date()
            if "hourly" in dag:
                s = at.replace(minute=15, second=1) - (timedelta(hours=1) if at.minute < 30 else timedelta(0))
            else:
                s = datetime.combine(day, s0.time(), s0.tzinfo)
            runs[:] = [{"state": "success", "start_date": s.isoformat(), "end_date": (s + (e0 - s0)).isoformat()}]
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _search_for(root: Path, at: datetime) -> None:
    """실행 시각 기준 인덱스 상태: 최종일 전에는 products · reco-feed 가 정상 전환된 상태."""
    p = root / "search.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["fetched_at"] = (at - timedelta(seconds=20)).isoformat()
    if at.date().isoformat() < FINAL_DAY:
        tz = at.tzinfo
        build_day = at.date() if at.hour >= 4 else (at - timedelta(days=1)).date()
        n = 41 - (datetime(2026, 9, 28).date() - build_day).days
        prods = [{"index": f"products_v{v}", "health": "green", "docs_count": 1_198_000 + v * 150,
                  "store_size": "3.1gb", "creation_date": datetime.combine(dday, datetime.min.time(), tz).replace(hour=3, minute=45).isoformat(),
                  "mappings": {"title": "text(nori)", "brand": "keyword", "price": "scaled_float", "in_stock": "boolean"}}
                 for v, dday in ((n - 1, build_day - timedelta(days=1)), (n, build_day))]
        feed_day = at.date() if at.hour >= 7 else (at - timedelta(days=1)).date()
        feeds = [{"index": f"reco-feed-{dd:%Y.%m.%d}", "health": "green", "docs_count": 3_050_000 + dd.day * 2_000,
                  "store_size": "5.7gb", "creation_date": datetime.combine(dd, datetime.min.time(), tz).replace(hour=6, minute=18).isoformat(),
                  "mappings": {"user_id": "long", "items": "long"}}
                 for dd in (feed_day - timedelta(days=1), feed_day)]
        suggest = [i for i in d["indices"] if i["index"].startswith("query-suggest")]
        d["indices"] = prods + feeds + suggest
        d["aliases"] = {"products": prods[-1]["index"], "reco-feed": feeds[-1]["index"], "query-suggest": suggest[0]["index"]}
        d["cluster_health"] = "green"
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _metrics_until(root: Path, at: datetime) -> None:
    p = root / "metrics/series.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    cut = at.timestamp()
    for r in d["data"]["result"]:
        r["values"] = [v for v in r["values"] if v[0] <= cut]
    d["fetched_at"] = at.isoformat()
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def seed(cfg: Settings, backends: tuple[str, ...] = ("opensearch",)) -> list[dict[str, Any]]:
    spec = yaml.safe_load((cfg.root / "dummy/history.yaml").read_text(encoding="utf-8"))
    src_root = cfg.root / "dummy"
    out = []
    runs = spec["runs"]
    for i, r in enumerate(runs):
        at = datetime.fromisoformat(r["at"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dummy"
            shutil.copytree(src_root, root)
            for ch in spec["changes"].values():
                if at < datetime.fromisoformat(ch["since"]):
                    for op in ch["undo"]:
                        _apply(root, op)
            _airflow_for(root, at)
            _search_for(root, at)
            _metrics_until(root, at)
            sources = [{**s, "path": str(root / Path(s["path"]).relative_to("dummy"))} if s["path"].startswith("dummy/") else s
                       for s in cfg.sources]
            c = cfg.model_copy(update={"sources": sources, "now": at.isoformat()})
            last = i == len(runs) - 1
            out.append(run(c, backends if last else None, job=r["job"], trigger=r["trigger"], actor=r.get("actor"),
                           at=r["at"], fail=set(r.get("fail", [])), note=r.get("note", "")))
    return out
