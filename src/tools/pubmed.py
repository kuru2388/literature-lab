"""PubMed search through NCBI E-utilities: esearch for ids, efetch for records."""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET

import requests

from src.ratelimits import record_call

NAME = "PubMed"
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "literature-research-agent"
CONTACT = "local@localhost"
MIN_DELAY_SECONDS = 0.4

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_at = time.time()


def _common_params() -> dict[str, str]:
    params = {"tool": TOOL, "email": CONTACT}
    key = (os.getenv("NCBI_API_KEY") or "").strip()
    if key:
        params["api_key"] = key
    return params


def search(query: str, *, max_results: int = 8, from_year: int | None = None) -> list[dict]:
    # Title/Abstract keeps PubMed on topic; a bare term matches MeSH senses such as
    # "agent" meaning a drug, which floods results with unrelated clinical papers.
    ids = _search_ids(
        f'"{query}"[Title/Abstract]', max_results=max_results, from_year=from_year
    )
    if not ids and len(query.split()) >= 2:
        ids = _search_ids(
            f"{query}[Title/Abstract]", max_results=max_results, from_year=from_year
        )
    if not ids:
        return []
    return [
        paper for paper in _fetch_records(ids) if not _is_placeholder(paper["title"])
    ]


def _is_placeholder(title: str) -> bool:
    return title.strip("[]. ").lower() in {"not available", "no title available"}


def _search_ids(query: str, *, max_results: int, from_year: int | None) -> list[str]:
    params = {
        **_common_params(),
        "db": "pubmed",
        "term": query,
        "retmax": max(1, min(max_results, 25)),
        "retmode": "json",
        "sort": "relevance",
    }
    if from_year:
        params.update(
            {"datetype": "pdat", "mindate": str(from_year), "maxdate": "3000"}
        )

    _throttle()
    response = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=45)
    record_call("pubmed", status=response.status_code)
    response.raise_for_status()
    payload = (response.json() or {}).get("esearchresult") or {}
    return [str(pmid) for pmid in (payload.get("idlist") or [])]


def _fetch_records(pmids: list[str]) -> list[dict]:
    params = {
        **_common_params(),
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    _throttle()
    response = requests.get(f"{BASE_URL}/efetch.fcgi", params=params, timeout=60)
    record_call("pubmed", status=response.status_code)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    papers: list[dict] = []
    for article in root.findall(".//PubmedArticle"):
        paper = _normalize(article)
        if paper:
            papers.append(paper)
    return papers


def _normalize(article: ET.Element) -> dict | None:
    pmid = _text(article.find(".//PMID"))
    title = _join(article.find(".//ArticleTitle"))
    if not pmid or not title:
        return None

    abstract = " ".join(
        _join(node) for node in article.findall(".//Abstract/AbstractText")
    ).strip()

    authors: list[str] = []
    for author in article.findall(".//Author"):
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials"))
        collective = _text(author.find("CollectiveName"))
        name = f"{last} {initials}".strip() or collective
        if name:
            authors.append(name)

    doi = None
    for node in article.findall(".//ArticleId"):
        if (node.get("IdType") or "").lower() == "doi":
            doi = _text(node)
            break

    return {
        "source": NAME,
        "paper_id": pmid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors[:12],
        "published": _published(article),
        "year": _year(article),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "pdf_url": None,
        "venue": _text(article.find(".//Journal/Title")),
        "citations": None,
    }


def _published(article: ET.Element) -> str:
    year = _year(article)
    if not year:
        return ""
    node = article.find(".//ArticleDate") or article.find(".//PubDate")
    month = _month_number(_text(node.find("Month")) if node is not None else "")
    day = _text(node.find("Day")) if node is not None else ""
    day_num = int(day) if day.isdigit() else 1
    return f"{year:04d}-{month:02d}-{day_num:02d}"


def _year(article: ET.Element) -> int | None:
    for path in (".//ArticleDate/Year", ".//PubDate/Year", ".//PubDate/MedlineDate"):
        raw = _text(article.find(path))
        digits = "".join(ch for ch in raw[:4] if ch.isdigit())
        if len(digits) == 4:
            return int(digits)
    return None


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _month_number(raw: str) -> int:
    raw = raw.strip().lower()
    if raw.isdigit():
        return max(1, min(int(raw), 12))
    return MONTHS.get(raw[:3], 1)


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _join(node: ET.Element | None) -> str:
    """Flatten mixed-content nodes such as titles with inline italics."""
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())
