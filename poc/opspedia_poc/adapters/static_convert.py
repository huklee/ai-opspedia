"""정적 문서 변환기: txt · html · Confluence storage XML → Markdown (LLM 없음, 결정적).

반환: (markdown, report) — report 에 변환 통계와 경고(지원하지 않는 매크로 · 추정 변환)를 남겨
업로드 미리보기와 생성 이력에서 그대로 확인.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup, CData, NavigableString, Tag

ADMON = {"info": "info", "note": "note", "tip": "success", "warning": "warning", "panel": "note"}


class Report:
    def __init__(self, fmt: str):
        self.fmt, self.stats, self.warnings = fmt, Counter(), []

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def as_dict(self) -> dict[str, Any]:
        return {"format": self.fmt, "converter": "static-parser v1", "stats": dict(self.stats), "warnings": self.warnings}


# ---------- txt ----------
TXT_HEAD = re.compile(r"^(?:\d+[.)]\s+|[■▶●◆#]+\s*|\[[^\]]+\]\s*$)")


def from_txt(text: str) -> tuple[str, dict[str, Any]]:
    """평문 규칙: 첫 줄 = 제목, '1. 제목' · '■ 제목' · '[제목]' · '제목:' = 섹션, '- ' · '• ' = 목록,
    들여쓰기 4칸 · '$ ' 로 시작 = 코드, 'key: value' 연속 = 표."""
    rep = Report("txt")
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        out += [f"# {lines[i].strip()}", ""]
        rep.stats["title"] += 1
        i += 1
    code: list[str] = []
    kv: list[tuple[str, str]] = []

    def flush_code() -> None:
        if code:
            out.extend(["```", *code, "```", ""])
            rep.stats["code_blocks"] += 1
            code.clear()

    def flush_kv() -> None:
        if len(kv) >= 2:
            out.extend(["| 항목 | 값 |", "|---|---|", *[f"| {k} | {v} |" for k, v in kv], ""])
            rep.stats["tables"] += 1
        else:
            out.extend(f"- {k}: {v}" for k, v in kv)
        kv.clear()
    for raw in lines[i:]:
        line = raw.rstrip()
        s = line.strip()
        if line.startswith(("    ", "\t")) or s.startswith("$ "):
            flush_kv()
            code.append(s[2:] if s.startswith("$ ") else line.strip("\t")[4:] if line.startswith("    ") else line.strip("\t"))
            continue
        flush_code()
        if not s:
            flush_kv()
            out.append("")
            continue
        m = re.match(r"^([가-힣A-Za-z0-9 _/()·-]{1,20}):\s+(.+)$", s)
        if m and not s.startswith(("- ", "* ", "• ")):
            kv.append((m.group(1), m.group(2)))
            continue
        flush_kv()
        if re.match(r"^\d+[.)]\s", s) and len(s) < 40 and not s.endswith((".", "다", "요")):
            out += ["", f"## {re.sub(r'^\d+[.)]\s*', '', s)}", ""]
            rep.stats["headings"] += 1
        elif TXT_HEAD.match(s) or (s.endswith(":") and len(s) < 30):
            out += ["", f"## {re.sub(r'^[■▶●◆#]+\s*|^\[|\]$|:$', '', s).strip()}", ""]
            rep.stats["headings"] += 1
        elif re.match(r"^[-*•·]\s+", s):
            out.append("- " + re.sub(r"^[-*•·]\s+", "", s))
            rep.stats["list_items"] += 1
        elif re.match(r"^\(?\d+\)\s+|^[①-⑳]", s):
            out.append("1. " + re.sub(r"^\(?\d+\)\s+|^[①-⑳]\s*", "", s))
            rep.stats["list_items"] += 1
        else:
            out.append(s)
            rep.stats["paragraphs"] += 1
    flush_code()
    flush_kv()
    if not rep.stats.get("headings"):
        rep.warn("섹션 헤딩을 찾지 못함 — 평문 그대로 변환")
    return _tidy("\n".join(out)), rep.as_dict()


# ---------- html / confluence 공통 ----------
def _inline(node, rep: Report, conf: bool) -> str:
    if isinstance(node, CData):
        return str(node)
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    name = node.name
    inner = "".join(_inline(c, rep, conf) for c in node.children)
    if name in ("strong", "b"):
        return f"**{inner.strip()}**" if inner.strip() else ""
    if name in ("em", "i"):
        return f"*{inner.strip()}*" if inner.strip() else ""
    if name == "code":
        return f"`{node.get_text()}`"
    if name == "br":
        return "  \n"
    if name == "a":
        href = node.get("href", "")
        rep.stats["links"] += 1
        return f"[{inner.strip() or href}]({href})" if href else inner
    if name == "img":
        rep.stats["images"] += 1
        return f"![{node.get('alt', '')}]({node.get('src', '')})"
    if conf and name == "ac:link":
        page = node.find("ri:page")
        user = node.find("ri:user")
        rep.stats["links"] += 1
        if page is not None:
            title = page.get("ri:content-title", "")
            text = node.find("ac:plain-text-link-body")
            return f"[{(text.get_text() if text else '') or title}](confluence:{title})"
        if user is not None:
            return f"@{user.get('ri:username') or user.get('ri:account-id', 'user')}"
        return inner
    if conf and name == "ac:emoticon":
        return ""
    if conf and name == "ac:image":
        att = node.find("ri:attachment")
        rep.stats["images"] += 1
        return f"![{att.get('ri:filename', '') if att else ''}](attachment:{att.get('ri:filename', '') if att else ''})"
    if conf and name == "ac:structured-macro" and node.get("ac:name") in ("status", "jira"):
        p = {x.get("ac:name"): x.get_text() for x in node.find_all("ac:parameter")}
        return f"`{p.get('title') or p.get('key') or node.get('ac:name')}`"
    return inner


def _table(t: Tag, rep: Report, conf: bool) -> list[str]:
    rows = []
    for tr in t.find_all("tr"):
        cells = [re.sub(r"\s+", " ", "".join(_inline(c, rep, conf) for c in td.children)).strip().replace("|", "\\|")
                 for td in tr.find_all(["th", "td"], recursive=False)]
        if cells:
            rows.append(cells)
    if not rows:
        return []
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    rep.stats["tables"] += 1
    return ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w, *["| " + " | ".join(r) + " |" for r in rows[1:]], ""]


def _blocks(node: Tag, rep: Report, conf: bool, depth: int = 0) -> list[str]:
    out: list[str] = []
    for c in node.children:
        if isinstance(c, NavigableString) and not isinstance(c, CData):
            if str(c).strip():
                out += [re.sub(r"\s+", " ", str(c)).strip(), ""]
            continue
        if not isinstance(c, Tag):
            continue
        n = c.name
        if n in ("script", "style", "nav", "head", "footer"):
            rep.stats["removed"] += 1
            continue
        if re.fullmatch(r"h[1-6]", n):
            out += [f"{'#' * int(n[1])} {c.get_text(' ', strip=True)}", ""]
            rep.stats["headings"] += 1
        elif n == "p":
            t = "".join(_inline(x, rep, conf) for x in c.children).strip()
            if t:
                out += [t, ""]
                rep.stats["paragraphs"] += 1
        elif n in ("ul", "ol"):
            for k, li in enumerate(c.find_all("li", recursive=False), 1):
                sub = [x for x in li.children if isinstance(x, Tag) and x.name in ("ul", "ol")]
                text = "".join(_inline(x, rep, conf) for x in li.children if x not in sub).strip()
                out.append(("  " * depth) + (f"{k}. " if n == "ol" else "- ") + text)
                rep.stats["list_items"] += 1
                for s_ in sub:
                    wrap = BeautifulSoup("", "html.parser")
                    holder = wrap.new_tag("div")
                    holder.append(s_.__copy__())
                    out += _blocks(holder, rep, conf, depth + 1)[:-1]
            out.append("")
        elif n == "pre":
            out += ["```", c.get_text().rstrip("\n"), "```", ""]
            rep.stats["code_blocks"] += 1
        elif n == "table":
            out += _table(c, rep, conf)
        elif n == "blockquote":
            out += ["> " + ln for ln in "\n".join(_blocks(c, rep, conf)).strip().splitlines()] + [""]
        elif n == "hr":
            out += ["---", ""]
        elif conf and n == "ac:structured-macro":
            out += _macro(c, rep)
        elif conf and n == "ac:task-list":
            for task in c.find_all("ac:task"):
                done = (task.find("ac:task-status").get_text() if task.find("ac:task-status") else "") == "complete"
                body = task.find("ac:task-body")
                out.append(f"- [{'x' if done else ' '}] {body.get_text(' ', strip=True) if body else ''}")
                rep.stats["tasks"] += 1
            out.append("")
        elif conf and n == "ac:layout":
            out += _blocks(c, rep, conf, depth)
        elif n in ("div", "section", "article", "main", "body", "html", "span", "ac:layout-section", "ac:layout-cell",
                   "ac:rich-text-body"):
            out += _blocks(c, rep, conf, depth)
        else:
            t = "".join(_inline(x, rep, conf) for x in c.children).strip()
            if t:
                out += [t, ""]
            if n.startswith(("ac:", "ri:")):
                rep.warn(f"지원하지 않는 Confluence 요소 <{n}> — 텍스트만 보존")
    return out


def _macro(m: Tag, rep: Report) -> list[str]:
    name = m.get("ac:name", "")
    params = {p.get("ac:name"): p.get_text() for p in m.find_all("ac:parameter", recursive=False)}
    body = m.find("ac:rich-text-body")
    rep.stats[f"macro:{name}"] += 1
    if name in ("code", "noformat"):
        txt = m.find("ac:plain-text-body")
        return [f"```{params.get('language', '')}", (txt.get_text() if txt else "").strip("\n"), "```", ""]
    if name in ADMON:
        inner = _blocks(body, rep, True) if body else []
        title = params.get("title", "")
        return [f'!!! {ADMON[name]} "{title}"' if title else f"!!! {ADMON[name]}", ""] + \
               ["    " + ln if ln else "" for ln in inner] + [""]
    if name == "expand":
        inner = _blocks(body, rep, True) if body else []
        return [f'??? note "{params.get("title", "펼치기")}"', ""] + ["    " + ln if ln else "" for ln in inner] + [""]
    if name in ("toc", "children", "recently-updated", "anchor"):
        rep.warn(f"'{name}' 매크로 제외 (동적 목록 · 앵커)")
        return []
    rep.warn(f"지원하지 않는 매크로 '{name}' — 본문 텍스트만 보존")
    return _blocks(body, rep, True) if body else []


def from_html(text: str) -> tuple[str, dict[str, Any]]:
    rep = Report("html")
    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    root = soup.body or soup
    md = "\n".join(_blocks(root, rep, False))
    if title and not md.lstrip().startswith("# "):
        md = f"# {title}\n\n{md}"
    return _tidy(md), rep.as_dict()


def from_confluence(text: str) -> tuple[str, dict[str, Any]]:
    """Confluence storage format (페이지 export XML · REST body.storage)."""
    rep = Report("confluence-xml")
    title = None
    m = re.search(r"<title>(.*?)</title>", text, re.S)
    if m:
        title = m.group(1).strip()
    body = re.search(r"<body[^>]*>(.*)</body>", text, re.S)
    soup = BeautifulSoup(body.group(1) if body else text, "html.parser")
    md = "\n".join(_blocks(soup, rep, True))
    if title and not md.lstrip().startswith("# "):
        md = f"# {title}\n\n{md}"
    return _tidy(md), rep.as_dict()


def convert(filename: str, text: str) -> tuple[str, dict[str, Any]]:
    low = filename.lower()
    if low.endswith(".md"):
        return text, {"format": "markdown", "converter": "없음 (원문 그대로)", "stats": {}, "warnings": []}
    if low.endswith(".txt"):
        return from_txt(text)
    if low.endswith(".xml") or "<ac:" in text[:5000]:
        return from_confluence(text)
    if low.endswith((".html", ".htm")):
        return from_html(text)
    raise ValueError(f"지원하지 않는 형식: {filename} (txt · html · xml · md)")


def _tidy(md: str) -> str:
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"
