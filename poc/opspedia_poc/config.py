"""설정 로더 (pyyaml + pydantic)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    root: Path
    data_dir: str
    sources: list[dict[str, Any]]
    systems: list[dict[str, Any]]
    teams: list[dict[str, Any]]
    dag_systems: dict[str, str] = {}
    index_families: list[dict[str, Any]]
    schema_systems: dict[str, str] = {}
    user_dictionary: list[str] = []
    known_terms: list[str] = []
    aliases: dict[str, str] = {}
    synonyms: list[list[str]] = []
    now: str
    llm: dict[str, Any]
    ask: dict[str, Any] = {}
    embedder: dict[str, Any]
    search: dict[str, Any]
    server: dict[str, Any]

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else (self.root / q).resolve()

    @property
    def data(self) -> Path:
        d = self.path(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def team(self, team_id: str | None) -> dict[str, Any]:
        return next((t for t in self.teams if t["id"] == team_id), {})

    def system(self, system_id: str | None) -> dict[str, Any]:
        return next((s for s in self.systems if s["id"] == system_id), {})


def load(path: str | Path = "config.yaml") -> Settings:
    p = Path(path).resolve()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Settings(root=p.parent, **raw)
