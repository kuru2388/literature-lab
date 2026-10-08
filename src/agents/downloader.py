"""Downloader agent: save open-access PDFs to data/papers/."""

from __future__ import annotations

from pathlib import Path

from src.tools.download import save_pdf


def download_papers(papers: list[dict]) -> list[dict]:
    results: list[dict] = []
    for paper in papers:
        updated = dict(paper)
        try:
            path: Path = save_pdf(paper)
            updated["pdf_path"] = str(path)
            updated["download_error"] = None
        except Exception as exc:
            # Paywalled or landing-page-only papers still get read from the abstract.
            updated["pdf_path"] = None
            updated["download_error"] = str(exc)[:160]
        results.append(updated)
    return results
