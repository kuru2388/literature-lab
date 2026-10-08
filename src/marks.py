"""Save PDF highlights and reading notes per paper."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.db import DATA_DIR, get_task, task_to_dict
from src.reader import find_paper

MARKS_PATH = DATA_DIR / "marks.json"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_key(task_id: str, rank: int) -> str:
    return f"{task_id}:{int(rank)}"


def _read_all() -> dict[str, Any]:
    if not MARKS_PATH.exists():
        return {}
    try:
        data = json.loads(MARKS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MARKS_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def purge_for_task(task_id: str) -> int:
    prefix = f"{str(task_id or '').strip()}:"
    if prefix == ":":
        return 0
    with _lock:
        data = _read_all()
        kept = {key: value for key, value in data.items() if not str(key).startswith(prefix)}
        removed = len(data) - len(kept)
        if removed:
            _write_all(kept)
    return removed


def _clean_mark(item: dict, *, order: int) -> dict[str, Any]:
    text = " ".join(str(item.get("text") or "").split())[:2000]
    note = str(item.get("note") or "")[:4000]
    try:
        page = max(1, int(item.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    mark_id = str(item.get("id") or uuid.uuid4())[:40]
    return {
        "id": mark_id,
        "page": page,
        "text": text,
        "note": note,
        "order": order,
        "created_at": str(item.get("created_at") or _now()),
    }


def _meta_from_paper(task_id: str, rank: int, extra: dict | None = None) -> dict[str, Any]:
    extra = extra or {}
    if str(task_id).startswith("upload:"):
        from src.library import get_upload

        upload_id = str(task_id).split(":", 1)[1]
        paper = get_upload(upload_id) or {}
        return {
            "task_id": task_id,
            "rank": 1,
            "research_name": str(extra.get("research_name") or "Your papers")[:200],
            "title": str(extra.get("title") or paper.get("title") or "")[:400],
            "year": extra.get("year") if extra.get("year") not in (None, "") else paper.get("year"),
            "authors": str(extra.get("authors") or paper.get("authors") or "")[:400],
            "url": str(paper.get("url") or ""),
        }
    task = get_task(task_id)
    payload = task_to_dict(task) if task else {}
    paper = find_paper(task_id, rank) or {}
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        author_text = ", ".join(str(name) for name in authors[:8])
    else:
        author_text = str(authors)
    return {
        "task_id": task_id,
        "rank": rank,
        "research_name": str(extra.get("research_name") or payload.get("title") or payload.get("idea") or "")[:200],
        "title": str(extra.get("title") or paper.get("title") or "")[:400],
        "year": extra.get("year") if extra.get("year") not in (None, "") else paper.get("year"),
        "authors": str(extra.get("authors") or author_text)[:400],
        "url": str(paper.get("url") or ""),
    }


def mark_activity() -> dict[str, dict[str, int]]:
    with _lock:
        data = _read_all()
    out: dict[str, dict[str, int]] = {}
    for key, stored in data.items():
        if not isinstance(stored, dict):
            continue
        marks = [item for item in (stored.get("marks") or []) if isinstance(item, dict)]
        if not marks:
            continue
        out[str(key)] = {
            "mark_count": len(marks),
            "note_count": sum(1 for item in marks if str(item.get("note") or "").strip()),
        }
    return out


def get_marks(task_id: str, rank: int) -> dict[str, Any]:
    key = _doc_key(task_id, rank)
    meta = _meta_from_paper(task_id, rank)
    with _lock:
        stored = dict(_read_all().get(key) or {})
    marks = [_clean_mark(item, order=index) for index, item in enumerate(stored.get("marks") or [])]
    if not isinstance(stored.get("marks"), list):
        marks = []
    return {
        **meta,
        "research_name": str(stored.get("research_name") or meta["research_name"]),
        "title": str(stored.get("title") or meta["title"]),
        "year": stored.get("year") if stored.get("year") not in (None, "") else meta["year"],
        "authors": str(stored.get("authors") or meta["authors"]),
        "marks": marks,
        "updated_at": stored.get("updated_at") or "",
    }


def save_marks(task_id: str, rank: int, body: dict[str, Any]) -> dict[str, Any]:
    key = _doc_key(task_id, rank)
    raw_marks = body.get("marks") if isinstance(body.get("marks"), list) else []
    cleaned = [_clean_mark(item, order=index) for index, item in enumerate(raw_marks)]
    meta = _meta_from_paper(task_id, rank, body)
    doc = {
        **meta,
        "marks": cleaned,
        "updated_at": _now(),
    }
    with _lock:
        data = _read_all()
        data[key] = doc
        _write_all(data)
    return doc


def list_all_notes() -> dict[str, Any]:
    with _lock:
        data = dict(_read_all())
    papers: list[dict[str, Any]] = []
    total = 0
    for key, stored in data.items():
        if not isinstance(stored, dict):
            continue
        marks = [
            _clean_mark(item, order=index)
            for index, item in enumerate(stored.get("marks") or [])
            if isinstance(item, dict)
        ]
        if not marks:
            continue
        task_id, _, rank_s = str(key).rpartition(":")
        try:
            rank = int(stored.get("rank") or rank_s or 0)
        except (TypeError, ValueError):
            rank = 0
        total += len(marks)
        papers.append(
            {
                "task_id": str(stored.get("task_id") or task_id),
                "rank": rank,
                "research_name": str(stored.get("research_name") or ""),
                "title": str(stored.get("title") or "Untitled paper"),
                "year": stored.get("year"),
                "authors": str(stored.get("authors") or ""),
                "url": str(stored.get("url") or ""),
                "updated_at": str(stored.get("updated_at") or ""),
                "mark_count": len(marks),
                "marks": marks,
            }
        )
    papers.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"papers": papers, "count": total, "paper_count": len(papers)}


def add_mark(task_id: str, rank: int, body: dict[str, Any]) -> dict[str, Any]:
    current = get_marks(task_id, rank)
    marks = list(current.get("marks") or [])
    marks.append(
        {
            "id": str(uuid.uuid4()),
            "page": body.get("page") or 1,
            "text": body.get("text") or "",
            "note": body.get("note") or "",
            "created_at": _now(),
        }
    )
    return save_marks(task_id, rank, {**current, "marks": marks})
