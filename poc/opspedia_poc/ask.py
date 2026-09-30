"""자연어 질의 검색 (AI 답변). 규칙 우선 + LLM 보조, 모든 답에 인용 · 검증 (ADR-018 · ADR-008).

  ① 질의 해석 : 규칙(의도 키워드 · 엔티티 레지스트리 · 시간 표현) + LLM 재작성(검색어 · 의도)
  ② 검색      : 원 질의 ⊕ 재작성 질의 ⊕ 엔티티 id 를 RRF 로 결합 + 엔티티 구조화 컨텍스트(/api/context)
  ③ 답변      : 근거 [S#] 만으로 2–4문장, 인용 · 수치 검증 → 실패 시 규칙 기반 답변 카드
"""
from __future__ import annotations

import re
import time
from typing import Any

from pydantic import BaseModel, Field

from .adapters.extract_ac import Extractor
from .retrieval import rrf

INTENTS = {
    "원인": ["왜", "원인", "이유", "때문"],
    "담당": ["누가", "담당", "온콜", "오너", "연락"],
    "절차": ["어떻게", "방법", "절차", "하려면", "복구", "재실행", "롤백", "런북"],
    "상태": ["지금", "상태", "됐어", "됐나", "끝났", "정상", "실패했"],
    "영향": ["영향", "다운스트림", "어디까지", "누가 쓰", "읽는"],
    "정의": ["뭐야", "무엇", "뭘 하", "설명", "정의"],
    "지표": ["지표", "ctr", "신선도", "무결과율", "slo", "지연"],
}
NL_HINT = re.compile(r"[?？]|왜|어떻게|누가|언제|뭐야|무엇|있어|했어|됐|나요|인가|할까|하려면")


def looks_natural(q: str) -> bool:
    return bool(NL_HINT.search(q)) or len(q.split()) >= 5


class QueryPlan(BaseModel):
    intent: str = Field(description="원인 | 담당 | 절차 | 상태 | 영향 | 정의 | 지표 | 기타")
    keywords: list[str] = Field(max_length=6, description="검색에 쓸 핵심 명사 · 식별자")
    rewritten: str = Field(description="검색 엔진용으로 다시 쓴 짧은 질의")


class Sentence(BaseModel):
    cites: list[int] = Field(min_length=1, max_length=3, description="근거 번호 (S1 → 1)")
    text: str


class Answer(BaseModel):
    """cites 를 문장보다 먼저 → 근거를 고른 뒤 문장 작성 (제약 디코딩 순서)."""
    sentences: list[Sentence] = Field(min_length=1, max_length=4)


PLAN_SYS = ("운영 위키 검색 질의 분석기. 사용자의 자연어 질문을 검색용으로 바꿈. keywords 는 질문에 나온 핵심 명사와 "
            "식별자만, 조사 · 어미 제거. rewritten 은 6단어 이내.")
ANS_SYS = ("운영 위키 도우미. 주어진 근거 [S#] 만 사용해 한국어 2–4문장으로 답함. "
           "근거에 없는 사실 · 숫자 · 이름은 쓰지 않음. 명사형 종결. "
           "의도가 원인이면 첫 문장에 실패한 업스트림과 근본 원인을 씀.")


class Asker:
    def __init__(self, svc, llm):
        self.svc, self.llm = svc, llm
        names = {k.lower(): v for k, v in svc.cfg.aliases.items() if v in svc.docs}
        for d in svc.docs.values():
            if d["type"] in ("dag", "table", "index", "alias", "metric", "incident", "runbook", "system"):
                names[d["title"].lower()] = d["id"]
                if d["type"] == "incident":
                    names[d["id"].split(":")[1].lower()] = d["id"]
        self.ex = Extractor(names)

    # ① 질의 해석
    def plan(self, q: str) -> dict[str, Any]:
        low = q.lower()
        intents = [k for k, ws in INTENTS.items() if any(w in low for w in ws)]
        ents = self.ex.find(q)
        when = next((w for w in ("오늘", "어제", "지난주", "새벽", "아침") if w in q), None)
        out = {"rule": {"intents": intents, "entities": ents, "when": when}, "llm": None}
        if self.llm is not None:
            t0 = time.perf_counter()
            try:
                p = self.llm.complete_json(PLAN_SYS, q, QueryPlan)
                out["llm"] = {**p.model_dump(), "ms": round((time.perf_counter() - t0) * 1000), **getattr(self.llm, "last", {})}
            except Exception as ex:
                out["llm"] = {"error": str(ex)[:160]}
        intent = (intents[0] if intents else None) or ((out["llm"] or {}).get("intent")) or "기타"
        out["intent"] = intent
        out["entities"] = ents
        return out

    # ② 검색
    def retrieve(self, q: str, plan: dict[str, Any], k: int = 6, use_llm: bool = True) -> list[dict[str, Any]]:
        r = self.svc.retriever
        lists = [[(x["id"], 0) for x in r.search(q, 30)]]
        lp = plan.get("llm") or {}
        if use_llm and lp.get("rewritten"):
            lists.append([(x["id"], 0) for x in r.search(lp["rewritten"] + " " + " ".join(lp.get("keywords", [])), 30)])
        if plan["entities"]:
            lists.insert(0, [(e, 0) for e in plan["entities"]])
        ids = [i for i, _ in rrf(*lists)][:k]
        return [self.svc.docs[i] for i in ids if i in self.svc.docs]

    # ③ 답변
    def sources(self, q: str, plan: dict[str, Any], docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from .retrieval import snippet
        src = []
        for ent in plan["entities"][:2]:
            c = self.svc.context(ent)
            if not c:
                continue
            parts = [f"{c['entity']['title']} ({c['entity']['type']})", c["summary"].replace("\n", " ")]
            st = c["status"]
            if st.get("last_run"):
                parts.append(f"최근 실행 {st['last_run'].get('state')} {st['last_run'].get('start_date', '')[5:16]}")
            if st.get("checks"):
                parts.append("점검: " + "; ".join(x["msg"] for x in st["checks"][:3]))
            if c["oncall"].get("oncall"):
                parts.append(f"담당 {c['oncall']['team']} · 온콜 {c['oncall']['oncall']} · {c['oncall']['channel']}")
            if c["downstream"]:
                parts.append("다운스트림 " + ", ".join(x.get("title", x["id"]) for x in c["downstream"][:5]))
            if c["incidents"]:
                parts.append("알려진 장애 " + ", ".join(x.get("title", x["id"]) for x in c["incidents"][:3]))
            if c["runbooks"]:
                parts.append("런북 " + ", ".join(x.get("title", x["id"]) for x in c["runbooks"][:3]))
            if plan["intent"] == "원인":
                cause = []
                ups = [u for u in c["upstream"] if u.get("status") in ("❌", "⛔", "🔴")]
                if ups:
                    cause.append("문제 있는 업스트림 " + ", ".join(f"{u.get('title', u['id'])}({self._state(u['id'])})" for u in ups[:4]))
                for inc in c["incidents"][:2]:
                    rc = self._root_cause(inc["id"])
                    if rc:
                        cause.append(f"{inc['id'].split(':')[1]} 근본 원인: {rc}")
                parts = parts[:1] + cause + parts[1:]
            src.append({"id": ent, "title": c["entity"]["title"], "kind": "구조화 컨텍스트", "text": " · ".join(p for p in parts if p)})
        for d in docs:
            if any(s["id"] == d["id"] for s in src):
                continue
            src.append({"id": d["id"], "title": d["title"], "kind": d["type"], "text": snippet(d["body"], q, 320)})
        return src[:6]

    def structured(self, plan: dict[str, Any]) -> list[str] | None:
        """담당 · 상태 · 영향 질문은 구조화 사실로 직접 답함 (LLM 불필요 · 환각 없음)."""
        if plan["intent"] not in ("담당", "상태", "영향") or not plan["entities"]:
            return None
        c = self.svc.context(plan["entities"][0])
        if not c:
            return None
        t, st = c["entity"]["title"], c["status"]
        if plan["intent"] == "담당":
            o = c["oncall"]
            return [f"{t} 담당은 {o.get('team') or '미지정'} · 온콜 {o.get('oncall') or '—'} · 채널 {o.get('channel') or '—'} [S1]"]
        if plan["intent"] == "상태":
            if st.get("last_run"):
                lr = st["last_run"]
                return [f"{t} 최근 실행 {lr.get('state')} ({lr.get('start_date', '')[5:16].replace('T', ' ')} 시작) [S1]"]
            if st.get("checks") is not None:
                msgs = "; ".join(x["msg"] for x in st["checks"][:2]) or "이상 없음"
                return [f"{t} 상태 {st.get('level')}: {msgs} [S1]"]
            return None
        down = c["downstream"]
        return [f"{t} 의 3홉 이내 다운스트림 {len(down)}개: " + ", ".join(x.get("title", x["id"]) for x in down[:6]) + " [S1]"]

    def _state(self, i: str) -> str:
        d = self.svc.docs.get(i) or {}
        runs = d.get("facts", {}).get("runs") or []
        return runs[0].get("state", "") if runs else d.get("facts", {}).get("status_icon", "")

    def _root_cause(self, i: str) -> str | None:
        f = (self.svc.docs.get(i) or {}).get("facts", {})
        sy = f.get("synthesis") or {}
        rc = (sy.get("merged") or {}).get("root_cause")
        return (rc or {}).get("text") or (f.get("fields") or {}).get("customfield_root_cause") or None

    def answer(self, q: str, plan: dict[str, Any], src: list[dict[str, Any]]) -> dict[str, Any]:
        rule = self.structured(plan)
        if rule:
            return {"status": "rule", "detail": f"의도 '{plan['intent']}' → 구조화 사실로 직접 답변", "sentences": rule}
        if self.llm is None or not src:
            return {"status": "off", "sentences": self.fallback(plan, src)}
        user = f"질문: {q}\n의도: {plan['intent']}\n\n" + "\n".join(f"[S{i + 1}] {s['title']}: {s['text']}" for i, s in enumerate(src))
        t0 = time.perf_counter()
        try:
            a = self.llm.complete_json(ANS_SYS, user, Answer)
        except Exception as ex:
            return {"status": "error", "detail": str(ex)[:160], "sentences": self.fallback(plan, src)}
        ms = round((time.perf_counter() - t0) * 1000)
        bad, sents = [], []
        for x in a.sentences:
            txt = re.sub(r"\s*\[S\d+\]", "", x.text).strip()
            s = txt + " " + "".join(f"[S{c}]" for c in x.cites)
            sents.append(s)
            if any(c < 1 or c > len(src) for c in x.cites):
                bad.append((s, "범위 밖 인용"))
                continue
            cited = " ".join(src[c - 1]["text"] + " " + src[c - 1]["title"] for c in x.cites)
            nums = [m for m in re.findall(r"\d+(?:\.\d+)?", txt) if len(m) > 1]
            miss = [n for n in nums if n not in cited]
            if miss:
                bad.append((s, f"인용 근거에 없는 수치 {', '.join(miss)}"))
        info = {"ms": ms, **getattr(self.llm, "last", {})}
        if bad and len(bad) == len(sents):
            return {"status": "rejected", "rejected": bad, "sentences": self.fallback(plan, src), **info}
        keep = [s for s in sents if s not in {b[0] for b in bad}]
        return {"status": "ok" if not bad else "partial", "rejected": bad, "sentences": keep, **info}

    def fallback(self, plan: dict[str, Any], src: list[dict[str, Any]]) -> list[str]:
        """LLM 없이: 의도별 규칙 답변 카드 (구조화 컨텍스트 첫 근거)."""
        if not src:
            return ["관련 문서를 찾지 못함"]
        s = src[0]
        return [f"{s['title']}: {s['text'][:220]} [S1]"]

    def ask(self, q: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        plan = self.plan(q)
        t1 = time.perf_counter()
        docs = self.retrieve(q, plan)
        src = self.sources(q, plan, docs)
        t2 = time.perf_counter()
        ans = self.answer(q, plan, src)
        t3 = time.perf_counter()
        return {"query": q, "natural": looks_natural(q), "plan": plan, "answer": ans,
                "sources": [{"n": i + 1, **{k: s[k] for k in ("id", "title", "kind")}, "url": f"/e/{s['id']}"} for i, s in enumerate(src)],
                "model": getattr(self.llm, "label", None),
                "timing_ms": {"plan": round((t1 - t0) * 1000), "retrieve": round((t2 - t1) * 1000), "answer": round((t3 - t2) * 1000)}}
