"""Save open-access PDFs from any source into data/papers/."""

from __future__ import annotations

import re
from pathlib import Path

import requests

from src.db import PAPERS_DIR
from src.tools.arxiv import HEADERS, download_pdf

MIN_PDF_BYTES = 1000


def save_pdf(paper: dict, dest_dir: Path | None = None) -> Path:
    """Download a paper's PDF. Raises if the source has no usable open PDF."""
    arxiv_id = paper.get("arxiv_id")
    if arxiv_id:
        return download_pdf(arxiv_id, dest_dir)

    url = paper.get("pdf_url")
    if not url:
        raise ValueError("no open-access PDF for this paper")

    dest_dir = dest_dir or PAPERS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{_safe_name(paper)}.pdf"
    if path.exists() and path.stat().st_size > MIN_PDF_BYTES:
        return path

    response = requests.get(
        url, headers=HEADERS, timeout=120, stream=True, allow_redirects=True
    )
    response.raise_for_status()

    tmp = path.with_suffix(".pdf.part")
    first_chunk = True
    with tmp.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            if first_chunk:
                if not chunk.lstrip()[:5].startswith(b"%PDF"):
                    handle.close()
                    tmp.unlink(missing_ok=True)
                    raise ValueError("link did not return a PDF (likely a landing page)")
                first_chunk = False
            handle.write(chunk)

    if not tmp.exists() or tmp.stat().st_size <= MIN_PDF_BYTES:
        tmp.unlink(missing_ok=True)
        raise ValueError("PDF was empty")

    tmp.replace(path)
    return path


def _safe_name(paper: dict) -> str:
    source = (paper.get("source") or "paper").lower().replace(" ", "-")
    ident = paper.get("doi") or paper.get("paper_id") or paper.get("title") or "unknown"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(ident)).strip("-")
    return f"{source}-{slug[:80]}"
