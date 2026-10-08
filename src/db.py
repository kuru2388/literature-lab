"""SQLite persistence for research tasks and live agent progress."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PAPERS_DIR = DATA_DIR / "papers"
DB_PATH = DATA_DIR / "app.db"

_lock = threading.Lock()


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idea: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))}
        if "usage_json" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN usage_json TEXT DEFAULT '{}'"))
        if "title" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN title TEXT"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_task(task_id: str, idea: str, steps: list[dict[str, Any]]) -> Task:
    with _lock:
        session = SessionLocal()
        try:
            task = Task(
                id=task_id,
                idea=idea,
                status="running",
                progress_json=json.dumps(steps),
                result_json="{}",
                usage_json="{}",
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
        finally:
            session.close()


def list_tasks(limit: int = 80) -> list[dict[str, Any]]:
    session = SessionLocal()
    try:
        rows = (
            session.query(Task)
            .order_by(Task.updated_at.desc(), Task.created_at.desc())
            .limit(limit)
            .all()
        )
        chats = []
        for task in rows:
            idea = (task.idea or "").strip()
            usage_raw = json.loads(task.usage_json or "{}")
            total_usd = (usage_raw.get("total") or {}).get("usd_display") or ""
            chats.append(
                {
                    "id": task.id,
                    "idea": idea,
                    "title": _title_for(task),
                    "renamed": bool((task.title or "").strip()),
                    "status": task.status,
                    "usd": total_usd,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )
        return chats
    finally:
        session.close()


def _title_for(task: Task) -> str:
    custom = (task.title or "").strip()
    if custom:
        return clip_title(custom)
    raw = (task.idea or "").strip()
    cleaned = re.sub(r"^(problem|idea|task|project|project idea)\s*:\s*", "", raw, flags=re.IGNORECASE).strip()
    lines = [line.strip().lstrip("# ").strip() for line in (cleaned or raw).split("\n") if line.strip()]
    first_meaningful = lines[0] if lines else ""
    return clip_title(first_meaningful) or "Untitled research"


def clip_title(title: str) -> str:
    text = " ".join(str(title or "").split())
    words = text.split()
    if len(words) > 25:
        text = " ".join(words[:25])
    if len(text) > 160:
        text = text[:160].rstrip()
    return text


def rename_task(task_id: str, title: str) -> dict[str, Any] | None:
    """Set a custom chat title, or clear it to fall back to the idea's first line."""
    with _lock:
        session = SessionLocal()
        try:
            task = session.get(Task, task_id)
            if not task:
                return None
            cleaned = clip_title(title)
            task.title = cleaned or None
            session.commit()
            session.refresh(task)
            return {
                "id": task.id,
                "title": _title_for(task),
                "renamed": bool(cleaned),
            }
        finally:
            session.close()


def delete_task(task_id: str) -> bool:
    with _lock:
        session = SessionLocal()
        try:
            task = session.get(Task, task_id)
            if not task:
                return False
            session.delete(task)
            session.commit()
            return True
        finally:
            session.close()


def get_task(task_id: str) -> Task | None:
    session = SessionLocal()
    try:
        return session.get(Task, task_id)
    finally:
        session.close()


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    with _lock:
        session = SessionLocal()
        try:
            task = session.get(Task, task_id)
            if not task:
                return
            if status is not None:
                task.status = status
            if steps is not None:
                task.progress_json = json.dumps(steps)
            if result is not None:
                task.result_json = json.dumps(result, default=str)
            if error is not None:
                task.error = error
            if usage is not None:
                task.usage_json = json.dumps(usage, default=str)
            task.updated_at = _now()
            session.commit()
        finally:
            session.close()


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "idea": task.idea,
        "title": _title_for(task),
        "status": task.status,
        "progress": json.loads(task.progress_json or "[]"),
        "result": json.loads(task.result_json or "{}"),
        "error": task.error,
        "usage": json.loads(task.usage_json or "{}"),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
