"""커넥터 레지스트리 (ADR-017). PoC 는 dummy 파일을 실제 API 응답 형태로 읽음."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from ..adapters.convert import to_markdown
from ..adapters.sql_lineage import lineage, parse_ddl
from ..model import RawItem


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


class DagRepo:
    """DAG 폴더 디렉터리 스캔 (git 미사용, ADR-020). ast 로 태스크·SQL·센서 추출."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        for f in sorted(self.path.glob("*.py")):
            src = f.read_bytes()
            info = parse_dag_file(src.decode("utf-8"))
            if not info.get("dag_id"):
                continue
            info["raw"] = src.decode("utf-8")  # 원문 (생성 이력 "원본 데이터")
            yield RawItem(self.name, "dag_code", info["dag_id"], info, f"{f.name}@{_sha(src)}")


def _const(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [n.id for n in node.elts if isinstance(n, ast.Name)]
    return []


def parse_dag_file(code: str) -> dict[str, Any]:
    tree = ast.parse(code)
    info: dict[str, Any] = {"description": ast.get_docstring(tree) or "", "tasks": [], "deps": []}
    var_task: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DAG":
            kw = {k.arg: _const(k.value) for k in node.keywords}
            info["dag_id"] = kw.get("dag_id")
            info["schedule"] = kw.get("schedule")
            info["tags"] = kw.get("tags") or []
            info["owner"] = (kw.get("default_args") or {}).get("owner")
            info["retries"] = (kw.get("default_args") or {}).get("retries", 0)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            op = getattr(call.func, "id", getattr(call.func, "attr", ""))
            kw = {k.arg: _const(k.value) for k in call.keywords}
            if "task_id" not in kw:
                continue
            task = {"task_id": kw["task_id"], "operator": op, "line": node.lineno}
            for key in ("sql", "external_dag_id", "index_family", "alias", "source_table", "bash_command", "max_doc_drop"):
                if kw.get(key) is not None:
                    task[key] = kw[key]
            if isinstance(task.get("sql"), str):
                reads, writes = lineage(task["sql"])
                task["reads"], task["writes"] = sorted(reads), sorted(writes)
                task["sql"] = "\n".join(line.strip() for line in task["sql"].strip().splitlines())
            for t in node.targets:
                if isinstance(t, ast.Name):
                    var_task[t.id] = kw["task_id"]
            info["tasks"].append(task)
    for node in ast.walk(tree):  # a >> b >> [c, d]
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.BinOp):
            chain: list[list[str]] = []

            def flat(n: ast.AST) -> None:
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift):
                    flat(n.left)
                    flat(n.right)
                else:
                    chain.append(_names(n))
            flat(node.value)
            for left, right in zip(chain, chain[1:]):
                info["deps"] += [[var_task.get(a, a), var_task.get(b, b)] for a in left for b in right]
    return info


class AirflowFixture:
    """Airflow REST /api/v1 dags + dagRuns 응답 형태의 스냅샷."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        at = data["fetched_at"]
        for d in data["dags"]:
            runs = data["dag_runs"].get(d["dag_id"], [])
            h = _sha(json.dumps({**d, "runs": runs}, sort_keys=True).encode())  # 의미 기반 해시 (조회 시각 제외)
            yield RawItem(self.name, "dag_rest", d["dag_id"], {**d, "runs": runs, "fetched_at": at},
                          f"airflow:/api/v1/dags/{d['dag_id']}#{h}")


class Ddl:
    def __init__(self, name: str, path: Path, dialect: str = "hive", **_: Any):
        self.name, self.path, self.dialect = name, path, dialect

    def fetch(self) -> Iterable[RawItem]:
        for f in sorted(self.path.glob("*.sql")):
            text = f.read_text(encoding="utf-8")
            lines = text.splitlines()
            for t in parse_ddl(text, self.dialect):
                line = next((i + 1 for i, ln in enumerate(lines) if t["name"] in ln.lower()), 1)
                t["raw"] = next((st.strip() + ";" for st in text.split(";") if t["name"] in st.lower() and "create" in st.lower()), "")
                yield RawItem(self.name, "table", t["name"], t, f"{f.name}:{line}@{_sha(text.encode())}")


class SearchFixture:
    """ES/OpenSearch _cat/indices · _alias · _mapping 스냅샷."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        h = _sha(json.dumps({k: v for k, v in data.items() if k != "fetched_at"}, sort_keys=True).encode())
        yield RawItem(self.name, "index_meta", data["cluster"], data, f"{data['cluster']}:_cat/indices#{h}")


class IncidentsFixture:
    """Jira REST issue 응답 형태."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        for f in sorted(self.path.glob("*.json")):
            issue = json.loads(f.read_text(encoding="utf-8"))
            yield RawItem(self.name, "incident", issue["key"], issue, f"jira:{issue['key']}")


class Manuals:
    """매뉴얼 · 런북. parser=markitdown (기본: HTML·PDF·Office) | static (자체 정적 변환기: txt · html · Confluence XML)."""

    def __init__(self, name: str, path: Path, parser: str = "markitdown", **_: Any):
        self.name, self.path, self.parser = name, path, parser

    def fetch(self) -> Iterable[RawItem]:
        if not self.path.exists():
            return
        if self.parser == "static":
            from ..adapters.static_convert import convert
            for f in sorted(self.path.iterdir()):
                if f.suffix.lower() not in {".txt", ".html", ".htm", ".xml", ".md"} or f.name.startswith("."):
                    continue
                raw = f.read_text(encoding="utf-8", errors="replace")
                md, report = convert(f.name, raw)
                yield RawItem(self.name, "manual", f.stem, {"markdown": md, "file": f.name, "raw": raw, "conversion": report},
                              f"upload:{f.name}@{_sha(raw.encode())}")
            return
        for f in sorted(self.path.iterdir()):
            if f.suffix == ".md":
                md = f.read_text(encoding="utf-8")
                raw = None
            elif f.suffix in {".html", ".htm", ".pdf", ".docx"}:
                md = to_markdown(f)
                raw = f.read_text(encoding="utf-8", errors="replace") if f.suffix in {".html", ".htm"} else f"(바이너리 {f.suffix})"
            else:
                continue
            yield RawItem(self.name, "manual", f.stem, {"markdown": md, "file": f.name, **({"raw": raw} if raw else {})},
                          f"{f.name}@{_sha(f.read_bytes())}")


class IncidentBundles:
    """장애 원자료 묶음: 알림(Alertmanager · Airflow 콜백) · 슬랙 스레드 export · 포스트모템 (디렉터리 = 티켓 키)."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        for d in sorted(p for p in self.path.iterdir() if p.is_dir()):
            files = {}
            for f in sorted(d.iterdir()):
                files[f.name] = f.read_text(encoding="utf-8")
            blob = "".join(files.values()).encode()
            yield RawItem(self.name, "incident_bundle", d.name, {"files": files},
                          f"bundle:{d.name}/{'+'.join(sorted(files))}#{_sha(blob)}")


class MetricsFixture:
    """지표 카탈로그(YAML) + Prometheus query_range 응답 형태의 시계열."""

    def __init__(self, name: str, path: Path, **_: Any):
        self.name, self.path = name, path

    def fetch(self) -> Iterable[RawItem]:
        import yaml
        cat_text = (self.path / "catalog.yaml").read_text(encoding="utf-8")
        cat = yaml.safe_load(cat_text)
        series = json.loads((self.path / "series.json").read_text(encoding="utf-8"))
        at = series["fetched_at"]
        by_id = {r["metric"]["id"]: r["values"] for r in series["data"]["result"]}
        for c in cat["categories"]:
            yield RawItem(self.name, "metric_category", c["id"], c, f"catalog.yaml@{_sha(cat_text.encode())}")
        for m in cat["metrics"]:
            vals = [[t, None if v is None else float(v)] for t, v in by_id.get(m["id"], [])]
            yield RawItem(self.name, "metric", m["id"], {**m, "values": vals, "fetched_at": at},
                          f"prometheus:query_range{{id=\"{m['id']}\"}}#{_sha(json.dumps(vals).encode())}")


from .ops_csv import OpsMetaCsv  # noqa: E402

CONNECTORS = {
    "ops_csv": OpsMetaCsv,
    "incident_bundles": IncidentBundles,
    "metrics_fixture": MetricsFixture,
    "dag_repo": DagRepo,
    "airflow_fixture": AirflowFixture,
    "ddl": Ddl,
    "search_fixture": SearchFixture,
    "incidents_fixture": IncidentsFixture,
    "manuals": Manuals,
}


def build(sources: list[dict[str, Any]], resolve) -> list:
    out = []
    for s in sources:
        s = dict(s)
        cls = CONNECTORS[s.pop("kind")]
        out.append(cls(path=resolve(s.pop("path")), **s))
    return out
