"""Search every paper source in parallel and merge the hits into one list."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.tools import openalex, pubmed, semantic_scholar
from src.tools.arxiv import RECENT_YEARS, cutoff_date, is_recent, search_arxiv
from src.tools.corpus import corpus_fields, load_corpus_sizes

SOURCES = [
    {"id": "arxiv", "name": "arXiv", "site": "arxiv.org"},
    {"id": "openalex", "name": openalex.NAME, "site": "openalex.org"},
    {"id": "semantic_scholar", "name": semantic_scholar.NAME, "site": "semanticscholar.org"},
    {"id": "pubmed", "name": pubmed.NAME, "site": "pubmed.ncbi.nlm.nih.gov"},
]


def source_catalog(*, refresh: bool = False) -> list[dict]:
    """Sources the app searches, for display before a run starts."""
    sizes = load_corpus_sizes(refresh=refresh)
    return [
        dict(source, found=0, kept=0, **corpus_fields(source["id"], sizes))
        for source in SOURCES
    ]


def search_sources(
    queries: list[str],
    *,
    per_query: int = 10,
    limit: int = 80,
    recent_years: int | None = RECENT_YEARS,
    max_queries: int = 8,
) -> dict:
    """Run the queries against all sources, de-duplicate, and count per site."""
    terms = [q.strip() for q in queries if q and q.strip()][:max_queries]
    if not terms:
        return {"papers": [], "counts": source_catalog(), "window": _window(recent_years)}

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {
            source["id"]: pool.submit(
                _run_source, source["id"], terms, per_query, recent_years
            )
            for source in SOURCES
        }
        raw: dict[str, list[dict]] = {}
        errors: dict[str, str | None] = {}
        for source_id, future in futures.items():
            try:
                raw[source_id], errors[source_id] = future.result()
            except Exception as exc:
                raw[source_id], errors[source_id] = [], str(exc)[:120]

    merged: dict[str, dict] = {}
    order: list[str] = []
    kept_by_source: dict[str, int] = {source["id"]: 0 for source in SOURCES}

    # Round-robin so one source cannot crowd out the others, but keep as many
    # unique hits as the overall limit allows (do not drop most of arXiv).
    queues = {source["id"]: list(raw.get(source["id"], [])) for source in SOURCES}
    while len(order) < limit and any(queues.values()):
        progressed = False
        for source in SOURCES:
            source_id = source["id"]
            queue = queues.get(source_id) or []
            if not queue or len(order) >= limit:
                continue
            paper = queue.pop(0)
            key = _dedupe_key(paper)
            if not key:
                continue
            progressed = True
            if key in merged:
                _absorb(merged[key], paper)
                continue
            merged[key] = paper
            order.append(key)
            kept_by_source[source_id] += 1
        if not progressed:
            break

    sizes = load_corpus_sizes()
    counts = [
        dict(
            source,
            found=len(raw.get(source["id"], [])),
            kept=kept_by_source[source["id"]],
            error=errors.get(source["id"]),
            **corpus_fields(source["id"], sizes),
        )
        for source in SOURCES
    ]
    return {
        "papers": [merged[key] for key in order],
        "counts": counts,
        "window": _window(recent_years),
    }


def _run_source(
    source_id: str,
    terms: list[str],
    per_query: int,
    recent_years: int | None,
) -> tuple[list[dict], str | None]:
    """Query one source for every term. Returns its hits plus the last error."""
    from_year = (
        datetime.now(timezone.utc).year - recent_years if recent_years else None
    )
    if source_id == "semantic_scholar":
        # S2 takes every keyword in a single bulk request, so it never loops.
        try:
            found = semantic_scholar.search_all(
                terms, max_results=max(per_query * 3, 24), from_year=from_year
            )
        except Exception as exc:
            return [], str(exc)[:120]
        kept = _unique_papers(
            p for p in (_prepare(p, recent_years) for p in found) if p
        )
        return kept, None

    hits: list[dict] = []
    error: str | None = None
    for term in terms:
        try:
            if source_id == "arxiv":
                found = search_arxiv(
                    term, max_results=per_query, recent_years=recent_years
                )
            elif source_id == "openalex":
                found = openalex.search(
                    term,
                    max_results=per_query,
                    from_date=cutoff_date(recent_years) if recent_years else None,
                )
            elif source_id == "pubmed":
                found = pubmed.search(
                    term, max_results=per_query, from_year=from_year
                )
            else:
                found = []
        except Exception as exc:
            error = str(exc)[:120]
            if "rate limit" in error.lower():
                # Stop hitting a throttled source; the remaining terms would only wait.
                break
            continue
        hits.extend(_prepare(paper, recent_years) for paper in found)
    kept = _unique_papers(paper for paper in hits if paper)
    return kept, (None if kept else error)


def _unique_papers(papers) -> list[dict]:
    """Drop duplicate hits from the same source (same paper, several tags)."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for paper in papers:
        key = _dedupe_key(paper)
        if not key:
            continue
        if key in merged:
            _absorb(merged[key], paper)
            continue
        merged[key] = paper
        order.append(key)
    return [merged[key] for key in order]


def _prepare(paper: dict, recent_years: int | None) -> dict | None:
    if not (paper.get("title") or "").strip():
        return None
    if recent_years and paper.get("published") and not is_recent(paper, recent_years):
        return None
    paper.setdefault("source", "arXiv")
    paper.setdefault("paper_id", paper.get("arxiv_id") or "")
    paper.setdefault("doi", None)
    paper.setdefault("venue", "")
    paper.setdefault("citations", None)
    paper.setdefault("pdf_url", None)
    paper.setdefault("also_in", [])
    paper.setdefault("fields_of_study", [])
    paper.setdefault("publication_types", [])
    paper.setdefault("is_dataset", False)
    return paper


def _dedupe_key(paper: dict) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    arxiv_id = (paper.get("arxiv_id") or "").split("v")[0].strip().lower()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    title = re.sub(r"[^a-z0-9]+", " ", (paper.get("title") or "").lower()).strip()
    if title:
        return f"title:{title}"
    return ""


def _absorb(kept: dict, duplicate: dict) -> None:
    """Same paper from a second site: keep the richer copy, remember both names."""
    name = duplicate.get("source")
    if name and name != kept.get("source") and name not in kept["also_in"]:
        kept["also_in"].append(name)
    for field in (
        "doi",
        "pdf_url",
        "venue",
        "arxiv_id",
        "published",
        "year",
        "fields_of_study",
        "publication_types",
    ):
        if not kept.get(field) and duplicate.get(field):
            kept[field] = duplicate[field]
    if len(duplicate.get("abstract") or "") > len(kept.get("abstract") or ""):
        kept["abstract"] = duplicate["abstract"]
    if (duplicate.get("citations") or 0) > (kept.get("citations") or 0):
        kept["citations"] = duplicate["citations"]
    kept["is_dataset"] = bool(kept.get("is_dataset") or duplicate.get("is_dataset"))


def _window(recent_years: int | None) -> str:
    return f"last {recent_years} years" if recent_years else "all years"
