"""엔티티 추출: pyahocorasick 로 레지스트리 이름 매칭 (LLM 없음, ADR-018)."""
from __future__ import annotations

import ahocorasick


class Extractor:
    def __init__(self, names: dict[str, str]):
        """names: 표면형(소문자) → 엔티티 id."""
        self.a = ahocorasick.Automaton()
        for surface, eid in names.items():
            self.a.add_word(surface.lower(), (surface.lower(), eid))
        self.a.make_automaton() if names else None
        self.empty = not names

    def spans(self, text: str) -> list[tuple[str, int, int, str]]:
        """(엔티티 id, 시작, 끝, 원문 표면형) — 엔티티마다 첫 등장 위치."""
        if self.empty:
            return []
        low = text.lower()
        hits: dict[str, tuple[int, int]] = {}
        for end, (surface, eid) in self.a.iter(low):
            start = end - len(surface) + 1
            before = low[start - 1] if start > 0 else " "
            after = low[end + 1] if end + 1 < len(low) else " "
            # 식별자 경계: 영숫자·밑줄 안쪽 부분 일치 제외 (products 가 products_v42 안에서 잡히는 경우는 허용)
            if (before.isascii() and (before.isalnum() or before == "_")) or (after.isascii() and after.isalnum()):
                continue
            hits.setdefault(eid, (start, end + 1))
        return [(e, a, b, text[a:b]) for e, (a, b) in sorted(hits.items(), key=lambda x: x[1][0])]

    def find(self, text: str) -> list[str]:
        return [e for e, *_ in self.spans(text)]
