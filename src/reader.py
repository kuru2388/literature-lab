"""Resolve a ranked paper's local PDF for the in-app reader."""

from __future__ import annotations

from pathlib import Path

from src.db import PAPERS_DIR, get_task, task_to_dict
from src.tools.pdf import reader_outline


def find_paper(task_id: str, rank: int) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None
    papers = (task_to_dict(task).get("result") or {}).get("papers") or []
    for paper in papers:
        try:
            if int(paper.get("rank") or 0) == rank:
                return paper
        except (TypeError, ValueError):
            continue
    if 1 <= rank <= len(papers):
        return papers[rank - 1]
    return None


def safe_pdf_path(paper: dict | None) -> Path | None:
    if not paper:
        return None
    raw = paper.get("pdf_path")
    if not raw:
        return None
    root = PAPERS_DIR.resolve()
    # 1. Try standard path resolution
    try:
        path = Path(str(raw)).expanduser().resolve()
        path.relative_to(root)
        if path.is_file() and path.suffix.lower() == ".pdf":
            return path
    except (ValueError, OSError):
        pass

    # 2. Cross-platform fallback: match file name inside PAPERS_DIR or uploads/
    name = Path(str(raw).replace("\\", "/")).name
    for candidate in (root / name, root / "uploads" / name):
        if candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate
    return None


def outline_for_paper(paper: dict, *, research_name: str = "") -> dict:
    path = safe_pdf_path(paper)
    jumps = paper.get("section_jumps") or []
    page_count = int(paper.get("page_count") or 0)
    if path:
        scanned = reader_outline(path)
        if scanned.get("section_jumps"):
            jumps = scanned["section_jumps"]
        page_count = scanned.get("page_count") or page_count
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        author_text = ", ".join(str(name) for name in authors[:8])
    else:
        author_text = str(authors)
    return {
        "title": paper.get("title") or "Untitled paper",
        "url": paper.get("url") or "",
        "rank": paper.get("rank"),
        "has_pdf": bool(path),
        "page_count": page_count,
        "section_jumps": jumps,
        "problem": paper.get("problem") or "",
        "method": paper.get("method") or "",
        "features": paper.get("features") or "",
        "datasets": paper.get("datasets") or "",
        "metrics": paper.get("metrics") or "",
        "key_findings": paper.get("key_findings") or "",
        "limitations": paper.get("limitations") or "",
        "why_read": paper.get("why_read") or paper.get("why_it_matters") or "",
        "year": paper.get("year"),
        "authors": author_text,
        "research_name": research_name,
    }
