"""Track paper-source API limits from response headers and local call windows."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from src.db import DATA_DIR

LIMITS_PATH = DATA_DIR / "ratelimits.json"
_lock = threading.Lock()

# Documented limits when a service does not return headers.
SOURCE_META: dict[str, dict[str, Any]] = {
    "openalex": {
        "name": "OpenAlex",
        "site": "openalex.org",
        "period": "daily",
        "hint": "Anonymous IP budget (~$0.10/day). Resets at midnight UTC. Add mailto in Settings for the polite pool.",
    },
    "semantic_scholar": {
        "name": "Semantic Scholar",
        "site": "semanticscholar.org",
        "period": "5 minutes",
        "window_seconds": 300,
        "default_limit": 100,
        "hint": "Shared keyless pool. Add a free API key in Settings for higher limits.",
    },
    "arxiv": {
        "name": "arXiv",
        "site": "arxiv.org",
        "period": "courtesy",
        "min_interval_seconds": 3,
        "hint": "Courtesy delay: 1 request every 3 seconds. We throttle automatically.",
    },
    "pubmed": {
        "name": "PubMed",
        "site": "pubmed.ncbi.nlm.nih.gov",
        "period": "per second",
        "window_seconds": 1,
        "default_limit": 3,
        "hint": "NCBI E-utilities: 3 requests/sec without a key, 10/sec with NCBI_API_KEY.",
    },
}


def _read() -> dict:
    if not LIMITS_PATH.exists():
        return {}
    try:
        return json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIMITS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _live_meta(source_id: str) -> dict[str, Any]:
    meta = dict(SOURCE_META.get(source_id) or {})
    if source_id == "semantic_scholar":
        from src.settings import get_s2_key

        if get_s2_key():
            meta["period"] = "per second"
            meta["window_seconds"] = 1
            meta["default_limit"] = 1
            meta["hint"] = "Using your Semantic Scholar key (~1 request/second)."
        else:
            meta["hint"] = "Shared keyless pool: 100 requests / 5 minutes. Add a free API key in Settings for your own limit."
    elif source_id == "openalex":
        from src.settings import get_contact_email

        email = get_contact_email()
        meta["hint"] = (
            f"Using {email} (OpenAlex polite pool). Daily credit budget resets at midnight UTC."
            if email
            else "Anonymous IP budget (~$0.10/day). Resets at midnight UTC. Add a contact email in Settings."
        )
    elif source_id == "pubmed":
        import os

        if (os.getenv("NCBI_API_KEY") or "").strip():
            meta["default_limit"] = 10
            meta["hint"] = "NCBI API key detected: 10 requests/second."
        else:
            meta["hint"] = "3 requests/second without NCBI_API_KEY; 10/second with a key."
    return meta


def _seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return None


def _ago(iso: str | None) -> str:
    secs = _seconds_since(iso)
    if secs is None:
        return ""
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _format_reset(seconds: int | None, *, period: str = "") -> str:
    if not seconds or seconds <= 0:
        if period == "5 minutes":
            return "new window every 5 minutes"
        if period == "per second":
            return "resets every second"
        if period == "courtesy":
            return "we wait 3 seconds between calls"
        return "resets at midnight UTC"
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"resets in {hours}h {minutes}m"
    if minutes:
        return f"resets in {minutes}m"
    return f"resets in {seconds}s"


def record_http(source_id: str, response: requests.Response | None) -> None:
    """Update stored limits from an HTTP response."""
    if response is None:
        return

    with _lock:
        data = _read()
        entry = data.get(source_id, {})
        entry["updated_at"] = _now_iso()
        entry["last_status"] = response.status_code

        if source_id == "openalex":
            _merge_openalex(entry, response)
        elif source_id == "semantic_scholar":
            _merge_semantic_scholar(entry, response)

        _record_local_call(data, source_id)
        data[source_id] = entry
        _write(data)


def record_call(source_id: str, *, status: int | None = None, error: str | None = None) -> None:
    """Track a request for sources that do not expose limit headers."""
    with _lock:
        data = _read()
        entry = data.get(source_id, {})
        entry["updated_at"] = _now_iso()
        if status is not None:
            entry["last_status"] = status
        if error:
            entry["last_error"] = error[:160]
        _record_local_call(data, source_id)
        if status == 429:
            entry["status"] = "exhausted"
        data[source_id] = entry
        _write(data)


def _merge_openalex(entry: dict, response: requests.Response) -> None:
    headers = response.headers
    limit = _int_header(headers, "X-RateLimit-Limit")
    remaining = _int_header(headers, "X-RateLimit-Remaining")
    reset = _int_header(headers, "X-RateLimit-Reset")
    limit_usd = headers.get("X-RateLimit-Limit-USD")
    remaining_usd = headers.get("X-RateLimit-Remaining-USD")
    credits_used = _int_header(headers, "X-RateLimit-Credits-Used")

    if limit is not None:
        entry["limit"] = limit
    if remaining is not None:
        entry["remaining"] = remaining
    if reset is not None:
        entry["reset_seconds"] = reset
    if limit_usd is not None:
        entry["limit_usd"] = limit_usd
    if remaining_usd is not None:
        entry["remaining_usd"] = remaining_usd
    if credits_used is not None:
        entry["last_cost"] = credits_used

    if response.status_code == 429 or (remaining is not None and remaining <= 0):
        entry["status"] = "exhausted"
    elif remaining is not None and limit and remaining < limit * 0.15:
        entry["status"] = "warning"
    else:
        entry["status"] = "ok"


def _merge_semantic_scholar(entry: dict, response: requests.Response) -> None:
    headers = response.headers
    for key in ("x-ratelimit-limit", "X-RateLimit-Limit"):
        if key in headers:
            entry["limit"] = _safe_int(headers.get(key))
    for key in ("x-ratelimit-remaining", "X-RateLimit-Remaining"):
        if key in headers:
            entry["remaining"] = _safe_int(headers.get(key))
    for key in ("x-ratelimit-reset", "X-RateLimit-Reset", "retry-after"):
        if key in headers:
            entry["reset_seconds"] = _safe_int(headers.get(key))

    if response.status_code == 429:
        entry["status"] = "exhausted"
    elif entry.get("remaining") is not None and entry.get("limit"):
        if entry["remaining"] < entry["limit"] * 0.15:
            entry["status"] = "warning"
        else:
            entry["status"] = "ok"


def _record_local_call(data: dict, source_id: str) -> None:
    meta = _live_meta(source_id)
    window = meta.get("window_seconds")
    if not window and source_id != "arxiv":
        return

    entry = data.get(source_id, {})
    now = time.time()
    times: list[float] = [t for t in entry.get("call_times", []) if now - t <= (window or 3600)]
    times.append(now)
    entry["call_times"] = times[-500:]

    if source_id == "arxiv":
        entry["last_call_at"] = now
        if times:
            since_last = now - times[-2] if len(times) > 1 else 999
            entry["seconds_since_last"] = round(since_last, 1)

    if window and meta.get("default_limit"):
        recent = [t for t in times if now - t <= window]
        entry["window_used"] = len(recent)
        entry["window_limit"] = meta["default_limit"]
        entry["window_remaining"] = max(0, meta["default_limit"] - len(recent))
        if len(recent) >= meta["default_limit"]:
            entry["status"] = "exhausted"
        elif len(recent) >= meta["default_limit"] * 0.85:
            entry["status"] = "warning"
        else:
            entry["status"] = "ok"

    data[source_id] = entry


def probe_openalex() -> None:
    """Light ping so the settings page can show fresh OpenAlex headers."""
    try:
        from src.settings import get_contact_email

        mailto = get_contact_email() or "local@localhost"
        response = requests.get(
            "https://api.openalex.org/works",
            params={"filter": "type:article", "per_page": 1, "mailto": mailto},
            timeout=30,
        )
        record_http("openalex", response)
    except Exception as exc:
        record_call("openalex", error=str(exc))


def _format_source(source_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    meta = _live_meta(source_id)
    now = time.time()
    window = meta.get("window_seconds")
    window_limit = meta.get("default_limit")
    fresh = [t for t in entry.get("call_times", []) if now - t <= window] if window else []
    last_call = max(entry.get("call_times") or [0]) if entry.get("call_times") else entry.get("last_call_at") or 0
    since_last = (now - last_call) if last_call else 10_000
    recent_429 = entry.get("last_status") == 429 and since_last <= (window or 300)

    if source_id == "arxiv":
        wait = meta.get("min_interval_seconds", 3)
        cooling = max(0.0, wait - since_last)
        ready = cooling <= 0.05
        return _pack(
            source_id,
            meta,
            entry,
            limit=wait,
            remaining=0 if not ready else wait,
            used=0 if ready else 1,
            percent=0 if ready else min(100, round((1 - cooling / wait) * 100)),
            status="exhausted" if recent_429 else "ok",
            detail="Ready for the next request." if ready else f"Wait {cooling:.1f}s (courtesy delay).",
            reset_label="ready" if ready else f"ready in {int(cooling) + 1}s",
        )

    if source_id == "openalex":
        limit = entry.get("limit")
        remaining = entry.get("remaining")
        reset_left = entry.get("reset_seconds")
        elapsed = _seconds_since(entry.get("updated_at"))
        if reset_left and elapsed is not None:
            reset_left = max(0, int(reset_left - elapsed))
            if reset_left == 0 and limit:
                remaining = limit
        used = max(0, (limit or 0) - (remaining or 0)) if limit is not None and remaining is not None else None
        percent = min(100, round((used / limit) * 100)) if limit and used is not None else 0
        usd = ""
        if entry.get("remaining_usd") is not None and entry.get("limit_usd") is not None and reset_left:
            usd = f" (${entry['remaining_usd']} of ${entry['limit_usd']} budget)"
        if remaining is None or limit is None:
            detail = entry.get("last_error") or "No data yet. Refresh or run a search."
            status = "unknown"
        elif remaining <= 0 or (entry.get("last_status") == 429 and (elapsed or 0) < 120):
            detail = f"0 of {limit} credits left{usd}"
            status = "exhausted"
        else:
            detail = f"{remaining} of {limit} credits left{usd}"
            status = "warning" if remaining < limit * 0.15 else "ok"
        return _pack(
            source_id,
            meta,
            entry,
            limit=limit,
            remaining=remaining,
            used=used,
            percent=percent,
            status=status,
            detail=detail,
            reset_label=_format_reset(reset_left, period=meta.get("period", "")),
        )

    # Sliding windows: Semantic Scholar, PubMed
    if window and window_limit:
        used = len(fresh)
        remaining = max(0, window_limit - used)
        percent = min(100, round((used / window_limit) * 100))
        period = meta.get("period") or "window"
        period_text = "second" if period == "per second" else period
        if recent_429 or used >= window_limit:
            status = "exhausted"
            detail = (
                f"Rate limited. {remaining} of {window_limit} left this {period_text}."
                if recent_429 and used < window_limit
                else f"0 of {window_limit} left this {period_text}."
            )
        else:
            status = "warning" if used >= window_limit * 0.85 else "ok"
            detail = f"{remaining} of {window_limit} left this {period_text}."
        return _pack(
            source_id,
            meta,
            entry,
            limit=window_limit,
            remaining=remaining,
            used=used,
            percent=percent,
            status=status,
            detail=detail,
            reset_label=_format_reset(None, period=period),
        )

    return _pack(
        source_id,
        meta,
        entry,
        limit=None,
        remaining=None,
        used=None,
        percent=0,
        status="unknown",
        detail=entry.get("last_error") or "No data yet. Run a search to update.",
        reset_label=_format_reset(entry.get("reset_seconds"), period=meta.get("period", "")),
    )


def _pack(
    source_id: str,
    meta: dict[str, Any],
    entry: dict[str, Any],
    *,
    limit,
    remaining,
    used,
    percent,
    status,
    detail,
    reset_label,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "name": meta.get("name") or source_id,
        "site": meta.get("site") or "",
        "period": meta.get("period") or "",
        "limit": limit,
        "remaining": remaining,
        "used": used if isinstance(used, int) else None,
        "percent_used": percent,
        "reset_label": reset_label,
        "status": status,
        "detail": detail,
        "hint": meta.get("hint", ""),
        "updated_at": entry.get("updated_at"),
        "updated_label": _ago(entry.get("updated_at")),
        "last_status": entry.get("last_status"),
    }


def public_limits() -> list[dict[str, Any]]:
    """Format limits for the settings UI."""
    raw = _read()
    return [_format_source(source_id, raw.get(source_id, {})) for source_id in SOURCE_META]


def _int_header(headers: requests.structures.CaseInsensitiveDict, key: str) -> int | None:
    return _safe_int(headers.get(key))


def _safe_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
