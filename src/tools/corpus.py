"""How many papers each library holds, for the source list on the home page."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

import requests

from src.db import DATA_DIR

CACHE_PATH = DATA_DIR / "corpus.json"
CACHE_HOURS = 24

# Public catalog sizes, refreshed August 2026. Live probes overwrite these.
FALLBACK = {
    "arxiv": 3_140_000,
    "openalex": 324_000_000,
    "semantic_scholar": 200_000_000,
    "pubmed": 40_000_000,
}

_lock = threading.Lock()
_refreshing = False


def format_papers(count: int) -> str:
    """Turn 3_140_000 into 'about 3.1 million papers'."""
    n = max(0, int(count or 0))
    if n >= 1_000_000_000:
        value = n / 1_000_000_000
        shown = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"about {shown} billion papers"
    if n >= 1_000_000:
        value = n / 1_000_000
        shown = f"{value:.0f}" if value >= 100 else f"{value:.1f}".rstrip("0").rstrip(".")
        return f"about {shown} million papers"
    if n >= 1_000:
        return f"about {n:,} papers"
    return f"{n} papers"


def corpus_fields(source_id: str, sizes: dict[str, int] | None = None) -> dict[str, Any]:
    count = int((sizes or load_corpus_sizes()).get(source_id) or FALLBACK.get(source_id) or 0)
    return {"corpus": count, "corpus_label": format_papers(count)}


def load_corpus_sizes(*, refresh: bool = False) -> dict[str, int]:
    """Return cached catalog sizes, or the fallbacks. Optionally refresh in the background."""
    cached = _read_cache()
    sizes = {key: int(cached["sizes"].get(key) or FALLBACK[key]) for key in FALLBACK}
    stale = _is_stale(cached.get("updated_at"))
    if refresh or stale:
        _kick_refresh()
    return sizes


def _read_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {"sizes": dict(FALLBACK), "updated_at": ""}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"sizes": dict(FALLBACK), "updated_at": ""}
    sizes = dict(FALLBACK)
    for key, value in (data.get("sizes") or {}).items():
        try:
            sizes[key] = int(value)
        except (TypeError, ValueError):
            continue
    return {"sizes": sizes, "updated_at": data.get("updated_at") or ""}


def _is_stale(updated_at: str | None) -> bool:
    if not updated_at:
        return True
    try:
        stamp = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - stamp
    return age.total_seconds() > CACHE_HOURS * 3600


def _kick_refresh() -> None:
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True
    threading.Thread(target=_refresh_cache, daemon=True).start()


def _refresh_cache() -> None:
    global _refreshing
    try:
        sizes = dict(FALLBACK)
        probes = {
            "arxiv": _arxiv_total,
            "openalex": _openalex_total,
            "semantic_scholar": _semantic_scholar_total,
            "pubmed": _pubmed_total,
        }
        for key, probe in probes.items():
            try:
                count = probe()
            except Exception:
                continue
            if count and count > 100_000:
                sizes[key] = int(count)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(
                {
                    "sizes": sizes,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        with _lock:
            _refreshing = False


def _arxiv_total() -> int | None:
    response = requests.get("https://arxiv.org/stats/monthly_submissions", timeout=6)
    response.raise_for_status()
    match = re.search(r"articles available is ([\d,]+)", response.text, re.I)
    if not match:
        match = re.search(r"Total number of submissions[^\d]+([\d,]+)", response.text, re.I)
    return int(match.group(1).replace(",", "")) if match else None


def _openalex_total() -> int | None:
    from src.tools.openalex import API_URL, _contact

    response = requests.get(
        API_URL,
        params={"per_page": 1, "mailto": _contact()},
        timeout=8,
    )
    response.raise_for_status()
    count = ((response.json() or {}).get("meta") or {}).get("count")
    return int(count) if count else None


def _semantic_scholar_total() -> int | None:
    # Bulk search returns a `total` for the query; a tiny common word is a lower bound
    # of the catalog, not a perfect census. Keep it only if it looks catalog-sized.
    from src.tools.semantic_scholar import BULK_URL, _headers

    response = requests.get(
        BULK_URL,
        params={"query": "the", "fields": "title"},
        headers=_headers(),
        timeout=10,
    )
    response.raise_for_status()
    total = (response.json() or {}).get("total")
    if not total:
        return None
    total = int(total)
    return total if total >= 50_000_000 else None


def _pubmed_total() -> int | None:
    from src.tools.pubmed import BASE_URL, _common_params

    response = requests.get(
        f"{BASE_URL}/esearch.fcgi",
        params={
            **_common_params(),
            "db": "pubmed",
            "term": "all[sb]",
            "rettype": "count",
            "retmax": 0,
            "retmode": "json",
        },
        timeout=8,
    )
    response.raise_for_status()
    count = ((response.json() or {}).get("esearchresult") or {}).get("count")
    return int(count) if count else None
