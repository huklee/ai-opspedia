"""수동 업로드: 미리보기(DB 쓰기 없음) → 반영(파이프라인 실행) → 목록 → 삭제(DB 흔적까지 제거)."""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from .adapters.extract_ac import Extractor
from .adapters.static_convert import convert
from .config import Settings

ALLOWED = {".txt", ".html", ".htm", ".xml", ".md"}
MAX_BYTES = 2 * 1024 * 1024
SOURCE = "uploads"


def upload_dir(cfg: Settings) -> Path:
    src = next(s for s in cfg.sources if s["name"] == SOURCE)
    d = cfg.path(src["path"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    stem, dot, ext = Path(name).name.rpartition(".")
    stem = re.sub(r"[^0-9A-Za-z가-힣_\-]+", "-", stem or ext).strip("-").lower()[:60] or "upload"
    ext = "." + ext.lower() if dot else ""
    if ext not in ALLOWED:
        raise ValueError(f"지원하지 않는 형식 {ext or '(없음)'} — txt · html · xml(Confluence) · md")
    return stem + ext


def decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 2 MB 를 넘음")
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("문자 인코딩을 알 수 없음 (UTF-8 · CP949 지원)")


def preview(svc, filename: str, data: bytes) -> dict[str, Any]:
    name = safe_name(filename)
    text = decode(data)
    md, report = convert(name, text)
    doc_id = f"runbook:{Path(name).stem}"
    names = {d["title"]: d["id"] for d in svc.docs.values() if d["type"] in ("dag", "table", "index")}
    ex = Extractor(names)
    ents = []
    for eid, a, b, surf in ex.spans(md):
        in_raw = surf.lower() in text.lower()
        ents.append({"id": eid, "surface": surf, "in_raw": in_raw,
                     "snippet": md[max(0, a - 50): b + 50].replace("\n", " ⏎ ")})
    cur = svc.docs.get(doc_id)
    diff = []
    if cur:
        from .mdutil import strip_frontmatter
        _, old = strip_frontmatter(cur["markdown"])
        diff = [ln for ln in difflib.unified_diff(old.splitlines(), md.splitlines(), lineterm="", n=1)][:200]
    title = next((ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")), Path(name).stem)
    return {"file": name, "doc_id": doc_id, "title": title, "exists": bool(cur), "markdown": md,
            "raw": text[:20000], "report": report, "entities": ents, "diff": diff,
            "checks": _checks(md, report, ents)}


def _checks(md: str, report: dict[str, Any], ents: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = [{"ok": "ok" if md.lstrip().startswith("# ") else "warn", "msg": "제목(H1) 있음" if md.lstrip().startswith("# ") else "제목 없음 → 파일명 사용"}]
    heads = md.count("\n## ")
    out.append({"ok": "ok" if heads else "warn", "msg": f"섹션 {heads}개"})
    out.append({"ok": "ok" if re.search(r"문서 ID\W+[A-Z]+-[A-Z]+-\d+", md) else "warn",
                "msg": "문서 ID 인식" if re.search(r"문서 ID\W+[A-Z]+-[A-Z]+-\d+", md) else "문서 ID 없음 → 장애 근거와 연결 불가"})
    out.append({"ok": "ok" if ents else "warn", "msg": f"레지스트리 엔티티 {len(ents)}개 인식 · 원본 대조 {sum(e['in_raw'] for e in ents)}개 확인"})
    for w in report.get("warnings", []):
        out.append({"ok": "warn", "msg": w})
    return out


def commit(cfg: Settings, filename: str, data: bytes, actor: str, note: str = "") -> dict[str, Any]:
    from .pipeline import run
    name = safe_name(filename)
    decode(data)
    (upload_dir(cfg) / name).write_bytes(data)
    st = run(cfg, job="full-sync", trigger="upload", actor=actor, note=note or f"업로드 {name}")
    return {"file": name, "doc_id": f"runbook:{Path(name).stem}", "run": st}


def listing(cfg: Settings, svc) -> list[dict[str, Any]]:
    out = []
    for f in sorted(upload_dir(cfg).iterdir()):
        if f.suffix.lower() in ALLOWED:
            doc = svc.docs.get(f"runbook:{f.stem}")
            out.append({"file": f.name, "bytes": f.stat().st_size, "doc_id": f"runbook:{f.stem}",
                        "title": doc["title"] if doc else None, "version": doc["version"] if doc else None})
    return out


def delete(cfg: Settings, name: str, actor: str) -> dict[str, Any]:
    """파일 삭제 + DB 흔적 제거 + 재색인 (다른 문서의 링크도 다음 실행에서 정리)."""
    from .pipeline import run
    from .storage import SqliteRepository
    name = safe_name(name)
    f = upload_dir(cfg) / name
    existed = f.exists()
    f.unlink(missing_ok=True)
    repo = SqliteRepository(cfg.data / "opspedia.db")
    purged = repo.purge(f"runbook:{Path(name).stem}", SOURCE, Path(name).stem)
    st = run(cfg, job="full-sync", trigger="upload", actor=actor, note=f"업로드 삭제 {name}")
    return {"file": name, "existed": existed, "purged": purged, "run": st}
