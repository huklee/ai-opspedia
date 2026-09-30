"""sqlglot 기반 SQL 파싱: 테이블 단위 리니지와 DDL."""
from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp


def _name(t: exp.Table) -> str:
    return ".".join(p for p in (t.db, t.name) if p).lower()


def lineage(sql: str, dialect: str = "hive") -> tuple[set[str], set[str]]:
    """(reads, writes). CTE 이름은 제외."""
    reads: set[str] = set()
    writes: set[str] = set()
    for stmt in sqlglot.parse(sql, read=dialect):
        if stmt is None:
            continue
        ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
        targets: set[str] = set()
        for ins in stmt.find_all(exp.Insert):
            tgt = ins.this if isinstance(ins.this, exp.Table) else ins.this.find(exp.Table)
            if tgt is not None:
                targets.add(_name(tgt))
        writes |= targets
        for t in stmt.find_all(exp.Table):
            n = _name(t)
            if n and n not in targets and n not in ctes:
                reads.add(n)
    return reads, writes


def parse_ddl(sql: str, dialect: str = "hive") -> list[dict[str, Any]]:
    tables = []
    for stmt in sqlglot.parse(sql, read=dialect):
        if not isinstance(stmt, exp.Create) or stmt.kind != "TABLE":
            continue
        schema = stmt.this
        table = schema.this if isinstance(schema, exp.Schema) else schema
        cols = []
        for cd in schema.find_all(exp.ColumnDef) if isinstance(schema, exp.Schema) else []:
            if cd.parent is not schema:
                continue
            comment = next((c.kind.this.name for c in cd.constraints
                            if isinstance(c.kind, exp.CommentColumnConstraint)), "")
            cols.append({"name": cd.name, "type": cd.kind.sql(dialect) if cd.kind else "", "comment": comment})
        comment, partitions = "", []
        props = stmt.args.get("properties")
        if props:
            for p in props.expressions:
                if isinstance(p, exp.SchemaCommentProperty):
                    comment = p.this.name
                elif isinstance(p, exp.PartitionedByProperty):
                    part = p.this
                    for cd in part.find_all(exp.ColumnDef):
                        partitions.append(cd.name)
        tables.append({"name": _name(table), "comment": comment, "columns": cols, "partitions": partitions})
    return tables
