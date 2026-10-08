"""Per-paper reading progress for the library cards."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from src.db import DATA_DIR
from src.marks import mark_activity
from src.tutor import tutor_activity

READING_PATH = DATA_DIR / "reading.json"
_lock = threading.Lock()

STEPS = ("opened", "marked", "noted", "tutor")
LABELS = {
    0: "Not started",
    1: "Started",
    2: "Reading",
    3: "Noted",
    4: "Well read",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_opened() -> dict[str, Any]:
    if not READING_PATH.exists():
        return {}
    try:
        data = json.loads(READING_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def mark_opened(paper_key: str) -> None:
    key = str(paper_key or "").strip()
    if not key:
        return
    now = _now()
    with _lock:
        data = _read_opened()
        prev = data.get(key) if isinstance(data.get(key), dict) else {}
        data[key] = {
            "opened_at": prev.get("opened_at") or now,
            "last_at": now,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        READING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def purge_for_task(task_id: str) -> int:
    prefix = f"{str(task_id or '').strip()}:"
    if prefix == ":":
        return 0
    with _lock:
        data = _read_opened()
        kept = {key: value for key, value in data.items() if not str(key).startswith(prefix)}
        removed = len(data) - len(kept)
        if removed:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            READING_PATH.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return removed


def set_reading_status(paper_key: str, status: str) -> dict[str, Any]:
    key = str(paper_key or "").strip()
    valid_statuses = {"unread", "reading", "completed"}
    if not key or status not in valid_statuses:
        return {"error": "Invalid status"}
    now = _now()
    with _lock:
        data = _read_opened()
        prev = data.get(key) if isinstance(data.get(key), dict) else {}
        prev["status"] = status
        prev["last_at"] = now
        data[key] = prev
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        READING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"key": key, "status": status}


def _paper_keys(paper: dict) -> tuple[str, str, str]:
    if str(paper.get("kind") or "") == "you" or paper.get("id"):
        uid = str(paper.get("id") or paper.get("upload_id") or "").strip()
        return f"upload:{uid}:1", f"upload:{uid}", f"upload:{uid}"
    task_id = str(paper.get("task_id") or "").strip()
    rank = int(paper.get("rank") or 0)
    key = f"{task_id}:{rank}"
    return key, key, key


def _compute(*, opened_info: dict | None, mark_count: int, note_count: int, tutor_turns: int, paper_key: str) -> dict[str, Any]:
    marked = mark_count > 0
    noted = note_count > 0
    tutored = tutor_turns > 0
    opened = bool(opened_info)
    active = opened or marked or tutored
    
    saved_status = str((opened_info or {}).get("status") or "").strip()
    if saved_status in {"unread", "reading", "completed"}:
        status = saved_status
    elif active:
        status = "reading"
    else:
        status = "unread"
        
    return {
        "status": status,
        "mark_count": mark_count,
        "note_count": note_count,
        "tutor_turns": tutor_turns,
        "paper_key": paper_key,
    }


def attach_progress(papers: list[dict]) -> list[dict]:
    marks = mark_activity()
    tutors = tutor_activity()
    with _lock:
        opened = _read_opened()
    ready = []
    for paper in papers:
        marks_key, tutor_key, open_key = _paper_keys(paper)
        mark_row = marks.get(marks_key) or {}
        tutor_row = tutors.get(tutor_key) or {}
        opened_info = opened.get(open_key) if isinstance(opened.get(open_key), dict) else ({"opened": True} if open_key in opened else None)
        item = dict(paper)
        item["paper_key"] = open_key
        item["progress"] = _compute(
            opened_info=opened_info,
            mark_count=int(mark_row.get("mark_count") or 0),
            note_count=int(mark_row.get("note_count") or 0),
            tutor_turns=int(tutor_row.get("tutor_turns") or 0),
            paper_key=open_key,
        )
        ready.append(item)
    return ready
