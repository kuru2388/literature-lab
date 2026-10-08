"""Per-paper reading tutor: teaches how to read, does not write the student's work."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.db import DATA_DIR
from src.library import get_upload, resolve_library_paper
from src.llm import chat_messages, chat_messages_stream
from src.marks import get_marks
from src.reader import find_paper, outline_for_paper, safe_pdf_path
from src.settings import has_gemini_key, has_openai_key, resolve_provider
from src.tools.pdf import extract_for_research

TUTOR_DIR = DATA_DIR / "tutor"
_lock = threading.Lock()
MAX_TURNS = 16

REFUSAL = "Sorry, I can't assist with that."

_OFF_TOPIC = re.compile(
    r"(?i)\b("
    r"joke|jokes|funny|riddle|pun|meme|memes|"
    r"poem|poetry|song|lyrics|story|fiction|"
    r"game|play a game|"
    r"recipe|cooking|food|weather|horoscope|astrology|"
    r"celebrity|movie|actor|actress|cinema|"
    r"ceo|cfo|cto|president|founder|boss|sam altman|elon musk|"
    r"capital of|who won|sports|football|cricket|"
    r"(summ?[a-z]*[sz]\w*|summary)\s+(all\s+pages?|all\s+the\s+pages?|the\s+whole\s+paper|whole\s+paper|whole\s+document|full\s+paper|full\s+document|entire\s+paper|entire\s+document|everything|all\s+page)|"
    r"(give|write|provide|send)\s+(me\s+)?(the\s+)?(full|whole|entire|complete)\s+(document|paper|pdf|text|pages?)|"
    r"(dump|extract|print|show)\s+(all\s+pages?|the\s+entire\s+text|the\s+full\s+document|full\s+paper)|"
    r"(write|generate|do)\s+(my\s+)?(essay|homework|assignment|thesis|dissertation|entire\s+paper|full\s+paper)"
    r")\b"
)


def _off_topic_reply(message: str) -> str | None:
    if _OFF_TOPIC.search(str(message or "")):
        return REFUSAL
    return None


SYSTEM_PROMPT = """You are an expert academic reading tutor inside Literature Lab, a local research tool for university and postgraduate students who must critically READ research papers.

Your job is to coach the student through the Advisor's Staged Reading Strategy for THIS paper. You teach the student HOW to read, extract technical depth, and build a defensible literature review.

Off-topic and abuse restrictions (MANDATORY HARD RULE):
- If the user asks for a joke, trivia, casual chat, weather, news, stories, poems, or anything not about learning to read and analyze THIS specific research paper, you MUST reply with exactly:
Sorry, I can't assist with that.
- If the user asks to "summarize all pages", "summarize the whole paper/document", "give full document", "write an essay/paper for me", "do my homework/assignment", or dump text to copy-paste, you MUST REFUSE and reply with exactly:
Sorry, I can't assist with that.
- Stop immediately after that exact single sentence. Never add conversational filler, apologies, or alternatives.

On-topic only: coaching how to read this specific paper, evaluating methodology, verifying architecture, comparing baselines, analyzing limitations, staged reading passes, literature survey matrix synthesis, and snowballing citations.

METHODOLOGY — Staged Reading Strategy (Advisor's Framework):
Do not initially read every research paper word-by-word from page 1 to the references. Use a disciplined 3-pass staged reading strategy:

Pass 1 — Relevance Filter (Fast decision: Is this paper genuinely relevant?):
- Sequence to read: Title → Abstract → Introduction → Problem → Contribution → Results → Conclusion → Limitations / Future Work.
- Goal: Determine whether the paper is genuinely relevant to the student's research direction before committing hours to technical depth.

Pass 2 — Technical Depth (For relevant papers):
- In-depth investigation sequence: Dataset → Preprocessing → Methodology → Architecture → Experimental Setup → Baselines → Metrics → Results → Limitations.
- Goal: Unpack the engineering and scientific rigor: what data was used, how it was cleaned, the exact model architecture/pipeline, how baselines were compared, and what failed.

Pass 3 — Literature Survey Matrix & Snowballing (Synthesis & Gap Finding):
- Immediately record the paper in the Literature Survey Matrix with these columns:
  Paper ID | Problem | Dataset | Method | Contribution | Results | Limitations | Relevance | Possible Gap
- When the student asks to "Record to Matrix", provide:
  1. A structured Markdown table with columns: Paper ID | Problem | Dataset | Method | Contribution | Results | Limitations | Relevance | Possible Gap.
  2. Followed immediately by a clean bulleted breakdown for each field (Problem, Dataset, Method, Contribution, Results, Limitations, Relevance, Possible Gap) so it is effortless to read on any screen size.
- Snowballing technique:
  * Backward Snowballing: Examine important foundational references cited by this paper (especially in Related Work / Introduction).
  * Forward Snowballing: Investigate newer publications that cite this paper to see how the field advanced or if the gap was closed.
- Defensible Research Gap: Remind the student that 2 papers can suggest a direction, but are insufficient to establish a defensible research gap. Synthesizing across a matrix of papers is required.

Teaching rules:
- Never write a fake assignment or full paper for the student to copy. Coach them to understand and extract the facts.
- When teaching a technique (e.g., finding the gap, verifying baselines, or extracting datasets): teach the TECHNIQUE (where authors place it, signal words to scan for), cite short evidence from the paper text, and guide them to VERIFY it on the PDF page.
- Keep responses focused and readable (typically 150-250 words, using clear bullet points or a markdown table where requested).
- If the extract lacks certain details (e.g., exact hyperparameter numbers not in the abstract), clearly tell the student which section in the PDF to look at (e.g. "Section 4.1 Implementation Details").
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(paper_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", paper_key)[:120] or "paper"


def _path(paper_key: str) -> Path:
    TUTOR_DIR.mkdir(parents=True, exist_ok=True)
    return TUTOR_DIR / f"{_safe_name(paper_key)}.json"


def paper_key_for(*, kind: str, task_id: str = "", rank: int = 0, upload_id: str = "") -> str:
    if kind == "you":
        return f"upload:{upload_id}"
    return f"{task_id}:{int(rank)}"


def _read(paper_key: str) -> dict[str, Any]:
    path = _path(paper_key)
    if not path.exists():
        return {"paper_key": paper_key, "messages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"paper_key": paper_key, "messages": []}
    if not isinstance(data, dict):
        return {"paper_key": paper_key, "messages": []}
    data["messages"] = [item for item in (data.get("messages") or []) if isinstance(item, dict)]
    return data


def _write(paper_key: str, data: dict[str, Any]) -> dict[str, Any]:
    doc = {
        "paper_key": paper_key,
        "messages": data.get("messages") or [],
        "updated_at": _now(),
        "provider": data.get("provider") or "",
    }
    with _lock:
        _path(paper_key).write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return doc


def purge_for_task(task_id: str) -> int:
    tid = str(task_id or "").strip()
    if not tid or not TUTOR_DIR.exists():
        return 0
    prefix = f"{tid}:"
    stem_prefix = _safe_name(tid)
    removed = 0
    with _lock:
        for path in list(TUTOR_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            key = str((data or {}).get("paper_key") or "")
            if key.startswith(prefix) or path.stem == stem_prefix or path.stem.startswith(f"{stem_prefix}_"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def tutor_activity() -> dict[str, dict[str, int]]:
    if not TUTOR_DIR.exists():
        return {}
    out: dict[str, dict[str, int]] = {}
    for path in TUTOR_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        key = str(data.get("paper_key") or path.stem).strip()
        if not key:
            continue
        messages = [item for item in (data.get("messages") or []) if isinstance(item, dict)]
        turns = sum(1 for item in messages if item.get("role") == "user" and str(item.get("content") or "").strip())
        if not turns:
            continue
        out[key] = {"tutor_turns": turns}
    return out


def get_memory(paper_key: str) -> dict[str, Any]:
    with _lock:
        data = _read(paper_key)
    return {
        "paper_key": paper_key,
        "messages": data.get("messages") or [],
        "updated_at": data.get("updated_at") or "",
    }


def _paper_context(kind: str, *, task_id: str = "", rank: int = 0, upload_id: str = "") -> str:
    paper = resolve_library_paper(kind=kind, task_id=task_id, rank=rank, upload_id=upload_id) or {}
    if kind == "you":
        upload = get_upload(upload_id) or paper
        outline = outline_for_paper(upload, research_name="Your papers")
        path = safe_pdf_path(upload)
        extract = extract_for_research(path) if path else {}
        marks = get_marks(f"upload:{upload_id}", 1)
        title = upload.get("title") or outline.get("title")
        bits = [
            f"Paper (uploaded by the student): {title}",
            f"Pages: {outline.get('page_count') or extract.get('page_count') or '?'}",
            "Section jumps: "
            + ", ".join(
                f"{item.get('label')} p.{item.get('page')}"
                for item in (outline.get("section_jumps") or [])
            ),
        ]
        text = str(extract.get("text") or "")[:6000]
        if text:
            bits.append("Selected PDF text:\n" + text)
        quotes = (marks.get("marks") or [])[:8]
        if quotes:
            bits.append(
                "Student marks:\n"
                + "\n".join(f"- p.{item.get('page')}: {item.get('text')}" for item in quotes)
            )
        return "\n".join(bits)

    paper = find_paper(task_id, rank) or {}
    outline = outline_for_paper(paper)
    marks = get_marks(task_id, rank)
    path = safe_pdf_path(paper)
    extract = extract_for_research(path) if path else {}
    bits = [
        f"Title: {paper.get('title')}",
        f"Year: {paper.get('year')}",
        f"Authors: {outline.get('authors')}",
        f"Problem (extract): {paper.get('problem') or ''}",
        f"Method (extract): {paper.get('method') or ''}",
        f"Datasets (extract): {paper.get('datasets') or ''}",
        f"Key findings (extract): {paper.get('key_findings') or ''}",
        f"Limitations (extract): {paper.get('limitations') or ''}",
        f"Why read (extract): {paper.get('why_read') or paper.get('why_it_matters') or ''}",
        "Section jumps: "
        + ", ".join(
            f"{item.get('label')} p.{item.get('page')}"
            for item in (outline.get("section_jumps") or [])
        ),
        "These extracts can be wrong. The student must check the PDF.",
    ]
    text = str(extract.get("text") or paper.get("abstract") or "")[:5000]
    if text:
        bits.append("Paper text / abstract:\n" + text)
    quotes = (marks.get("marks") or [])[:8]
    if quotes:
        bits.append(
            "Student marks:\n"
            + "\n".join(f"- p.{item.get('page')}: {item.get('text')}" for item in quotes)
        )
    return "\n".join(bits)


def _prepare_tutor(
    *,
    kind: str,
    message: str,
    provider: str = "",
    task_id: str = "",
    rank: int = 0,
    upload_id: str = "",
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]], str]:
    text = " ".join(str(message or "").split())[:4000]
    if not text:
        raise ValueError("Type a question about how to read this paper.")
    paper_key = paper_key_for(kind=kind, task_id=task_id, rank=rank, upload_id=upload_id)
    memory = get_memory(paper_key)
    history = list(memory.get("messages") or [])
    history.append({"role": "user", "content": text, "at": _now()})
    context = _paper_context(kind, task_id=task_id, rank=rank, upload_id=upload_id)
    payload = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nPaper in front of the student:\n" + context}]
    for item in history[-MAX_TURNS:]:
        role = item.get("role")
        if role in {"user", "assistant"}:
            payload.append({"role": role, "content": item.get("content") or ""})
    chosen = (provider or "").strip().lower()
    if chosen not in {"openai", "gemini"}:
        chosen = resolve_provider()
    return paper_key, history, payload, chosen


def ask_tutor_stream(
    *,
    kind: str,
    message: str,
    provider: str = "",
    task_id: str = "",
    rank: int = 0,
    upload_id: str = "",
):
    paper_key, history, payload, chosen = _prepare_tutor(
        kind=kind,
        message=message,
        provider=provider,
        task_id=task_id,
        rank=rank,
        upload_id=upload_id,
    )
    blocked = _off_topic_reply(message)
    if blocked:
        yield blocked
        history.append({"role": "assistant", "content": blocked, "at": _now(), "provider": chosen})
        _write(paper_key, {"messages": history, "provider": chosen})
        return
    parts: list[str] = []
    try:
        for delta in chat_messages_stream(payload, provider=chosen, temperature=0.4):
            parts.append(delta)
            yield delta
    except Exception:
        if not parts:
            raise
    reply = "".join(parts).strip()
    if not reply:
        raise RuntimeError("The tutor had nothing to say. Try again.")
    history.append({"role": "assistant", "content": reply, "at": _now(), "provider": chosen})
    _write(paper_key, {"messages": history, "provider": chosen})


def ask_tutor(
    *,
    kind: str,
    message: str,
    provider: str = "",
    task_id: str = "",
    rank: int = 0,
    upload_id: str = "",
) -> dict[str, Any]:
    paper_key, history, payload, chosen = _prepare_tutor(
        kind=kind,
        message=message,
        provider=provider,
        task_id=task_id,
        rank=rank,
        upload_id=upload_id,
    )
    blocked = _off_topic_reply(message)
    if blocked:
        history.append({"role": "assistant", "content": blocked, "at": _now(), "provider": chosen})
        saved = _write(paper_key, {"messages": history, "provider": chosen})
        return {
            **saved,
            "reply": blocked,
            "provider": chosen,
            "openai_configured": has_openai_key(),
            "gemini_configured": has_gemini_key(),
        }
    try:
        reply = chat_messages(payload, provider=chosen, temperature=0.4)
    except RuntimeError:
        raise
    history.append({"role": "assistant", "content": reply, "at": _now(), "provider": chosen})
    saved = _write(paper_key, {"messages": history, "provider": chosen})
    return {
        **saved,
        "reply": reply,
        "provider": chosen,
        "openai_configured": has_openai_key(),
        "gemini_configured": has_gemini_key(),
    }
