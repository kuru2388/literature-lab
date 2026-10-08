"""Semantic Scholar search.

The keyless pool is shared by every anonymous caller, so the goal is to spend as
few requests as possible: one bulk call covers every keyword at once, sorted by
citation count so the most valuable work in the window comes back first. The
per-keyword relevance endpoint is kept as a fallback.
"""

from __future__ import annotations

import os
import random
import time

import requests

from src.ratelimits import record_call, record_http

NAME = "Semantic Scholar"
BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
MIN_DELAY_SECONDS = 1.2

# `tldr` is valid on /paper/search but rejected by /paper/search/bulk.
BULK_FIELDS = ",".join(
    [
        "title",
        "abstract",
        "year",
        "publicationDate",
        "authors",
        "externalIds",
        "openAccessPdf",
        "venue",
        "citationCount",
        "fieldsOfStudy",
        "publicationTypes",
    ]
)
SEARCH_FIELDS = BULK_FIELDS + ",tldr"

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_at = time.time()


def _headers() -> dict[str, str]:
    key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    return {"x-api-key": key} if key else {}


def _get(url: str, params: dict) -> dict:
    attempts = 4 if _headers() else 3
    for attempt in range(attempts):
        _throttle()
        response = requests.get(url, params=params, headers=_headers(), timeout=90)
        record_http("semantic_scholar", response)
        if response.status_code == 429:
            time.sleep(4 * (attempt + 1) + random.uniform(0, 1.5))
            continue
        if response.status_code == 400:
            raise RuntimeError(f"rejected the query: {response.text[:80]}")
        response.raise_for_status()
        return response.json() or {}
    raise RuntimeError("rate limited; add a free Semantic Scholar key in Settings")


def search_all(
    terms: list[str],
    *,
    max_results: int = 12,
    from_year: int | None = None,
) -> list[dict]:
    """One bulk request for every keyword, blending most-cited with newest."""
    phrases = [f'"{t.strip()}"' for t in terms if t and t.strip()]
    if not phrases:
        return []

    params: dict[str, object] = {
        "query": " | ".join(phrases),
        "fields": BULK_FIELDS,
        "sort": "citationCount:desc",
    }
    if from_year:
        params["year"] = f"{from_year}-"

    try:
        rows = (_get(BULK_URL, params).get("data") or [])
    except RuntimeError:
        return _search_per_term(terms, max_results=max_results, from_year=from_year)

    papers = [_normalize(row) for row in rows if row.get("title")]
    papers = [p for p in papers if p["abstract"]]
    return _blend(papers, max_results)


def _blend(papers: list[dict], max_results: int) -> list[dict]:
    """Citation sort alone favours older work, so keep room for fresh papers."""
    if len(papers) <= max_results:
        return papers
    top_cited = papers[: max(1, int(max_results * 0.7))]
    chosen = {id(p) for p in top_cited}
    newest = sorted(papers, key=lambda p: p.get("published") or "", reverse=True)
    for paper in newest:
        if len(top_cited) >= max_results:
            break
        if id(paper) not in chosen:
            top_cited.append(paper)
            chosen.add(id(paper))
    return top_cited


def _search_per_term(
    terms: list[str], *, max_results: int, from_year: int | None
) -> list[dict]:
    hits: list[dict] = []
    for term in terms[:3]:
        params: dict[str, object] = {
            "query": term,
            "limit": max(1, min(max_results, 20)),
            "fields": SEARCH_FIELDS,
        }
        if from_year:
            params["year"] = f"{from_year}-"
        data = _get(SEARCH_URL, params)
        hits.extend(
            _normalize(item) for item in (data.get("data") or []) if item.get("title")
        )
    return hits


def search(query: str, *, max_results: int = 8, from_year: int | None = None) -> list[dict]:
    return search_all([query], max_results=max_results, from_year=from_year)


def _normalize(item: dict) -> dict:
    external = item.get("externalIds") or {}
    doi = external.get("DOI")
    arxiv_id = external.get("ArXiv")
    paper_id = item.get("paperId") or ""
    types = item.get("publicationTypes") or []
    pdf_url = (item.get("openAccessPdf") or {}).get("url") or None
    published = item.get("publicationDate") or ""
    if not published and item.get("year"):
        published = f"{item['year']}-01-01"

    return {
        "source": NAME,
        "paper_id": paper_id,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": item.get("title") or "",
        "abstract": item.get("abstract") or ((item.get("tldr") or {}).get("text") or ""),
        "authors": [
            (author.get("name") or "") for author in (item.get("authors") or [])
        ][:12],
        "published": published,
        "year": item.get("year"),
        "url": (
            (doi and f"https://doi.org/{doi}")
            or (arxiv_id and f"https://arxiv.org/abs/{arxiv_id}")
            or (paper_id and f"https://www.semanticscholar.org/paper/{paper_id}")
            or ""
        ),
        "pdf_url": pdf_url,
        "venue": item.get("venue") or "",
        "citations": item.get("citationCount"),
        "fields_of_study": item.get("fields_of_study") or item.get("fieldsOfStudy") or [],
        "publication_types": types,
        "is_dataset": any("dataset" in str(t).lower() for t in types),
    }
