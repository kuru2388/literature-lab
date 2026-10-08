"""Save starred papers so users can reopen them later."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from src.db import DATA_DIR

STARS_PATH = DATA_DIR / "starred.json"
_lock = threading.Lock()


def paper_key(paper: dict) -> str:
    return str(paper.get("url") or paper.get("title") or "").strip()


def _read() -> list[dict]:
    if not STARS_PATH.exists():
        return []
    try:
        data = json.loads(STARS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return data.get("papers") or []


def _write(papers: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STARS_PATH.write_text(json.dumps(papers, indent=2, default=str), encoding="utf-8")


def list_stars() -> list[dict]:
    with _lock:
        return list(_read())


def is_starred(paper: dict) -> bool:
    key = paper_key(paper)
    if not key:
        return False
    with _lock:
        return any(paper_key(item) == key for item in _read())


def snapshot_paper(paper: dict, *, task_id: str = "", chat_title: str = "") -> dict[str, Any]:
    return {
        "key": paper_key(paper),
        "title": paper.get("title") or "",
        "url": paper.get("url") or "",
        "authors": paper.get("authors") or [],
        "year": paper.get("year"),
        "source": paper.get("source") or "",
        "venue": paper.get("venue") or "",
        "citations": paper.get("citations"),
        "rank": paper.get("rank"),
        "score": paper.get("score"),
        "problem": paper.get("problem") or "",
        "method": paper.get("method") or "",
        "features": paper.get("features") or "",
        "datasets": paper.get("datasets") or "",
        "metrics": paper.get("metrics") or "",
        "key_findings": paper.get("key_findings") or "",
        "limitations": paper.get("limitations") or "",
        "why_read": paper.get("why_read") or paper.get("why_it_matters") or "",
        "verdict": paper.get("verdict") or "",
        "task_id": task_id,
        "chat_title": chat_title,
        "starred_at": datetime.now(timezone.utc).isoformat(),
    }


def add_star(paper: dict, *, task_id: str = "", chat_title: str = "") -> dict:
    snap = snapshot_paper(paper, task_id=task_id, chat_title=chat_title)
    if not snap["key"]:
        raise ValueError("Paper needs a title or URL to star.")
    with _lock:
        papers = [item for item in _read() if paper_key(item) != snap["key"]]
        papers.insert(0, snap)
        _write(papers)
    return snap


def remove_star(key: str) -> bool:
    key = (key or "").strip()
    if not key:
        return False
    with _lock:
        papers = _read()
        kept = [item for item in papers if paper_key(item) != key]
        if len(kept) == len(papers):
            return False
        _write(kept)
        return True


def remove_stars_for_task(task_id: str) -> int:
    tid = str(task_id or "").strip()
    if not tid:
        return 0
    with _lock:
        papers = _read()
        kept = [item for item in papers if str(item.get("task_id") or "") != tid]
        removed = len(papers) - len(kept)
        if removed:
            _write(kept)
    return removed
