"""Read PDFs by page count: short = full, longer = intro + method + conclusion."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

MAX_CHARS = {
    "short": 12_000,
    "medium": 10_000,
    "long": 9_000,
}

HEADING_RE = re.compile(
    r"^\s*(?:(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVX]{1,4})[\.\)]\s+)?"
    r"(abstract|introduction|overview|related\s+works?|background|prior\s+work|"
    r"method(?:s|ology)?|approach|architecture|proposed\s+method|model|"
    r"experiment(?:s|al\s+setup)?|evaluation|results?|"
    r"discussion|conclusion(?:s)?|limitations|"
    r"references|bibliography|appendix)\b",
    re.I,
)

INTRO_KEYS = {"abstract", "introduction", "overview"}
METHOD_KEYS = {"method", "methods", "methodology", "approach", "architecture", "proposed method", "model"}
RESULTS_KEYS = {"results", "result", "experiment", "experiments", "experimental setup", "evaluation"}
LIMITATION_KEYS = {"limitations"}
CLOSE_KEYS = {"conclusion", "conclusions", "discussion", "summary"}
RELATED_KEYS = {"related work", "related works", "background", "prior work"}
SKIP_KEYS = {"references", "bibliography", "appendix"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower()).strip()


def _kind(name: str) -> str:
    n = _norm(name)
    if n in INTRO_KEYS:
        return "intro"
    if n in METHOD_KEYS:
        return "method"
    if n in RESULTS_KEYS:
        return "results"
    if n in LIMITATION_KEYS:
        return "limitations"
    if n in CLOSE_KEYS:
        return "close"
    if n in RELATED_KEYS:
        return "related"
    if n in SKIP_KEYS:
        return "skip"
    return "other"


def _page_texts(doc) -> list[str]:
    pages = []
    for index in range(len(doc)):
        text = doc[index].get_text("text") or ""
        text = "\n".join(line.rstrip() for line in text.splitlines())
        pages.append(text.strip())
    return pages


def _find_sections(pages: list[str]) -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for page_index, text in enumerate(pages):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or len(line) > 80:
                continue
            match = HEADING_RE.match(line)
            if not match:
                continue
            title = match.group(1)
            key = (_norm(title), page_index)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "title": title.title(),
                    "kind": _kind(title),
                    "page": page_index,
                }
            )
    return found


def _pages_for_kind(sections: list[dict], kind: str, page_count: int) -> list[int]:
    starts = [s["page"] for s in sections if s["kind"] == kind]
    if not starts:
        return []
    chosen: list[int] = []
    for start in starts:
        end = page_count
        for other in sections:
            if other["page"] > start:
                end = other["page"]
                break
        chosen.extend(range(start, min(end, start + 3)))
    return chosen


def _select_pages(page_count: int, sections: list[dict]) -> tuple[list[int], str]:
    if page_count <= 8:
        return list(range(page_count)), "short: full paper"

    intro = _pages_for_kind(sections, "intro", page_count)
    method = _pages_for_kind(sections, "method", page_count)
    close = _pages_for_kind(sections, "limitations", page_count) + _pages_for_kind(
        sections, "close", page_count
    )

    if not intro:
        intro = list(range(min(2, page_count)))
    if not method and page_count >= 4:
        mid = page_count // 2
        method = [p for p in (mid - 1, mid, mid + 1) if 0 <= p < page_count]
    if not close:
        close = list(range(max(0, page_count - 2), page_count))

    if page_count <= 15:
        strategy = "medium: intro + method + conclusion"
    else:
        strategy = "long: intro + method + conclusion"

    ordered: list[int] = []
    for page in intro + method + close:
        if 0 <= page < page_count and page not in ordered:
            ordered.append(page)
    if not ordered:
        ordered = list(range(min(8, page_count)))
    return ordered, strategy


def extract_for_research(pdf_path: Path | str) -> dict:
    path = Path(pdf_path)
    empty = {
        "text": "",
        "page_count": 0,
        "pages_read": [],
        "strategy": "missing",
        "sections_found": [],
        "section_jumps": [],
    }
    if not path.exists():
        return empty

    with pymupdf.open(path) as doc:
        pages = _page_texts(doc)
        page_count = len(pages)

    sections = _find_sections(pages)
    selected, strategy = _select_pages(page_count, sections)
    band = "short" if page_count <= 8 else "medium" if page_count <= 15 else "long"
    text = "\n\n".join(
        f"[page {page + 1}]\n{pages[page]}" for page in selected if pages[page]
    ).strip()
    limit = MAX_CHARS[band]
    if len(text) > limit:
        text = text[:limit] + "\n\n[truncated]"

    return {
        "text": text,
        "page_count": page_count,
        "pages_read": [p + 1 for p in selected],
        "strategy": strategy,
        "sections_found": [s["title"] for s in sections[:12]],
        "section_jumps": _jumps_from_sections(sections, page_count),
    }


def reader_outline(pdf_path: Path | str) -> dict:
    """Page jumps for the in-app reader. Pages are 1-based (PDF viewer)."""
    path = Path(pdf_path)
    if not path.exists():
        return {"page_count": 0, "section_jumps": []}
    with pymupdf.open(path) as doc:
        pages = _page_texts(doc)
        page_count = len(pages)
    sections = _find_sections(pages)
    return {
        "page_count": page_count,
        "section_jumps": _jumps_from_sections(sections, page_count),
    }


def _jumps_from_sections(sections: list[dict], page_count: int) -> list[dict]:
    first: dict[str, dict] = {}
    for section in sections:
        first.setdefault(section["kind"], section)

    specs = (
        ("intro", "Introduction", ("intro",), "first"),
        ("related", "Related work", ("related",), "early"),
        ("method", "Method", ("method",), "mid"),
        ("results", "Results", ("results",), "late"),
        ("limitations", "Limitations", ("limitations", "close"), "last"),
    )
    jumps = []
    for jump_id, label, kinds, fallback in specs:
        hit = next((first[kind] for kind in kinds if kind in first), None)
        if hit:
            page = hit["page"] + 1
            found = True
            heading = str(hit.get("title") or label)
        else:
            found = False
            heading = label
            if fallback == "first":
                page = 1
            elif fallback == "early":
                page = min(page_count, 2)
            elif fallback == "mid":
                page = max(1, (page_count + 1) // 2)
            elif fallback == "late":
                page = max(1, (page_count * 2) // 3)
            else:
                page = max(1, page_count)
        jumps.append(
            {
                "id": jump_id,
                "label": label,
                "page": page,
                "found": found,
                "heading": heading,
            }
        )
    return jumps


def extract_text(pdf_path: Path | str, *, max_pages: int = 8) -> str:
    """Back-compat helper. Prefer extract_for_research()."""
    data = extract_for_research(pdf_path)
    if data["text"]:
        return data["text"]
    path = Path(pdf_path)
    if not path.exists():
        return ""
    parts: list[str] = []
    with pymupdf.open(path) as doc:
        for index in range(min(len(doc), max_pages)):
            parts.append(doc[index].get_text("text") or "")
    return "\n".join(parts).strip()
