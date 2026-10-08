"""Remove everything that belongs to one research chat, including local PDFs."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from src.db import PAPERS_DIR, SessionLocal, Task, delete_task, get_task, task_to_dict
from src.marks import purge_for_task as purge_marks
from src.progress import purge_for_task as purge_progress
from src.stars import remove_stars_for_task
from src.tutor import purge_for_task as purge_tutor

UPLOADS_DIR = PAPERS_DIR / "uploads"


def purge_research(task_id: str) -> bool:
    tid = str(task_id or "").strip()
    if not tid:
        return False
    task = get_task(tid)
    if not task:
        return False
    if not delete_task(tid):
        return False
    purge_marks(tid)
    purge_tutor(tid)
    purge_progress(tid)
    remove_stars_for_task(tid)
    sweep_orphan_pdfs()
    return True


def sweep_orphan_pdfs() -> int:
    """Delete agent PDFs that no remaining chat still uses. Uploads are kept."""
    used = _remaining_pdfs()
    removed = 0
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    for path in list(PAPERS_DIR.glob("*.pdf")) + list(PAPERS_DIR.glob("*.pdf.part")):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in used:
            continue
        if path.suffix.lower() == ".pdf" and not _is_agent_pdf(resolved):
            continue
        try:
            resolved.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _remaining_pdfs() -> set[Path]:
    session = SessionLocal()
    used: set[Path] = set()
    try:
        for task in session.query(Task).all():
            papers = (task_to_dict(task).get("result") or {}).get("papers") or []
            for paper in papers:
                used.update(_paper_files(paper))
    finally:
        session.close()
    return used


def _paper_files(paper: dict) -> set[Path]:
    found: set[Path] = set()
    if not isinstance(paper, dict):
        return found
    candidates: list[Path] = []
    raw = paper.get("pdf_path")
    if raw:
        candidates.append(Path(str(raw)))
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    if arxiv_id:
        candidates.append(PAPERS_DIR / f"{arxiv_id}.pdf")
        candidates.append(PAPERS_DIR / f"{quote_plus(arxiv_id)}.pdf")
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if _is_agent_pdf(resolved):
            found.add(resolved)
    return found


def _is_agent_pdf(path: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(PAPERS_DIR.resolve())
    except (OSError, ValueError):
        return False
    try:
        resolved.relative_to(UPLOADS_DIR.resolve())
        return False
    except ValueError:
        return resolved.is_file() and resolved.suffix.lower() == ".pdf"
