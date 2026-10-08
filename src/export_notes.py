"""Build a clean Literature Lab reading-notes PDF for one paper."""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any

import pymupdf

from src.library import outline_for_library_paper, resolve_library_paper
from src.marks import get_marks
from src.reader import find_paper, outline_for_paper
from src.tutor import get_memory, paper_key_for

INK = (0.11, 0.10, 0.08)
MUTED = (0.42, 0.38, 0.33)
GOLD = (0.72, 0.47, 0.08)
DARK = (0.11, 0.10, 0.08)
CREAM = (0.98, 0.96, 0.91)
LINE = (0.86, 0.82, 0.74)
CARD = (1, 0.97, 0.90)
WHITE = (1, 1, 1)

MARGIN = 52
HEADER_H = 72
FOOTER_H = 34
PAGE_W = 595
PAGE_H = 842
CONTENT_W = PAGE_W - 2 * MARGIN
LINE_GAP = 4

FONT = pymupdf.Font("helv")
BOLD = pymupdf.Font("hebo")

PASSES = (
    {
        "title": "Pass 1 · Survey",
        "goal": "Get the category, context, and claimed contribution. Decide if a second pass is worth it.",
        "steps": "Title → Abstract → Introduction → Headings → Conclusion",
    },
    {
        "title": "Pass 2 · Grasp",
        "goal": "Follow figures, the claimed gap, the method, and the results. Mark what is unclear.",
        "steps": "Related work / gap → Method → Results",
    },
    {
        "title": "Pass 3 · Deep",
        "goal": "Only for your 2–3 core papers: check claims against evidence.",
        "steps": "Check a claim → Limitations",
    },
    {
        "title": "What to note",
        "goal": "Problem, gap, method, dataset, findings, limits — in your own words.",
        "steps": "Problem · Gap · Method · Dataset · Findings · Limits",
    },
)


def notes_pdf_bytes(
    *,
    kind: str,
    task_id: str = "",
    rank: int = 0,
    upload_id: str = "",
    pass_index: int = 0,
    focus: str = "",
) -> tuple[bytes, str]:
    payload = _payload(
        kind=kind,
        task_id=task_id,
        rank=rank,
        upload_id=upload_id,
        pass_index=pass_index,
        focus=focus,
    )
    pdf = _draw(payload)
    return pdf, _filename(payload.get("title") or "paper")


def _payload(
    *,
    kind: str,
    task_id: str,
    rank: int,
    upload_id: str,
    pass_index: int,
    focus: str,
) -> dict[str, Any]:
    if kind == "you":
        paper = resolve_library_paper(kind="you", upload_id=upload_id) or {}
        outline = outline_for_library_paper(paper, research_name="Your papers")
        marks = get_marks(f"upload:{upload_id}", 1)
        tutor = get_memory(paper_key_for(kind="you", upload_id=upload_id))
    else:
        paper = find_paper(task_id, rank) or {}
        outline = outline_for_paper(paper)
        marks = get_marks(task_id, rank)
        tutor = get_memory(paper_key_for(kind="agent", task_id=task_id, rank=rank))
        outline["research_name"] = outline.get("research_name") or paper.get("research_name") or ""

    idx = max(0, min(3, int(pass_index or 0)))
    return {
        "title": outline.get("title") or paper.get("title") or "Untitled paper",
        "year": outline.get("year") or paper.get("year") or "",
        "authors": outline.get("authors") or "",
        "research_name": outline.get("research_name") or ("Your papers" if kind == "you" else ""),
        "page_count": outline.get("page_count") or 0,
        "pass": PASSES[idx],
        "focus": " ".join(str(focus or "").split())[:400],
        "marks": marks.get("marks") or [],
        "extract": [
            ("Problem", outline.get("problem")),
            ("Method", outline.get("method")),
            ("Dataset", outline.get("datasets")),
            ("Features", outline.get("features")),
            ("Findings", outline.get("key_findings")),
            ("Limitations", outline.get("limitations")),
        ],
        "messages": [
            item
            for item in (tutor.get("messages") or [])
            if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
        ],
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _filename(title: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "paper").strip())[:60].strip("-") or "paper"
    return f"Literature-Lab-notes-{stem}.pdf"


def _draw(data: dict[str, Any]) -> bytes:
    doc = pymupdf.open()
    doc.set_metadata(
        {
            "title": f"Reading notes - {data['title']}",
            "author": "Literature Lab",
            "creator": "Literature Lab",
            "producer": "Literature Lab",
        }
    )
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = _header(page) + 22
    page, y = _title_block(doc, page, y, data)

    page, y = _section(doc, page, y, "What you are working on")
    working = [
        f"Guide: {data['pass']['title']}",
        data["pass"]["goal"],
        f"Look at: {data['pass']['steps']}",
    ]
    if data.get("focus"):
        working.append(f"Focus now: {data['focus']}")
    page, y = _card(doc, page, y, "\n".join(working))

    extract_bits = [(label, text) for label, text in data["extract"] if str(text or "").strip()]
    if extract_bits:
        page, y = _section(doc, page, y, "Extract snapshot")
        for label, text in extract_bits:
            page, y = _labeled(doc, page, y, label, str(text))

    page, y = _section(doc, page, y, f"Your marks ({len(data['marks'])})")
    if not data["marks"]:
        page, y = _lines(doc, page, y, ["No marks yet. Select text in the PDF and click Mark."], MUTED, 10)
    else:
        for index, mark in enumerate(data["marks"], start=1):
            quote = str(mark.get("text") or "").strip() or "(no quote)"
            note = str(mark.get("note") or "").strip()
            body = f"{index}. p.{mark.get('page') or '?'}\n“{quote}”"
            if note:
                body += f"\nYour note: {note}"
            page, y = _card(doc, page, y, body)

    page, y = _section(doc, page, y, "Reading tutor")
    if not data["messages"]:
        page, y = _lines(doc, page, y, ["No tutor chat saved for this paper yet."], MUTED, 10)
    else:
        for item in data["messages"]:
            who = "You" if item.get("role") == "user" else "Tutor"
            page, y = _labeled(doc, page, y, who, _plain(item.get("content") or ""))

    page, y = _ensure(doc, page, y, 28)
    page, y = _lines(
        doc,
        page,
        y,
        ["These are your reading notes. Do not paste tutor text into a proposal or assignment."],
        MUTED,
        9,
    )
    _footers(doc, data)
    buf = io.BytesIO()
    doc.save(buf, deflate=True, garbage=3)
    doc.close()
    return buf.getvalue()


def _header(page: pymupdf.Page) -> float:
    page.draw_rect(pymupdf.Rect(0, 0, PAGE_W, HEADER_H), color=DARK, fill=DARK)
    page.draw_rect(pymupdf.Rect(0, HEADER_H - 3, PAGE_W, HEADER_H), color=GOLD, fill=GOLD)
    page.draw_rect(pymupdf.Rect(0, 0, 6, HEADER_H), color=GOLD, fill=GOLD)

    lit = "Literature "
    page.insert_text((MARGIN, 32), lit, fontsize=18, fontname="hebo", color=WHITE)
    lab_x = MARGIN + BOLD.text_length(lit, fontsize=18)
    page.insert_text((lab_x, 32), "Lab", fontsize=18, fontname="hebo", color=GOLD)
    page.insert_text((MARGIN, 50), "Reading notes", fontsize=9, fontname="helv", color=(0.85, 0.72, 0.38))

    right = "Your notes · not the paper"
    right_w = FONT.text_length(right, fontsize=8.5)
    page.insert_text((PAGE_W - MARGIN - right_w, 50), right, fontsize=8.5, fontname="helv", color=(0.72, 0.66, 0.58))
    return HEADER_H


def _title_block(doc, page, y: float, data: dict[str, Any]):
    page, y = _lines(doc, page, y, _wrap(data["title"], CONTENT_W, 16, bold=True), INK, 16, bold=True)
    meta = "  ·  ".join(
        part
        for part in (
            str(data.get("year") or "").strip() or "Year not set",
            str(data.get("authors") or "").strip(),
            f"{data['page_count']} pages" if data.get("page_count") else "",
            str(data.get("research_name") or "").strip(),
        )
        if part
    )
    page, y = _lines(doc, page, y, _wrap(meta, CONTENT_W, 10), MUTED, 10)
    y += 8
    page, y = _ensure(doc, page, y, 4)
    page.draw_rect(pymupdf.Rect(MARGIN, y, PAGE_W - MARGIN, y + 0.7), color=LINE, fill=LINE)
    return page, y + 16


def _section(doc, page, y: float, title: str):
    page, y = _ensure(doc, page, y, 26)
    page.insert_text((MARGIN, y + 11), title.upper(), fontsize=8.5, fontname="hebo", color=GOLD)
    return page, y + 22


def _labeled(doc, page, y, label: str, text: str):
    page, y = _ensure(doc, page, y, 22)
    page.insert_text((MARGIN, y + 9), label.upper(), fontsize=8, fontname="hebo", color=GOLD)
    return _lines(doc, page, y + 14, _wrap(text, CONTENT_W, 10), INK, 10)


def _card(doc, page, y: float, text: str):
    lines = _wrap(text, CONTENT_W - 20, 10)
    pad = 10
    line_h = 10 + LINE_GAP
    i = 0
    while i < len(lines):
        page, y = _ensure(doc, page, y, 40)
        avail = page.rect.height - FOOTER_H - 14 - y
        fit = max(1, int((avail - 2 * pad) // line_h))
        chunk = lines[i : i + fit]
        height = 2 * pad + len(chunk) * line_h
        box = pymupdf.Rect(MARGIN, y, PAGE_W - MARGIN, y + height)
        page.draw_rect(box, color=LINE, fill=CARD, width=0.6)
        page.draw_rect(pymupdf.Rect(box.x0, box.y0, box.x0 + 3, box.y1), color=GOLD, fill=GOLD)
        cy = box.y0 + pad + 10
        for line in chunk:
            page.insert_text((box.x0 + 12, cy), line, fontsize=10, fontname="helv", color=INK)
            cy += line_h
        y = box.y1 + 8
        i += len(chunk)
    return page, y


def _lines(doc, page, y: float, lines: list[str], color, size: float, *, bold: bool = False):
    fontname = "hebo" if bold else "helv"
    step = size + LINE_GAP
    for line in lines or [""]:
        page, y = _ensure(doc, page, y, step + 2)
        page.insert_text((MARGIN, y + size), line, fontsize=size, fontname=fontname, color=color)
        y += step
    return page, y + 4


def _plain(text: str) -> str:
    value = str(text or "").replace("\r", "")
    value = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", "").strip(), value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"^#{1,6}\s+", "", value, flags=re.M)
    value = re.sub(r"^>\s?", "", value, flags=re.M)
    value = re.sub(r"^[-*]\s+", "• ", value, flags=re.M)
    return value.strip()


def _wrap(text: str, width: float, size: float, *, bold: bool = False) -> list[str]:
    font = BOLD if bold else FONT
    out: list[str] = []
    blocks = str(text or "").replace("\r", "").split("\n") or [""]
    for para in blocks:
        words: list[str] = []
        for word in para.split():
            words.extend(_split_word(word, width, size, font))
        if not words:
            out.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if font.text_length(trial, fontsize=size) <= width:
                current = trial
            else:
                out.append(current)
                current = word
        out.append(current)
    return out or [""]


def _split_word(word: str, width: float, size: float, font: pymupdf.Font) -> list[str]:
    if font.text_length(word, fontsize=size) <= width:
        return [word]
    parts: list[str] = []
    buf = ""
    for char in word:
        trial = buf + char
        if buf and font.text_length(trial, fontsize=size) > width:
            parts.append(buf)
            buf = char
        else:
            buf = trial
    if buf:
        parts.append(buf)
    return parts or [word]


def _ensure(doc, page, y: float, need: float):
    if y + need <= page.rect.height - FOOTER_H - 10:
        return page, y
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    return page, _header(page) + 18


def _footers(doc, data: dict[str, Any]) -> None:
    total = doc.page_count
    stamp = data.get("exported_at") or ""
    for index, page in enumerate(doc, start=1):
        page.draw_rect(
            pymupdf.Rect(0, PAGE_H - FOOTER_H, PAGE_W, PAGE_H),
            color=CREAM,
            fill=CREAM,
        )
        page.draw_rect(pymupdf.Rect(0, PAGE_H - FOOTER_H, PAGE_W, PAGE_H - FOOTER_H + 1.2), color=LINE, fill=LINE)
        page.insert_text((MARGIN, PAGE_H - 14), f"Literature Lab  ·  {stamp}", fontsize=8, fontname="helv", color=MUTED)
        label = f"{index} / {total}"
        page.insert_text(
            (PAGE_W - MARGIN - FONT.text_length(label, fontsize=8), PAGE_H - 14),
            label,
            fontsize=8,
            fontname="helv",
            color=MUTED,
        )
