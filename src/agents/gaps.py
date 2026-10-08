"""Gap-finder agent: what is still missing relative to the user's idea."""

from __future__ import annotations

import json
import re

from src.llm import chat_json


def find_gaps(idea: str, questions: list[str], papers: list[dict]) -> list[dict]:
    compact = []
    by_id: dict[str, dict] = {}
    for index, paper in enumerate(papers):
        paper_id = f"P{paper.get('rank') or index + 1}"
        compact.append(
            {
                "paper": paper_id,
                "title": paper.get("title"),
                "year": paper.get("year"),
                "problem": paper.get("problem"),
                "method": paper.get("method"),
                "key_findings": paper.get("key_findings"),
                "limitations": paper.get("limitations"),
            }
        )
        by_id[paper_id] = paper

    data = chat_json(
        system=(
            "You are a research advisor. Identify genuine gaps between published work "
            "and the user's project idea. Prefer actionable, specific gaps "
            "(missing datasets, missing agent roles, weak evaluation, no open-source, etc.). "
            "Every gap must be grounded in named papers from the list. "
            "This is a reading guide: point the user to papers, do not write a finished "
            "proposal paragraph."
        ),
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Research questions:\n{json.dumps(questions, ensure_ascii=False)}\n\n"
            f"Extracted papers:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "gaps": [\n'
            "    {\n"
            '      "title": "short gap name",\n'
            '      "detail": "2-4 sentences describing what the listed papers still miss",\n'
            '      "opportunity": "what to look for when you read those papers",\n'
            '      "papers": ["P1", "P3"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Return 4 to 7 gaps. Use only paper ids from the list."
        ),
    )
    gaps = data.get("gaps") if isinstance(data, dict) else None
    if not gaps:
        return []
    cleaned: list[dict] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        title = str(gap.get("title") or "").strip()
        if not title:
            continue
        cleaned.append(
            {
                "title": title,
                "detail": str(gap.get("detail") or "").strip(),
                "opportunity": str(gap.get("opportunity") or "").strip(),
                "supported_by": _papers_for(gap.get("papers"), by_id),
            }
        )
    return cleaned


def _papers_for(raw, by_id: dict[str, dict]) -> list[dict]:
    ids: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            match = re.search(r"P\s*(\d+)", str(item), re.IGNORECASE)
            if match:
                ids.append(f"P{int(match.group(1))}")
    seen: set[str] = set()
    out: list[dict] = []
    for paper_id in ids:
        if paper_id in seen or paper_id not in by_id:
            continue
        seen.add(paper_id)
        paper = by_id[paper_id]
        out.append(
            {
                "id": paper_id,
                "title": paper.get("title") or paper_id,
                "url": paper.get("url") or "",
            }
        )
    return out
