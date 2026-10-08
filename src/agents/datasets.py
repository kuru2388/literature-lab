"""Dataset agent: turn the datasets named inside papers into a usable catalog."""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

from src.llm import chat_json

MAX_DATASETS = 20


def collect_datasets(idea: str, papers: list[dict]) -> list[dict]:
    """Group the datasets mentioned across papers, with what each one is and why it helps."""
    mentions = []
    for index, paper in enumerate(papers):
        text = (paper.get("datasets") or "").strip()
        if not text:
            continue
        mentions.append(
            {
                "paper_index": index,
                "paper_title": paper.get("title"),
                "datasets_text": text[:600],
                "method": (paper.get("method") or "")[:300],
            }
        )

    if not mentions:
        return []

    data = chat_json(
        system=(
            "You build a dataset catalog from research papers. "
            "Merge mentions of the same dataset into one entry, using the name the "
            "community actually uses. Skip vague mentions such as 'our own data' or "
            "'a private corpus' unless the paper names it. "
            "Never invent a URL: only fill 'homepage' when you are certain of the real "
            "address, otherwise leave it empty. "
            "Compare every named dataset that could help this idea. Do not cap at 5; "
            "include each distinct dataset mentioned, most useful first."
        ),
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Dataset mentions by paper (JSON):\n{json.dumps(mentions, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "datasets": [\n'
            "    {\n"
            '      "name": "short official name",\n'
            '      "kind": "text | images | audio | video | tabular | graph | multimodal | benchmark",\n'
            '      "size": "short size or scale if known, else empty",\n'
            '      "access": "public | on request | unknown",\n'
            '      "about": "what it contains, max 25 words",\n'
            '      "why_useful": "why it helps this specific project idea, max 25 words",\n'
            '      "strengths": "what this dataset is strong at, max 18 words",\n'
            '      "limitations": "what is missing or hard about it, max 18 words",\n'
            '      "feasibility": "easy | medium | hard",\n'
            '      "feasibility_note": "why that feasibility, max 12 words",\n'
            '      "homepage": "real URL or empty",\n'
            '      "paper_indexes": [0]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"Include every distinct named dataset from the papers, at most {MAX_DATASETS}. "
        ),
    )

    raw = data.get("datasets") if isinstance(data, dict) else None
    if not raw:
        return []

    catalog: list[dict] = []
    for item in raw[:MAX_DATASETS]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())[:80]
        if not name:
            continue
        catalog.append(
            {
                "name": name,
                "kind": _clean(item.get("kind"), 24) or "unknown",
                "size": _clean(item.get("size"), 40),
                "access": _clean(item.get("access"), 20) or "unknown",
                "about": _words(item.get("about"), 25),
                "why_useful": _words(item.get("why_useful"), 25),
                "strengths": _words(item.get("strengths"), 18),
                "limitations": _words(item.get("limitations"), 18),
                "feasibility": _feasibility(item.get("feasibility"), item.get("access")),
                "feasibility_note": _words(item.get("feasibility_note"), 12),
                "homepage": _url(item.get("homepage")),
                "search_url": f"https://www.google.com/search?q={quote_plus(name + ' dataset')}",
                "hf_url": f"https://huggingface.co/datasets?search={quote_plus(name)}",
                "papers": _papers(item.get("paper_indexes"), papers),
            }
        )
    return catalog


def _feasibility(value, access) -> str:
    text = str(value or "").strip().lower()
    if text in {"easy", "medium", "hard"}:
        return text
    access_text = str(access or "").strip().lower()
    if access_text == "public":
        return "easy"
    if access_text == "on request":
        return "medium"
    return "hard"


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _words(value, limit: int) -> str:
    words = str(value or "").split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(",.;:") + "."


def _url(value) -> str:
    text = str(value or "").strip()
    if re.match(r"^https?://[^\s]+\.[^\s]+$", text) and " " not in text:
        return text[:300]
    return ""


def _papers(indexes, papers: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for raw in indexes or []:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if index in seen or index < 0 or index >= len(papers):
            continue
        seen.add(index)
        paper = papers[index]
        out.append(
            {
                "rank": paper.get("rank"),
                "title": paper.get("title"),
                "url": paper.get("url"),
                "source": paper.get("source"),
            }
        )
    return out
