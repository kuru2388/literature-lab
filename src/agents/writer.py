"""Writer agent: synthesis report in Markdown."""

from __future__ import annotations

from src.llm import _is_busy_error, chat


def write_report(
    idea: str,
    plan: dict,
    papers: list[dict],
    gaps: list[dict],
) -> str:
    compact_plan = {
        "title": plan.get("title") or "",
        "tags": (plan.get("tags") or [])[:8],
        "questions": (plan.get("research_questions") or [])[:4],
    }
    compact_papers = [_paper_card(paper) for paper in (papers or [])[:8]]
    compact_gaps = [
        {
            "title": item.get("title") or item.get("gap") or "",
            "why": _clip(item.get("why") or item.get("summary") or item.get("description") or "", 280),
        }
        for item in (gaps or [])[:6]
    ]
    try:
        return chat(
            system=(
                "You write concise academic literature-review memos. "
                "Use Markdown with clear headings. Be specific and cite paper titles. "
                "Do not invent papers that were not provided. Keep the whole memo under 700 words."
            ),
            user=(
                f"Project idea:\n{_clip(idea, 800)}\n\n"
                f"Plan:\n{compact_plan}\n\n"
                f"Ranked papers:\n{compact_papers}\n\n"
                f"Gaps:\n{compact_gaps}\n\n"
                "Write a report with these sections:\n"
                "1. Project framing\n"
                "2. What the literature already covers\n"
                "3. Best papers to read first (ranked)\n"
                "4. Key findings\n"
                "5. Research gaps and opportunities\n"
                "6. Suggested next experiments\n"
            ),
            temperature=0.3,
        )
    except Exception as exc:
        if not _is_busy_error(exc):
            raise
        return _local_report(idea, compact_papers, compact_gaps)


def _paper_card(paper: dict) -> dict:
    return {
        "rank": paper.get("rank"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "arxiv_id": paper.get("arxiv_id"),
        "findings": _clip(paper.get("key_findings") or "", 320),
        "method": _clip(paper.get("method") or "", 180),
        "limits": _clip(paper.get("limitations") or "", 160),
    }


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _local_report(idea: str, papers: list[dict], gaps: list[dict]) -> str:
    lines = [
        "# Reading notes (draft)",
        "",
        "The model was busy, so this draft is built from your extract. "
        "Open the papers and write your own notes. Do not paste this into a proposal.",
        "",
        "## Project framing",
        idea.strip() or "(no idea saved)",
        "",
        "## Best papers to read first",
    ]
    if not papers:
        lines.append("No ranked papers were saved.")
    for paper in papers:
        lines.append(
            f"{paper.get('rank') or '-'}. **{paper.get('title') or 'Untitled'}**"
            + (f" ({paper.get('year')})" if paper.get("year") else "")
        )
        if paper.get("findings"):
            lines.append(f"   Findings: {paper['findings']}")
        if paper.get("method"):
            lines.append(f"   Method: {paper['method']}")
    lines.extend(["", "## Research gaps"])
    if not gaps:
        lines.append("No gaps were saved.")
    for gap in gaps:
        title = gap.get("title") or "Gap"
        why = gap.get("why") or ""
        lines.append(f"- **{title}**{(': ' + why) if why else ''}")
    lines.append("")
    return "\n".join(lines)
