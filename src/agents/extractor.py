"""Extractor agent: pull problem, method, findings, datasets, limitations from PDFs."""

from __future__ import annotations

from src.llm import chat_json
from src.tools.pdf import extract_for_research


def extract_paper(idea: str, paper: dict) -> dict:
    updated = dict(paper)
    pdf_path = paper.get("pdf_path")
    read = extract_for_research(pdf_path) if pdf_path else {}
    text = read.get("text") or ""
    has_file = bool(pdf_path)
    if not text:
        text = paper.get("abstract") or ""
        updated["extracted_from"] = "abstract"
        updated["read_strategy"] = "abstract only"
        updated["page_count"] = read.get("page_count") or 0
        updated["pages_read"] = []
        updated["section_jumps"] = read.get("section_jumps") or []
        updated["has_pdf"] = has_file
    else:
        updated["extracted_from"] = "pdf"
        updated["read_strategy"] = read.get("strategy") or "pdf"
        updated["page_count"] = read.get("page_count") or 0
        updated["pages_read"] = read.get("pages_read") or []
        updated["sections_found"] = read.get("sections_found") or []
        updated["section_jumps"] = read.get("section_jumps") or []
        updated["has_pdf"] = True

    data = chat_json(
        system=(
            "You extract structured notes from selected sections of a research paper. "
            "The text may be intro + method + conclusion only, not the full PDF. "
            "Be concrete. Quote methods, datasets, metrics, and limitations."
        ),
        user=(
            f"Project idea:\n{idea}\n\n"
            f"Title: {paper.get('title')}\n"
            f"Authors: {', '.join(paper.get('authors') or [])}\n"
            f"Source: {paper.get('source')} · {paper.get('venue') or 'preprint'}\n"
            f"Read strategy: {updated.get('read_strategy')}\n"
            f"Pages read: {updated.get('pages_read')}\n\n"
            f"Paper text:\n{text}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "problem": "the task this paper addresses",\n'
            '  "method": "model or method name, short",\n'
            '  "features": "input features or modalities, comma-separated",\n'
            '  "metrics": "evaluation metrics and scores if stated",\n'
            '  "key_findings": "",\n'
            '  "datasets": "dataset names used",\n'
            '  "limitations": "",\n'
            '  "relevance": ""\n'
            "}"
        ),
    )
    if not isinstance(data, dict):
        data = {}

    updated["problem"] = str(data.get("problem") or "")
    updated["method"] = str(data.get("method") or "")
    updated["features"] = str(data.get("features") or "")
    updated["metrics"] = str(data.get("metrics") or "")
    updated["key_findings"] = str(data.get("key_findings") or "")
    updated["datasets"] = str(data.get("datasets") or "")
    updated["limitations"] = str(data.get("limitations") or "")
    updated["relevance"] = str(data.get("relevance") or "")
    return updated


def extract_papers(idea: str, papers: list[dict]) -> list[dict]:
    return [extract_paper(idea, paper) for paper in papers]
