"""OpenAlex search. Free, no API key; a mailto keeps us in the polite pool."""

from __future__ import annotations

import os
import re
import time

import requests

from src.ratelimits import record_call, record_http

NAME = "OpenAlex"
API_URL = "https://api.openalex.org/works"
CONTACT = "local@localhost"
MIN_DELAY_SECONDS = 0.6
FIELDS = ",".join(
    [
        "id",
        "doi",
        "display_name",
        "publication_date",
        "publication_year",
        "authorships",
        "abstract_inverted_index",
        "best_oa_location",
        "open_access",
        "primary_location",
        "cited_by_count",
        "type",
    ]
)

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_at = time.time()


def search(query: str, *, max_results: int = 8, from_date: str | None = None) -> list[dict]:
    # Searching title+abstract keeps hits on topic; plain `search` also matches full text.
    filters = ["type:article", f"title_and_abstract.search:{query}"]
    if from_date:
        filters.append(f"from_publication_date:{from_date}")

    params = {
        "per_page": max(1, min(max_results, 50)),
        "select": FIELDS,
        "filter": ",".join(filters),
        "mailto": _contact(),
    }

    for attempt in range(3):
        _throttle()
        response = requests.get(API_URL, params=params, timeout=45)
        record_http("openalex", response)
        if response.status_code == 429:
            time.sleep(_retry_after(response, attempt))
            continue
        response.raise_for_status()
        results = (response.json() or {}).get("results") or []
        return [_normalize(work) for work in results if work.get("display_name")]

    raise RuntimeError("rate limited by OpenAlex")


def _contact() -> str:
    """OpenAlex only grants the polite (higher) rate limit when a mailto is sent."""
    from src.settings import get_contact_email

    return get_contact_email() or CONTACT


def _retry_after(response: requests.Response, attempt: int) -> float:
    match = re.search(r"retry in (\d+)", response.text or "", re.IGNORECASE)
    if match:
        return min(int(match.group(1)), 40) + 1
    return 4 * (attempt + 1)


def _normalize(work: dict) -> dict:
    doi = (work.get("doi") or "").replace("https://doi.org/", "") or None
    oa = work.get("open_access") or {}
    best = work.get("best_oa_location") or {}
    location = work.get("primary_location") or {}
    venue = ((location.get("source") or {}).get("display_name")) or ""
    authors = [
        ((entry.get("author") or {}).get("display_name") or "")
        for entry in (work.get("authorships") or [])
    ]
    published = work.get("publication_date") or ""

    return {
        "source": NAME,
        "paper_id": (work.get("id") or "").rsplit("/", 1)[-1],
        "doi": doi,
        "title": work.get("display_name") or "",
        "abstract": _abstract(work.get("abstract_inverted_index")),
        "authors": [a for a in authors if a][:12],
        "published": published,
        "year": work.get("publication_year"),
        "url": (doi and f"https://doi.org/{doi}") or work.get("id") or "",
        "pdf_url": best.get("pdf_url") or oa.get("oa_url"),
        "venue": venue,
        "citations": work.get("cited_by_count"),
    }


def _abstract(inverted_index: dict | None) -> str:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the sentence order."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in inverted_index.items():
        for spot in spots or []:
            positions.append((spot, word))
    positions.sort()
    return " ".join(word for _, word in positions)
