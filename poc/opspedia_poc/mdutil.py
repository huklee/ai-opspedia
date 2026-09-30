"""Markdown 공통 유틸."""
from __future__ import annotations


def strip_frontmatter(md: str) -> tuple[str, str]:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end > 0:
            return md[4:end], md[end + 5:]
    return "", md
