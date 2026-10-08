"""Papers gathered by the agent, plus PDFs the student uploaded."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from src.db import DATA_DIR, PAPERS_DIR, SessionLocal, Task, task_to_dict
from src.reader import find_paper, outline_for_paper, safe_pdf_path
from src.tools.pdf import extract_for_research, reader_outline

UPLOADS_DIR = PAPERS_DIR / "uploads"
UPLOADS_PATH = DATA_DIR / "uploads.json"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_uploads() -> list[dict]:
    if not UPLOADS_PATH.exists():
        return []
    try:
        data = json.loads(UPLOADS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_uploads(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_PATH.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


def _authors_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(name) for name in value[:8])
    return str(value or "")


def _title_from_pdf(path: Path) -> str:
    try:
        with pymupdf.open(path) as doc:
            meta = doc.metadata or {}
            title = str(meta.get("title") or "").strip()
            if title and title.lower() not in {"untitled", "microsoft word", "document"}:
                return title[:400]
            page = (doc[0].get_text("text") or "") if len(doc) else ""
    except Exception:
        return path.stem
    for line in page.splitlines():
        text = re.sub(r"\s+", " ", line).strip()
        if 12 <= len(text) <= 220 and not text.lower().startswith("arxiv"):
            return text[:400]
    return path.stem.replace("_", " ")[:400]


def get_upload(upload_id: str) -> dict | None:
    uid = str(upload_id or "").strip()
    if not uid:
        return None
    with _lock:
        for row in _read_uploads():
            if str(row.get("id")) == uid:
                return dict(row)
    return None


def list_uploads() -> list[dict]:
    with _lock:
        rows = list(_read_uploads())
    ready = []
    for row in rows:
        path = safe_pdf_path({"pdf_path": row.get("pdf_path")})
        item = dict(row)
        item["has_pdf"] = bool(path)
        item["kind"] = "you"
        item["paper_key"] = f"upload:{item.get('id')}"
        ready.append(item)
    return ready


def save_upload(filename: str, data: bytes) -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("That file is not a PDF.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF is too large. Keep it under 25 MB.")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    dest = UPLOADS_DIR / f"{upload_id}.pdf"
    dest.write_bytes(data)
    outline = reader_outline(dest)
    extract = extract_for_research(dest)
    original = Path(filename or "paper.pdf").name
    paper = {
        "id": upload_id,
        "kind": "you",
        "paper_key": f"upload:{upload_id}",
        "title": _title_from_pdf(dest),
        "original_name": original[:200],
        "year": "",
        "authors": "",
        "url": "",
        "pdf_path": str(dest),
        "has_pdf": True,
        "page_count": outline.get("page_count") or extract.get("page_count") or 0,
        "section_jumps": outline.get("section_jumps") or extract.get("section_jumps") or [],
        "research_name": "Your papers",
        "uploaded_at": _now(),
    }
    with _lock:
        rows = _read_uploads()
        rows.insert(0, paper)
        _write_uploads(rows)
    return paper


def delete_upload(upload_id: str) -> bool:
    uid = str(upload_id or "").strip()
    if not uid:
        return False
    with _lock:
        rows = _read_uploads()
        kept = [row for row in rows if str(row.get("id")) != uid]
        if len(kept) == len(rows):
            return False
        removed = next((row for row in rows if str(row.get("id")) == uid), None)
        _write_uploads(kept)
    path = safe_pdf_path({"pdf_path": (removed or {}).get("pdf_path")})
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    return True


def list_agent_papers() -> list[dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(Task)
            .filter(Task.status == "done")
            .order_by(Task.updated_at.desc())
            .limit(80)
            .all()
        )
        papers = []
        for task in rows:
            payload = task_to_dict(task)
            research = payload.get("title") or payload.get("idea") or ""
            for paper in (payload.get("result") or {}).get("papers") or []:
                rank = paper.get("rank")
                try:
                    rank = int(rank)
                except (TypeError, ValueError):
                    continue
                papers.append(
                    {
                        "kind": "agent",
                        "paper_key": f"task:{task.id}:{rank}",
                        "task_id": task.id,
                        "rank": rank,
                        "research_name": str(research)[:200],
                        "title": paper.get("title") or "Untitled paper",
                        "year": paper.get("year"),
                        "authors": _authors_text(paper.get("authors")),
                        "url": paper.get("url") or "",
                        "source": paper.get("source") or "",
                        "has_pdf": bool(safe_pdf_path(paper)),
                        "page_count": paper.get("page_count") or 0,
                    }
                )
        return papers
    finally:
        session.close()


def library_payload() -> dict:
    from src.progress import attach_progress

    agent = attach_progress(list_agent_papers())
    you = attach_progress(list_uploads())
    return {
        "agent": agent,
        "you": you,
        "count": len(agent) + len(you),
        "agent_count": len(agent),
        "you_count": len(you),
    }


def resolve_library_paper(*, kind: str, task_id: str = "", rank: int = 0, upload_id: str = "") -> dict | None:
    if kind == "you":
        paper = get_upload(upload_id)
        if not paper:
            return None
        paper["kind"] = "you"
        paper["research_name"] = paper.get("research_name") or "Your papers"
        return paper
    paper = find_paper(task_id, rank)
    if not paper:
        return None
    paper = dict(paper)
    paper["kind"] = "agent"
    paper["task_id"] = task_id
    paper["rank"] = rank
    return paper


def outline_for_library_paper(paper: dict, *, research_name: str = "") -> dict:
    data = outline_for_paper(paper, research_name=research_name or paper.get("research_name") or "")
    if paper.get("kind") == "you":
        data["kind"] = "you"
        data["upload_id"] = paper.get("id")
        data["research_name"] = "Your papers"
        data["year"] = paper.get("year") or data.get("year")
        data["authors"] = paper.get("authors") or data.get("authors") or ""
        path = safe_pdf_path(paper)
        if path:
            scanned = reader_outline(path)
            data["has_pdf"] = True
            data["page_count"] = scanned.get("page_count") or data.get("page_count")
            if scanned.get("section_jumps"):
                data["section_jumps"] = scanned["section_jumps"]
    else:
        data["kind"] = "agent"
        data["task_id"] = paper.get("task_id")
        data["rank"] = paper.get("rank")
    return data


def format_upload_as_paper(upload: dict, rank: int = 1) -> dict:
    upload_id = str(upload.get("id") or "").strip()
    return {
        "kind": "you",
        "upload_id": upload_id,
        "paper_key": f"upload:{upload_id}",
        "title": upload.get("title") or "Uploaded paper",
        "authors": [upload.get("authors")] if isinstance(upload.get("authors"), str) and upload.get("authors") else (upload.get("authors") or ["You"]),
        "year": upload.get("year") or datetime.now().year,
        "source": "You",
        "source_tag": "You",
        "url": upload.get("url") or "",
        "pdf_path": upload.get("pdf_path") or "",
        "has_pdf": True,
        "page_count": upload.get("page_count") or 0,
        "key_findings": upload.get("key_findings") or "User uploaded research paper.",
        "why_read": upload.get("why_read") or "Research paper uploaded by you.",
        "verified": "checked",
        "fits_idea": True,
        "score": 10.0,
        "rank": rank,
        "is_user_upload": True,
    }

