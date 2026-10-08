"""Verifier agents: audit the generated keywords, then audit the chosen papers."""

from __future__ import annotations

import json

from src.llm import chat_json


def verify_tags(idea: str, tags: list[str], *, datasets_only: bool = False) -> dict:
    """Second opinion on the planner's search tags before any searching happens."""
    if not tags:
        return {"tags": [], "dropped": [], "added": [], "note": "no tags to verify"}

    if datasets_only:
        system_prompt = (
            "You are a strict research librarian auditing search keywords for finding DATASET papers. "
            "These keywords must find papers that INTRODUCE, DESCRIBE, or BENCHMARK a dataset. "
            "Rules you must follow:\n"
            "1. DROP single-word tags and generic terms ('machine learning', 'deep learning').\n"
            "2. DROP any tag that would not specifically find dataset/corpus/benchmark papers.\n"
            "3. KEEP and PREFER tags that include words like: dataset, corpus, benchmark, annotation, "
            "labeled, ground truth, evaluation set, training data, data collection.\n"
            "4. ADD missing dataset-specific tags (e.g. 'X dataset', 'Y corpus', 'Z benchmark').\n"
            "5. ADD acronym variants where relevant.\n"
            "6. Every tag must be 2-5 words found in academic paper TITLES."
        )
    else:
        system_prompt = (
            "You are a strict research librarian auditing search keywords for academic databases. "
            "Your job is to ensure every keyword is SPECIFIC enough to find the right papers.\n"
            "Rules you must follow:\n"
            "1. DROP single-word tags — they are useless (e.g. 'transformers', 'detection', 'NLP').\n"
            "2. DROP tags that are too generic and would match thousands of unrelated papers "
            "(e.g. 'machine learning', 'deep learning', 'neural network', 'data analysis').\n"
            "3. DROP tags that are off-topic for this specific project idea.\n"
            "4. KEEP tags that are 2-5 words and combine task + domain OR method + application.\n"
            "5. ADD acronym variants for any approved multi-word tags "
            "(e.g. approve both 'named entity recognition' AND 'NER', "
            "'convolutional neural network' AND 'CNN', 'large language model' AND 'LLM').\n"
            "6. ADD any critical missing tags the project clearly needs that the planner missed.\n"
            "7. Every approved keyword must be the kind of phrase that appears in academic paper TITLES."
        )

    data = chat_json(
        system=system_prompt,
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Proposed keywords:\n{json.dumps(tags, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "approved": ["final keywords to search with, most specific first"],\n'
            '  "dropped": [{"tag": "", "reason": "why it is too generic or off-topic"}],\n'
            '  "added": ["new specific keywords you added that were missing"],\n'
            '  "note": "one sentence: what the approved set covers and what angle it focuses on"\n'
            "}\n"
            "Return 8 to 14 approved keywords. Prefer specificity over quantity."
        ),
    )
    if not isinstance(data, dict):
        data = {}

    approved = _clean_list(data.get("approved"))
    added = _clean_list(data.get("added"))
    for tag in added:
        if tag.lower() not in {t.lower() for t in approved}:
            approved.append(tag)
    if not approved:
        approved = list(tags)

    dropped = []
    for item in data.get("dropped") or []:
        if isinstance(item, dict) and item.get("tag"):
            dropped.append(
                {
                    "tag": str(item.get("tag")).strip(),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )

    return {
        "tags": approved[:14],
        "dropped": dropped,
        "added": added,
        "note": str(data.get("note") or "").strip(),
    }


def verify_papers(idea: str, papers: list[dict], *, keep_min: int = 3) -> list[dict]:
    """Judge each ranked paper against the idea and write a short 'why read this'."""
    if not papers:
        return []

    compact = []
    for index, paper in enumerate(papers):
        compact.append(
            {
                "index": index,
                "title": paper.get("title"),
                "year": paper.get("year"),
                "problem": paper.get("problem"),
                "method": paper.get("method"),
                "key_findings": paper.get("key_findings"),
                "limitations": paper.get("limitations"),
            }
        )

    data = chat_json(
        system=(
            "You are a strict reviewer checking whether each paper really fits the user's project idea. "
            "Judge on substance, not title keyword overlap. "
            "A paper is a good fit only if reading it would change how the user builds their project. "
            "For every paper also write 'why_read': 3 to 4 short lines, under 100 words, "
            "written to the user, saying what they will get out of this paper for their own idea."
        ),
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Ranked papers:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "verdicts": [\n'
            "    {\n"
            '      "index": 0,\n'
            '      "fits": true,\n'
            '      "confidence": 0.0,\n'
            '      "verdict": "one short line: strong fit / partial fit / off topic and why",\n'
            '      "why_read": "3-4 short lines, under 100 words, what this paper gives your project"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Include every paper by index."
        ),
    )

    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    if not verdicts:
        return [dict(p, verified="unchecked") for p in papers]

    by_index: dict[int, dict] = {}
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[index] = item

    checked: list[dict] = []
    for index, paper in enumerate(papers):
        item = by_index.get(index) or {}
        updated = dict(paper)
        fits = item.get("fits")
        updated["fits_idea"] = bool(fits) if fits is not None else True
        updated["verified"] = "checked" if item else "unchecked"
        updated["verdict"] = str(item.get("verdict") or "").strip()
        updated["why_read"] = _trim_words(str(item.get("why_read") or ""), 100)
        try:
            updated["confidence"] = round(float(item.get("confidence")), 2)
        except (TypeError, ValueError):
            updated["confidence"] = None
        checked.append(updated)

    # Never delete papers: good fits first, weak fits pushed to the bottom.
    kept = [p for p in checked if p.get("fits_idea")]
    weak = [p for p in checked if not p.get("fits_idea")]
    if len(kept) < keep_min:
        weak.sort(key=lambda p: float(p.get("confidence") or 0), reverse=True)
    ordered = kept + weak
    for i, paper in enumerate(ordered, start=1):
        paper["rank"] = i
    return ordered


def _clean_list(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        value = " ".join(str(item).replace('"', "").split()).strip(" ,.;:")
        if not value or len(value) > 60:
            continue
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        out.append(value)
    return out


def _trim_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(",.;:") + "."
