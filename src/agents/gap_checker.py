"""Gap-checker agent: determine whether identified research gaps have been solved by recent papers.

Execution flow
--------------
1. For every extracted gap:
   a. Generate 3 targeted search queries (idea-domain aware).
   b. Search arXiv / OpenAlex / Semantic Scholar / PubMed for the last 3 years.
   c. Filter candidates to papers published AFTER the original paper's year.
   d. LLM judges — must match the project idea domain, strict no partial overlap.

2. If the root gap is ✅ SOLVED → run a recursive downstream check:
   a. Extract the solving paper's key stated limitation / downstream gap.
   b. Generate 3 new focused queries for that downstream gap.
   c. Search ONLY for papers published after the solving paper's year,
      biasing toward top-tier venues (IEEE, ACM, NeurIPS, ICML, ICLR, ACL,
      arXiv cs.AI / cs.CL / cs.LG / cs.SE / cs.HC).
   d. LLM judges the downstream gap: SOLVED or STILL OPEN.
   e. If SOLVED → recurse deeper (up to max_depth=4).
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.agents.gaps import find_gaps
from src.llm import chat_json
from src.tools.sources import search_sources

# ── Config ──────────────────────────────────────────────────────────────────
GAP_CHECK_YEARS = 3        # wider window → more thorough search
GAP_CANDIDATES = 12        # candidates per query (was 6) → deeper search
DOWNSTREAM_CANDIDATES = 8  # candidates for recursive downstream (was 5)
GAP_QUERIES = 3            # queries per gap (was 2) → more diverse

# Venue bias appended to downstream queries so that the LLM filters by credibility
_VENUE_BIAS = (
    "site:arxiv.org OR venue:NeurIPS OR venue:ICML OR venue:ICLR OR venue:ACL "
    "OR venue:IEEE OR venue:ACM"
)


# ── Utilities ────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return time.strftime("%H:%M:%S")


# ── Root Gap Helpers ─────────────────────────────────────────────────────────

def _build_search_queries(idea: str, gap: dict) -> list[str]:
    """Generate 3 focused, idea-domain-aware search queries for a root gap."""
    data = chat_json(
        system=(
            "You are a research search expert. Given a project idea and a research gap, "
            f"generate exactly {GAP_QUERIES} precise search queries (4–8 words each) "
            "that would find recent papers solving this specific gap. "
            "IMPORTANT: Queries must stay within the domain of the project idea "
            "(e.g. if the idea is about desktop GUI agents, do NOT generate queries about mobile). "
            "Vary the angle of each query. Do NOT output explanations. Return JSON only."
        ),
        user=(
            f"Project idea: {idea}\n\n"
            f"Research gap title: {gap.get('title', '')}\n"
            f"Gap detail: {gap.get('detail', '')}\n\n"
            f'Return JSON: {{ "queries": ["query1", "query2", "query3"] }}'
        ),
    )
    if isinstance(data, dict):
        queries = data.get("queries") or []
        return [str(q).strip() for q in queries if str(q).strip()][:GAP_QUERIES]
    return [str(gap.get("title", "")).strip()]


def _judge_relevance(
    idea: str,
    gap: dict,
    candidates: list[dict],
    original_paper_year: int | None = None,
) -> dict[str, Any]:
    """LLM judges whether any candidate paper actually closes the root gap.

    Rules enforced:
    - Paper must be relevant to the PROJECT IDEA domain (not just the gap keywords).
    - Paper must be published AFTER the original paper's year (if known).
    - Partial overlap does NOT count as solving.
    """
    if not candidates:
        return {"solved": False, "solving_papers": [], "reasoning": "No candidate papers found."}

    compact = [
        {
            "index": i,
            "title": p.get("title", ""),
            "year": p.get("year", ""),
            "abstract": (p.get("abstract") or p.get("problem") or "")[:400],
            "url": p.get("url", ""),
            "source": p.get("source", ""),
        }
        for i, p in enumerate(candidates[:14])
    ]

    year_rule = (
        f"2. Paper must be published AFTER {original_paper_year} "
        f"(papers from {original_paper_year} or earlier do NOT count).\n"
        if original_paper_year
        else "2. Prefer recent papers.\n"
    )

    data = chat_json(
        system=(
            "You are a strict domain-aware research evaluator. Given a project idea, a research "
            "gap, and candidate papers, identify which papers (if any) genuinely SOLVE the gap.\n"
            "Strict rules:\n"
            "1. The paper's domain MUST match the project idea domain. "
            "Example: if the idea is about desktop GUI agents, reject any paper about mobile apps, "
            "web apps, or unrelated domains even if keywords overlap.\n"
            f"{year_rule}"
            "3. Partial overlap does NOT count as solving — the paper must directly address "
            "the described gap.\n"
            "4. If you are uncertain, mark as NOT solved.\n"
            "Return JSON only."
        ),
        user=(
            f"Project idea: {idea}\n\n"
            f"Research gap: {gap.get('title', '')}\n"
            f"Gap detail: {gap.get('detail', '')}\n\n"
            f"Candidate papers:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "solved": true or false,\n'
            '  "solving_indices": [0, 2],\n'
            '  "reasoning": "domain-aware explanation of why each accepted paper truly solves the gap"\n'
            "}"
        ),
    )

    if not isinstance(data, dict):
        return {"solved": False, "solving_papers": [], "reasoning": ""}

    solved = bool(data.get("solved"))
    indices = [int(i) for i in (data.get("solving_indices") or []) if isinstance(i, (int, float))]
    solving_papers = [candidates[i] for i in indices if 0 <= i < len(candidates)]
    return {
        "solved": solved,
        "solving_papers": solving_papers,
        "reasoning": str(data.get("reasoning") or "").strip(),
    }


# ── Downstream (Recursive) Helpers ───────────────────────────────────────────

def _extract_downstream_gap(idea: str, root_gap: dict, solving_paper: dict) -> str:
    """
    Extract the primary stated limitation or downstream gap from the solving paper.
    Returns a concise description of the downstream gap.
    """
    data = chat_json(
        system=(
            "You are a research analyst specializing in identifying limitations in academic papers. "
            "Given a paper that solves a research gap, extract its most significant stated limitation "
            "or unresolved downstream gap from its abstract/title context. "
            "Be specific and concise (1–2 sentences). Return JSON only."
        ),
        user=(
            f"Project idea: {idea}\n\n"
            f"Root gap that was solved: {root_gap.get('title', '')}\n\n"
            f"Solving paper:\n"
            f"  Title: {solving_paper.get('title', 'Unknown')}\n"
            f"  Year: {solving_paper.get('year', 'Unknown')}\n"
            f"  Abstract: {(solving_paper.get('abstract') or solving_paper.get('problem') or '')[:500]}\n\n"
            "Identify the key downstream limitation this paper leaves unresolved.\n"
            'Return JSON: { "downstream_gap": "description of the downstream limitation" }'
        ),
    )
    if isinstance(data, dict):
        return str(data.get("downstream_gap") or "").strip()
    return ""


def _build_downstream_queries(idea: str, downstream_gap: str, solving_paper: dict) -> list[str]:
    """Generate 2 targeted queries for the downstream gap, biased toward top venues."""
    paper_year = solving_paper.get("year") or ""
    data = chat_json(
        system=(
            "You are a research search expert. Generate 2 short, precise search queries "
            "(4–8 words each) to find recent papers (published after the given year) "
            "from top-tier venues (NeurIPS, ICML, ICLR, ACL, IEEE, ACM, arXiv cs.AI/CL/LG/SE/HC) "
            "that address the downstream research limitation. Return JSON only."
        ),
        user=(
            f"Project idea: {idea}\n"
            f"Downstream limitation: {downstream_gap}\n"
            f"Only consider papers published after: {paper_year}\n\n"
            'Return JSON: { "queries": ["query1", "query2"] }'
        ),
    )
    if isinstance(data, dict):
        queries = data.get("queries") or []
        return [str(q).strip() for q in queries if str(q).strip()][:2]
    return [downstream_gap[:60]]


def _judge_downstream(
    idea: str,
    downstream_gap: str,
    solving_paper: dict,
    candidates: list[dict],
) -> dict[str, Any]:
    """
    LLM judges whether any credible candidate (published AFTER the solving paper's year)
    resolves the downstream gap. Only top-tier venues count.
    """
    if not candidates:
        return {
            "solved": False,
            "solving_papers": [],
            "reasoning": "No credible candidate papers found after the solving paper's date.",
            "opportunity": (
                "This downstream gap remains an active research opening — "
                "no verified solution was found in top-tier venues published after "
                f"{solving_paper.get('year', 'the solving paper')}."
            ),
        }

    after_year = solving_paper.get("year") or ""
    compact = [
        {
            "index": i,
            "title": p.get("title", ""),
            "year": p.get("year", ""),
            "abstract": (p.get("abstract") or p.get("problem") or "")[:300],
            "url": p.get("url", ""),
            "source": p.get("source", ""),
        }
        for i, p in enumerate(candidates[:8])
    ]

    data = chat_json(
        system=(
            "You are a strict research evaluator. Assess whether any of the listed candidate papers "
            "genuinely resolve the downstream limitation described. "
            "Rules:\n"
            f"1. Only accept papers published AFTER {after_year}.\n"
            "2. Only accept papers from credible venues: IEEE, ACM, NeurIPS, ICML, ICLR, ACL, "
            "   or verified arXiv preprints (cs.AI, cs.CL, cs.LG, cs.SE, cs.HC).\n"
            "3. Partial overlap does NOT count as solving the gap.\n"
            "Return JSON only."
        ),
        user=(
            f"Project idea: {idea}\n\n"
            f"Downstream limitation: {downstream_gap}\n\n"
            f"Candidate papers:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "solved": true or false,\n'
            '  "solving_indices": [0, 2],\n'
            '  "reasoning": "brief explanation",\n'
            '  "opportunity": "why this remains an active research opening (if not solved)"\n'
            "}"
        ),
    )

    if not isinstance(data, dict):
        return {
            "solved": False,
            "solving_papers": [],
            "reasoning": "",
            "opportunity": "This downstream gap remains an active research opening.",
        }

    solved = bool(data.get("solved"))
    indices = [int(i) for i in (data.get("solving_indices") or []) if isinstance(i, (int, float))]
    solving_papers = [candidates[i] for i in indices if 0 <= i < len(candidates)]
    return {
        "solved": solved,
        "solving_papers": solving_papers,
        "reasoning": str(data.get("reasoning") or "").strip(),
        "opportunity": str(data.get("opportunity") or "").strip(),
    }


def _recursive_downstream_check(
    idea: str,
    gap_title: str,
    gap_num: str,      # e.g. "3/6"
    solving_paper: dict,
    root_gap: dict,
    logs: list[dict],
    should_stop: Any,
    on_progress: Any,
    depth: int = 1,
    max_depth: int = 4,
) -> dict[str, Any] | None:
    """
    Run the recursive downstream verification for a solved gap.
    If the downstream gap is ALSO solved, recurse deeper (up to max_depth).
    Always searches until it finds an OPEN gap or exhausts max_depth.
    Returns a 'downstream' dict (which may have its own nested 'downstream'), or None if stopped.
    """

    def log(msg: str) -> None:
        logs.append({"time": _now_str(), "msg": msg})

    depth_tag = "↳ " * depth + "Deep Dive"
    solving_title = solving_paper.get("title", "the solving paper")
    solving_year = solving_paper.get("year") or "Unknown"

    # ── Step A: Extract downstream gap ──────────────────────────────────────
    log(f"[{gap_num} {depth_tag}] Extracting stated limitations from '{solving_title}'...")
    if should_stop and should_stop():
        return None

    downstream_gap = _extract_downstream_gap(idea, root_gap, solving_paper)
    if not downstream_gap:
        downstream_gap = f"Limitations and future work of {solving_title}"
    log(f"[{gap_num} {depth_tag}] Identified downstream gap: '{downstream_gap}'")

    # ── Step B: Build downstream queries ────────────────────────────────────
    log(f"[{gap_num} {depth_tag}] Generating targeted search queries for downstream gap...")
    if should_stop and should_stop():
        return None

    downstream_queries = _build_downstream_queries(idea, downstream_gap, solving_paper)
    log(f"[{gap_num} {depth_tag}] Searching credible venues published after {solving_year}...")

    # ── Step C: Search with venue-biased queries ─────────────────────────────
    if should_stop and should_stop():
        return None

    biased_queries = [f"{q} {_VENUE_BIAS}" for q in downstream_queries]
    search_result = search_sources(
        biased_queries,
        per_query=DOWNSTREAM_CANDIDATES,
        limit=DOWNSTREAM_CANDIDATES * len(biased_queries),
        recent_years=GAP_CHECK_YEARS + 1,
        max_queries=len(biased_queries),
    )
    downstream_candidates = search_result.get("papers") or []

    # Filter to papers published after solving paper's year
    if solving_year and str(solving_year).isdigit():
        cutoff = int(solving_year)
        downstream_candidates = [
            p for p in downstream_candidates
            if str(p.get("year") or "0").isdigit() and int(p.get("year") or 0) >= cutoff
        ]

    log(f"[{gap_num} {depth_tag}] Found {len(downstream_candidates)} candidate paper(s) from verified venues.")

    if should_stop and should_stop():
        return None

    # ── Step D: LLM verdict ──────────────────────────────────────────────────
    log(f"[{gap_num} {depth_tag}] Evaluating candidate papers for downstream gap resolution...")
    judgement = _judge_downstream(idea, downstream_gap, solving_paper, downstream_candidates[:8])

    status = "✅ SOLVED" if judgement["solved"] else "❌ STILL OPEN"
    log(f"[{gap_num} {depth_tag}] Verdict for downstream gap: {status}.")

    evaluated = [
        {
            "title": p.get("title", "Untitled"),
            "year": p.get("year", ""),
            "source": p.get("source", "arXiv"),
            "url": p.get("url", ""),
        }
        for p in downstream_candidates[:8]
    ]

    # ── Step E: Recurse if this downstream gap is ALSO solved ────────────────
    nested_downstream = None
    if judgement["solved"] and judgement["solving_papers"] and depth < max_depth:
        next_solver = judgement["solving_papers"][0]
        log(
            f"[{gap_num} {depth_tag}] ↳ Downstream solved — diving one level deeper "
            f"(depth {depth+1}/{max_depth})..."
        )
        if should_stop and should_stop():
            pass  # return what we have
        else:
            # The new "root gap" for the next level is this downstream gap
            next_root_gap = {"title": downstream_gap, "detail": downstream_gap}
            nested_downstream = _recursive_downstream_check(
                idea=idea,
                gap_title=downstream_gap,
                gap_num=gap_num,
                solving_paper=next_solver,
                root_gap=next_root_gap,
                logs=logs,
                should_stop=should_stop,
                on_progress=on_progress,
                depth=depth + 1,
                max_depth=max_depth,
            )
    elif judgement["solved"] and depth >= max_depth:
        log(
            f"[{gap_num} {depth_tag}] Max recursion depth ({max_depth}) reached — "
            "stopping recursive dive here."
        )

    return {
        "gap": downstream_gap,
        "depth": depth,
        "search_filter": f"Published after {solving_year} · peer-reviewed venues & arXiv cs.AI/CL/LG/SE/HC",
        "solved": judgement["solved"],
        "solving_papers": judgement["solving_papers"],
        "reasoning": judgement["reasoning"],
        "opportunity": judgement["opportunity"],
        "queries_used": downstream_queries,
        "candidates_found": len(downstream_candidates),
        "evaluated_papers": evaluated,
        "downstream": nested_downstream,  # nested recursion result
    }



# ── Main Entry Point ─────────────────────────────────────────────────────────

def check_gaps_resolved(
    idea: str,
    selected_papers: list[dict],
    all_gaps: list[dict],
    *,
    on_progress: Any = None,
    should_stop: Any = None,
) -> dict[str, Any]:
    """
    Analyzes selected papers to extract their research gaps, then for each gap:
    1. Checks whether recent publications have solved it (root verdict).
    2. If SOLVED → runs a recursive downstream check on the solving paper's limitations.

    Returns dict with keys: results, logs, stopped.
    """
    logs: list[dict] = []

    def log(msg: str) -> None:
        logs.append({"time": _now_str(), "msg": msg})

    paper_names = ", ".join(f"P{p.get('rank') or i+1}" for i, p in enumerate(selected_papers[:4]))
    log(f"Started recursive gap check on {len(selected_papers)} paper(s): {paper_names}")

    if on_progress:
        stopped = on_progress(0, 1, f"Analyzing selected papers ({paper_names}) for research gaps", logs)
        if stopped or (should_stop and should_stop()):
            log("Stopped by user.")
            return {"results": [], "logs": logs, "stopped": True}

    # ── Step 1: Extract paper-specific gaps ───────────────────────────────────
    relevant_gaps = find_gaps(idea, [], selected_papers)

    if not relevant_gaps and all_gaps:
        selected_paper_ids = {f"P{p.get('rank') or ''}" for p in selected_papers}
        for gap in all_gaps:
            supporters = gap.get("supported_by") or []
            gap_paper_ids = {str(s.get("id") or "").strip() for s in supporters}
            if not selected_paper_ids or gap_paper_ids & selected_paper_ids:
                relevant_gaps.append(gap)
        if not relevant_gaps:
            relevant_gaps = list(all_gaps)

    log(f"Extracted {len(relevant_gaps)} gap(s) to evaluate.")

    results: list[dict] = []
    total = len(relevant_gaps)

    for idx, gap in enumerate(relevant_gaps):
        if should_stop and should_stop():
            log("Gap check stopped by user.")
            return {"results": results, "logs": logs, "stopped": True}

        gap_title = str(gap.get("title") or "Research Gap").strip()
        gap_num = f"{idx+1}/{total}"

        # ── Step 2: Generate search queries ───────────────────────────────────
        log(f"[{gap_num}] Generating search queries for: '{gap_title}'")
        if on_progress:
            stopped = on_progress(idx + 1, total, f"Generating queries for: '{gap_title}'", logs)
            if stopped or (should_stop and should_stop()):
                log("Stopped by user.")
                return {"results": results, "logs": logs, "stopped": True}

        queries = _build_search_queries(idea, gap)
        if not queries:
            queries = [gap_title]
        log(f"[{gap_num}] Queries generated: {queries}")

        # ── Determine original paper year for date filtering ──────────────────
        supporters = gap.get("supported_by") or []
        original_paper_year: int | None = None
        for supporter in supporters:
            yr = supporter.get("year") or supporter.get("date", "")
            if yr:
                try:
                    y = int(str(yr)[:4])
                    if original_paper_year is None or y < original_paper_year:
                        original_paper_year = y
                except ValueError:
                    pass
        if not original_paper_year and selected_papers:
            for sp in selected_papers:
                yr = sp.get("year") or sp.get("date", "")
                try:
                    y = int(str(yr)[:4])
                    if original_paper_year is None or y < original_paper_year:
                        original_paper_year = y
                except (ValueError, TypeError):
                    pass

        if original_paper_year:
            log(f"[{gap_num}] Original paper year: {original_paper_year} — only accepting papers published AFTER this year.")

        # ── Step 3: Search recent papers ──────────────────────────────────────
        log(f"[{gap_num}] Searching arXiv, OpenAlex, Semantic Scholar & PubMed (last {GAP_CHECK_YEARS} years, {GAP_CANDIDATES} per query)...")
        if on_progress:
            stopped = on_progress(idx + 1, total, f"Searching recent papers for: '{gap_title}'", logs)
            if stopped or (should_stop and should_stop()):
                log("Stopped by user.")
                return {"results": results, "logs": logs, "stopped": True}

        search_result = search_sources(
            queries,
            per_query=GAP_CANDIDATES,
            limit=GAP_CANDIDATES * len(queries),
            recent_years=GAP_CHECK_YEARS,
            max_queries=len(queries),
        )
        candidates = search_result.get("papers") or []

        # Filter: only accept papers published AFTER original paper's year
        if original_paper_year:
            before_count = len(candidates)
            candidates = [
                p for p in candidates
                if str(p.get("year") or "0").isdigit() and int(p.get("year") or 0) > original_paper_year
            ]
            log(f"[{gap_num}] Date filter (>{original_paper_year}): {len(candidates)}/{before_count} candidate(s) pass.")

        log(f"[{gap_num}] {len(candidates)} candidate paper(s) to evaluate.")

        # ── Verbose: log every candidate title ────────────────────────────────
        for ci, cp in enumerate(candidates):
            log(f"[{gap_num}] Candidate {ci+1}: \"{cp.get('title','?')}\" ({cp.get('year','?')}) [{cp.get('source','?')}]")

        evaluated_candidates = [
            {
                "title": p.get("title", "Untitled"),
                "year": p.get("year", ""),
                "source": p.get("source", "arXiv"),
                "url": p.get("url", ""),
            }
            for p in candidates
        ]

        # ── Step 4: LLM judges root gap (domain-aware, strict) ────────────────
        log(f"[{gap_num}] Evaluating {len(candidates)} candidate(s) — domain-aware strict filter for: '{gap_title}'")
        if on_progress:
            stopped = on_progress(idx + 1, total, f"Evaluating candidates for: '{gap_title}'", logs)
            if stopped or (should_stop and should_stop()):
                log("Stopped by user.")
                return {"results": results, "logs": logs, "stopped": True}

        judgement = _judge_relevance(idea, gap, candidates, original_paper_year=original_paper_year)
        status_str = "✅ SOLVED" if judgement["solved"] else "❌ STILL OPEN"

        if judgement["solved"] and judgement["solving_papers"]:
            sp = judgement["solving_papers"][0]
            log(f"[{gap_num}] Verdict: {status_str} by '{sp.get('title', 'Unknown')}' ({sp.get('year', '')}).")
            log(f"[{gap_num}] Reason: {judgement.get('reasoning', '')}")
        else:
            log(f"[{gap_num}] Verdict: {status_str}.")
            log(f"[{gap_num}] Reason: {judgement.get('reasoning', 'No domain-matching paper found.')}")

        # ── Step 5: Recursive downstream check (only if SOLVED) ──────────────
        downstream = None
        if judgement["solved"] and judgement["solving_papers"]:
            if should_stop and should_stop():
                log("Stopped before downstream check.")
                results.append(_make_result(gap, gap_title, queries, candidates, evaluated_candidates, judgement, None))
                return {"results": results, "logs": logs, "stopped": True}

            solving_paper = judgement["solving_papers"][0]
            if on_progress:
                on_progress(
                    idx + 1, total,
                    f"Analyzing limitations of '{solving_paper.get('title', '')[:50]}'...",
                    logs,
                )

            downstream = _recursive_downstream_check(
                idea=idea,
                gap_title=gap_title,
                gap_num=gap_num,
                solving_paper=solving_paper,
                root_gap=gap,
                logs=logs,
                should_stop=should_stop,
                on_progress=on_progress,
            )

            if downstream is None:
                # Stopped during downstream check
                results.append(_make_result(gap, gap_title, queries, candidates, evaluated_candidates, judgement, None))
                return {"results": results, "logs": logs, "stopped": True}

        results.append(_make_result(gap, gap_title, queries, candidates, evaluated_candidates, judgement, downstream))

    log(f"Completed recursive gap check. Analyzed {total} gap(s).")
    return {"results": results, "logs": logs, "stopped": False}


def _make_result(
    gap: dict,
    gap_title: str,
    queries: list[str],
    candidates: list[dict],
    evaluated_candidates: list[dict],
    judgement: dict,
    downstream: dict | None,
) -> dict:
    return {
        "gap_title": gap_title,
        "gap_detail": gap.get("detail", ""),
        "gap_opportunity": gap.get("opportunity", ""),
        "supported_by": gap.get("supported_by") or [],
        "queries_used": queries,
        "candidates_found": len(candidates),
        "evaluated_papers": evaluated_candidates,
        "solved": judgement["solved"],
        "solving_papers": judgement["solving_papers"],
        "reasoning": judgement.get("reasoning", ""),
        "downstream": downstream,
    }
