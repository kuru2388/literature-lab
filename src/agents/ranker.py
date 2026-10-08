"""Abstract ranker + final ranker for papers vs the user's project idea."""

from __future__ import annotations

import json
import re

from src.llm import chat_json

_STOP = {
    "the", "a", "an", "for", "and", "of", "to", "in", "on", "with", "using",
    "from", "into", "about", "that", "this", "via", "based", "paper", "study",
    "approach", "method", "system", "model", "models", "data", "new", "use",
}


def _tokens(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower())
        if len(word) > 2 and word not in _STOP
    }


def relevance_score(
    idea: str,
    paper: dict,
    tags: list[str] | None = None,
    understanding: dict | None = None,
) -> float:
    """Overlap score weighting the idea's specific task/domain/method components higher."""
    focus = _tokens(idea)
    for tag in tags or []:
        focus |= _tokens(tag)

    # Extra weight from structured understanding — these are the most specific signals
    if understanding:
        for key in ("core_task", "application_domain", "method_family", "key_constraints"):
            value = str(understanding.get(key) or "")
            focus |= _tokens(value)

    if not focus:
        return 0.0

    title = _tokens(paper.get("title") or "")
    abstract = _tokens((paper.get("abstract") or "")[:2000])
    title_hit = len(focus & title) / len(focus)
    abs_hit = len(focus & abstract) / len(focus)
    year = paper.get("year") or 0
    recency = max(0.0, min(1.0, ((year or 0) - 2018) / 8))
    cites = min((paper.get("citations") or 0) / 250, 1.0)
    multi = 0.12 * len(paper.get("also_in") or [])
    # Title match weighted higher — if the title matches, the paper is almost certainly relevant
    return 4.0 * title_hit + 1.8 * abs_hit + 0.25 * recency + 0.15 * cites + multi


def prioritize_papers(
    idea: str,
    papers: list[dict],
    *,
    cap: int = 60,
    tags: list[str] | None = None,
    understanding: dict | None = None,
) -> list[dict]:
    scored = sorted(
        papers,
        key=lambda paper: relevance_score(idea, paper, tags, understanding),
        reverse=True,
    )
    return scored[:cap]


def rank_abstracts(
    idea: str,
    papers: list[dict],
    *,
    keep: int = 8,
    tags: list[str] | None = None,
    understanding: dict | None = None,
    datasets_only: bool = False,
) -> list[dict]:
    if not papers:
        return []

    # When datasets_only, pre-filter to papers that mention dataset/corpus/benchmark
    candidate_pool = papers
    if datasets_only:
        _DATASET_SIGNALS = {"dataset", "corpus", "benchmark", "annotation", "labeled",
                            "annotated", "ground-truth", "crowdsourc", "evaluation set"}
        boosted = []
        rest = []
        for p in papers:
            text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
            if any(sig in text for sig in _DATASET_SIGNALS):
                boosted.append(p)
            else:
                rest.append(p)
        # Keep dataset papers first, fill remainder from the rest
        candidate_pool = boosted + rest

    pool = prioritize_papers(
        idea, candidate_pool, cap=max(30, keep * 7), tags=tags, understanding=understanding
    )

    catalog = []
    for index, paper in enumerate(pool):
        catalog.append(
            {
                "index": index,
                "source": paper.get("source"),
                "title": paper.get("title"),
                "year": paper.get("year"),
                "abstract": (paper.get("abstract") or "")[:900],
            }
        )

    # Build a rich idea context for the LLM ranker
    idea_context = idea
    if understanding:
        parts = []
        if understanding.get("core_task"):
            parts.append(f"Core task: {understanding['core_task']}")
        if understanding.get("application_domain"):
            parts.append(f"Domain: {understanding['application_domain']}")
        if understanding.get("method_family"):
            parts.append(f"Method: {understanding['method_family']}")
        if parts:
            idea_context = idea + "\n" + "\n".join(parts)

    if datasets_only:
        llm_system = (
            "You rank academic papers to find ones that INTRODUCE, DESCRIBE, or BENCHMARK a dataset. "
            "Score 1-10. Be strict — only papers about creating or releasing a dataset score high.\n"
            "Score 8-10: Paper presents a new dataset/corpus/benchmark for this specific task+domain.\n"
            "Score 5-7: Paper uses or compares multiple datasets, or describes data collection.\n"
            "Score 1-4: Paper uses a dataset but does not introduce or describe one.\n"
            "Papers without any mention of dataset/corpus/benchmark should score 1-2.\n"
            "Prefer recent (2018+) dataset papers."
        )
        llm_user = (
            f"Project (looking for DATASET papers):\n{idea_context}\n\n"
            f"Search tags used:\n{json.dumps(tags or [], ensure_ascii=False)}\n\n"
            f"Candidate papers:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "ranked": [\n'
            "    {\n"
            '      "index": 0,\n'
            '      "score": 8.5,\n'
            '      "why_it_matters": "one sentence: what dataset this paper introduces and how it helps"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"Include at most {keep} papers, highest score first. "
            "Only include papers that discuss, release, or benchmark a dataset."
        )
    else:
        llm_system = (
            "You rank academic papers for a given project idea. "
            "Score relevance 1-10. Be strict — only keep papers that directly help this project.\n"
            "Score 8-10: Paper addresses the SAME specific task in the SAME domain with similar methods.\n"
            "Score 5-7: Paper addresses the same task in a related domain, or same domain with different methods.\n"
            "Score 1-4: Paper shares only generic terms (agent, learning, system) but different problem.\n"
            "Drop papers scored below 5 unless you cannot fill the quota with higher-scoring ones.\n"
            "Prefer recent (2020+), highly cited, or methodologically close work."
        )
        llm_user = (
            f"Project:\n{idea_context}\n\n"
            f"Search tags used:\n{json.dumps(tags or [], ensure_ascii=False)}\n\n"
            f"Candidate papers:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "ranked": [\n'
            "    {\n"
            '      "index": 0,\n'
            '      "score": 8.5,\n'
            '      "why_it_matters": "one sentence: exactly what this paper contributes to the project"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"Include at most {keep} papers, highest score first. "
            "Do not include papers scored below 5 unless fewer than "
            f"{keep} papers clearly fit."
        )

    data = chat_json(system=llm_system, user=llm_user)
    ranked = data.get("ranked") if isinstance(data, dict) else None

    selected: list[dict] = []
    seen: set[int] = set()
    for item in ranked or []:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index in seen or index < 0 or index >= len(pool):
            continue
        score = float(item.get("score") or 0)
        if score < 5 and len(selected) >= min(3, keep):
            continue
        seen.add(index)
        paper = dict(pool[index])
        paper["score"] = score
        paper["why_it_matters"] = str(item.get("why_it_matters") or "")
        selected.append(paper)
        if len(selected) >= keep:
            break

    if len(selected) < keep:
        for paper in pool:
            key = (paper.get("url") or paper.get("title") or "").strip().lower()
            if any(
                (item.get("url") or item.get("title") or "").strip().lower() == key
                for item in selected
            ):
                continue
            filled = dict(paper)
            filled.setdefault("score", round(relevance_score(idea, paper, tags, understanding), 2))
            filled.setdefault("why_it_matters", "")
            selected.append(filled)
            if len(selected) >= keep:
                break

    return selected[:keep]


def rank_final(idea: str, papers: list[dict], gaps: list[dict]) -> list[dict]:
    if not papers:
        return []

    compact = []
    for index, paper in enumerate(papers):
        compact.append(
            {
                "index": index,
                "arxiv_id": paper.get("arxiv_id"),
                "title": paper.get("title"),
                "year": paper.get("year"),
                "score": paper.get("score"),
                "why_it_matters": paper.get("why_it_matters"),
                "key_findings": paper.get("key_findings"),
                "limitations": paper.get("limitations"),
            }
        )

    data = chat_json(
        system=(
            "You are a senior researcher ranking the best papers to read for a new project. "
            "Re-rank using extracted findings, not just abstracts. "
            "Best papers are those the user should study first to execute their idea."
        ),
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Research gaps:\n{gaps}\n\n"
            f"Papers:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "ranked": [\n'
            "    {\n"
            '      "index": 0,\n'
            '      "score": 9.2,\n'
            '      "why_it_matters": "updated one-sentence reason"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Include every paper, highest score first."
        ),
    )
    ranked = data.get("ranked") if isinstance(data, dict) else None
    if not ranked:
        papers.sort(key=lambda p: float(p.get("score") or 0), reverse=True)
        for i, paper in enumerate(papers, start=1):
            paper["rank"] = i
        return papers

    ordered: list[dict] = []
    seen: set[int] = set()
    for item in ranked:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index in seen or index < 0 or index >= len(papers):
            continue
        seen.add(index)
        paper = dict(papers[index])
        if item.get("score") is not None:
            paper["score"] = float(item.get("score"))
        if item.get("why_it_matters"):
            paper["why_it_matters"] = str(item.get("why_it_matters"))
        ordered.append(paper)

    for index, paper in enumerate(papers):
        if index not in seen:
            ordered.append(dict(paper))

    for i, paper in enumerate(ordered, start=1):
        paper["rank"] = i
    return ordered
