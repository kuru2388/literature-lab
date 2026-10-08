"""arXiv search and PDF download. Respects the API's ~3s courtesy delay."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

from src.db import PAPERS_DIR
from src.ratelimits import record_call

ATOM = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
SEARCH_URL = "http://export.arxiv.org/api/query"
PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
HEADERS = {"User-Agent": "literature-research-agent/1.0 (mailto:local@localhost)"}
MIN_DELAY_SECONDS = 3.0

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_at = time.time()


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return re.sub(r"\s+", " ", element.text).strip()


def _arxiv_id_from_entry(entry: ET.Element) -> str:
    raw_id = _text(entry.find(f"{{{ATOM}}}id"))
    # http://arxiv.org/abs/2301.12345v2 -> 2301.12345v2
    return raw_id.rsplit("/", 1)[-1]


def _year_from_published(published: str) -> int | None:
    match = re.match(r"(\d{4})", published or "")
    return int(match.group(1)) if match else None


STOPWORDS = {
    "the", "a", "an", "for", "and", "of", "to", "in", "on", "with", "using",
    "from", "into", "about", "that", "this", "via", "based",
}


def _search_query(query: str) -> str:
    cleaned = query.strip().strip('"').strip("'")
    if cleaned.lower().startswith("id:"):
        return cleaned
    tokens = re.findall(r"[A-Za-z0-9\-]+", cleaned)
    tokens = [t for t in tokens if t.lower() not in STOPWORDS and len(t) > 1][:3]
    if not tokens:
        return f"all:{cleaned[:80]}"
    return " AND ".join(f"all:{t}" for t in tokens)


RECENT_YEARS = 7


def cutoff_date(years: int = RECENT_YEARS) -> str:
    """ISO date that a paper must be published on or after."""
    today = datetime.now(timezone.utc).date()
    try:
        start = today.replace(year=today.year - years)
    except ValueError:  # Feb 29
        start = today.replace(year=today.year - years, day=28)
    return start.isoformat()


def _date_clause(years: int = RECENT_YEARS) -> str:
    start = cutoff_date(years).replace("-", "")
    end = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"submittedDate:[{start}0000 TO {end}2359]"


def is_recent(paper: dict, years: int = RECENT_YEARS) -> bool:
    published = (paper.get("published") or "")[:10]
    if not published:
        year = paper.get("year")
        return bool(year) and int(year) >= datetime.now(timezone.utc).year - years
    return published >= cutoff_date(years)


def _fetch_feed(search_query: str, max_results: int) -> str:
    last_error: Exception | None = None
    throttled = False
    for attempt in range(2):
        _throttle()
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            response = requests.get(
                SEARCH_URL,
                params=params,
                headers=HEADERS,
                timeout=60,
            )
            if response.status_code == 429:
                throttled = True
                record_call("arxiv", status=429)
                time.sleep(4 * (attempt + 1))
                continue
            response.raise_for_status()
            record_call("arxiv", status=response.status_code)
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    if throttled:
        raise RuntimeError("rate limited by arXiv")
    if last_error:
        raise last_error
    raise RuntimeError("arXiv search failed")


def search_arxiv(
    query: str,
    *,
    max_results: int = 8,
    recent_years: int | None = RECENT_YEARS,
) -> list[dict]:
    """Search arXiv and return normalized paper dicts, newest work first."""
    base = _search_query(query)
    scoped = f"{base} AND {_date_clause(recent_years)}" if recent_years else base

    papers = _parse_feed(_fetch_feed(scoped, max_results))
    if not papers:
        # One looser retry only: arXiv asks for a 3s gap, so extra tries cost real time.
        tokens = re.findall(r"[A-Za-z0-9\-]+", query)
        tokens = [t for t in tokens if t.lower() not in STOPWORDS and len(t) > 1][:2]
        if tokens:
            loose = " AND ".join(f"all:{t}" for t in tokens)
            papers = _parse_feed(_fetch_feed(loose, max_results))

    if recent_years:
        papers = [p for p in papers if is_recent(p, recent_years)]
    return papers


def _parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)

    papers: list[dict] = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        arxiv_id = _arxiv_id_from_entry(entry)
        authors = [
            _text(author.find(f"{{{ATOM}}}name"))
            for author in entry.findall(f"{{{ATOM}}}author")
        ]
        published = _text(entry.find(f"{{{ATOM}}}published"))
        papers.append(
            {
                "source": "arXiv",
                "paper_id": arxiv_id,
                "arxiv_id": arxiv_id,
                "doi": _text(entry.find(f"{{{ARXIV_NS}}}doi")) or None,
                "venue": _text(entry.find(f"{{{ARXIV_NS}}}journal_ref")),
                "citations": None,
                "title": _text(entry.find(f"{{{ATOM}}}title")),
                "abstract": _text(entry.find(f"{{{ATOM}}}summary")),
                "authors": [a for a in authors if a],
                "published": published,
                "year": _year_from_published(published),
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": PDF_URL.format(arxiv_id=arxiv_id),
                "categories": [
                    (cat.get("term") or "")
                    for cat in entry.findall(f"{{{ATOM}}}category")
                ],
            }
        )
    return papers


def search_many(
    queries: list[str],
    *,
    per_query: int = 8,
    limit: int = 30,
    recent_years: int | None = RECENT_YEARS,
) -> list[dict]:
    """Run several queries, de-duplicate by arXiv id, keep insertion order."""
    seen: set[str] = set()
    merged: list[dict] = []
    for query in queries:
        if not query.strip():
            continue
        try:
            hits = search_arxiv(
                query.strip(),
                max_results=per_query,
                recent_years=recent_years,
            )
        except Exception:
            continue
        for paper in hits:
            key = paper["arxiv_id"].split("v")[0]
            if key in seen:
                continue
            seen.add(key)
            merged.append(paper)
            if len(merged) >= limit:
                return merged
    return merged


def download_pdf(arxiv_id: str, dest_dir: Path | None = None) -> Path:
    """Download a paper PDF. Skips if the file already exists."""
    dest_dir = dest_dir or PAPERS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_id = quote_plus(arxiv_id)
    path = dest_dir / f"{safe_id}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path

    _throttle()
    url = PDF_URL.format(arxiv_id=arxiv_id)
    response = requests.get(url, headers=HEADERS, timeout=120, stream=True)
    response.raise_for_status()
    tmp = path.with_suffix(".pdf.part")
    with tmp.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                handle.write(chunk)
    tmp.replace(path)
    return path
