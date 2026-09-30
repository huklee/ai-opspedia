"""markitdown 어댑터: HTML · PDF · Office → Markdown."""
from __future__ import annotations

from pathlib import Path


def to_markdown(path: Path) -> str:
    from markitdown import MarkItDown
    return MarkItDown(enable_plugins=False).convert(str(path)).text_content
