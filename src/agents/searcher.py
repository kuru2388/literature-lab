"""Searcher agent: look up the verified tags on every paper source."""

from __future__ import annotations

from src.tools.arxiv import RECENT_YEARS
from src.tools.sources import search_sources, source_catalog


def search_papers(
    tags: list[str],
    *,
    limit: int = 80,
    recent_years: int | None = RECENT_YEARS,
    min_results: int = 8,
) -> dict:
    """Search arXiv, OpenAlex, Semantic Scholar and PubMed for the verified tags.

    Recent work only; the window widens to all years if too few papers come back.
    Uses more queries per source and a higher cap to cast a wider net for accuracy.
    """
    terms = [t for t in tags if t and t.strip()]
    if not terms:
        return {"papers": [], "counts": source_catalog(), "window": "no tags"}

    # Use more queries and a higher per-query limit for better coverage
    found = search_sources(
        terms, per_query=12, limit=limit, recent_years=recent_years, max_queries=12
    )

    if recent_years and len(found["papers"]) < min_results:
        wider = search_sources(
            terms, per_query=12, limit=limit, recent_years=None, max_queries=12
        )
        if len(wider["papers"]) > len(found["papers"]):
            wider["window"] = (
                f"last {recent_years} years, widened (too few recent hits)"
            )
            return wider

    return found
