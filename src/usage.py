"""Track OpenAI tokens and USD cost for the current run and lifetime."""

from __future__ import annotations

import json
import threading
from typing import Any

from src.db import DATA_DIR

# Official list prices, USD per 1 million tokens.
# Source: OpenAI API pricing (gpt-4o family). Update if you change models.
PRICES_PER_MILLION = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "o3": {"input": 2.00, "output": 8.00},
    "gemini-free": {"input": 0.0, "output": 0.0},
}

SPEND_PATH = DATA_DIR / "spend.json"
_lock = threading.Lock()
_current = threading.local()


def _model_key(model: str) -> str:
    name = (model or "openai:gpt-4o").split(":", 1)[-1].strip().lower()
    blob = (model or "").lower()
    if "gemini" in blob or "gemini" in name:
        return "gemini-free"
    if name in PRICES_PER_MILLION:
        return name
    for key in PRICES_PER_MILLION:
        if name.startswith(key):
            return key
    return "gpt-4o"


def price_for(model: str) -> dict[str, float]:
    return PRICES_PER_MILLION[_model_key(model)]


def cost_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    rates = price_for(model)
    return (prompt_tokens / 1_000_000) * rates["input"] + (
        completion_tokens / 1_000_000
    ) * rates["output"]


def round_usd(value: float) -> float:
    return round(value + 1e-12, 6)


def format_usd(value: float) -> str:
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def begin_run(task_id: str, model: str) -> None:
    _current.task_id = task_id
    _current.meter = {
        "task_id": task_id,
        "model": model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usd": 0.0,
        "calls": 0,
    }


def current_meter() -> dict[str, Any]:
    meter = getattr(_current, "meter", None)
    if not meter:
        return empty_usage()
    return dict(meter)


def empty_usage() -> dict[str, Any]:
    return {
        "model": "openai:gpt-4o",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usd": 0.0,
        "usd_display": "$0.0000",
        "calls": 0,
    }


def record_response(response: Any, model: str) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    prompt = _usage_int(usage, "prompt_tokens", "input_tokens")
    completion = _usage_int(usage, "completion_tokens", "output_tokens")
    usd = cost_usd(prompt, completion, model)

    meter = getattr(_current, "meter", None)
    if meter is None:
        begin_run("unknown", model)
        meter = _current.meter

    meter["model"] = model
    meter["prompt_tokens"] += prompt
    meter["completion_tokens"] += completion
    meter["total_tokens"] += prompt + completion
    meter["usd"] = round_usd(meter["usd"] + usd)
    meter["calls"] += 1
    from src.db import update_task

    usage = snapshot()
    task_id = meter.get("task_id")
    if task_id and task_id != "unknown":
        update_task(task_id, usage=usage)
    return usage


def snapshot() -> dict[str, Any]:
    meter = current_meter()
    lifetime = read_lifetime()
    return {
        "this_run": {
            "model": meter.get("model"),
            "prompt_tokens": meter.get("prompt_tokens", 0),
            "completion_tokens": meter.get("completion_tokens", 0),
            "total_tokens": meter.get("total_tokens", 0),
            "usd": meter.get("usd", 0.0),
            "usd_display": format_usd(float(meter.get("usd") or 0)),
            "calls": meter.get("calls", 0),
        },
        "total": {
            "prompt_tokens": lifetime["prompt_tokens"] + meter.get("prompt_tokens", 0),
            "completion_tokens": lifetime["completion_tokens"] + meter.get("completion_tokens", 0),
            "total_tokens": lifetime["total_tokens"] + meter.get("total_tokens", 0),
            "usd": round_usd(lifetime["usd"] + float(meter.get("usd") or 0)),
            "usd_display": format_usd(lifetime["usd"] + float(meter.get("usd") or 0)),
            "runs": lifetime["runs"],
        },
    }


def commit_run() -> dict[str, Any]:
    """Fold this run into lifetime spend. Call once when a pipeline ends."""
    meter = current_meter()
    with _lock:
        data = _read_file()
        data["prompt_tokens"] += int(meter.get("prompt_tokens") or 0)
        data["completion_tokens"] += int(meter.get("completion_tokens") or 0)
        data["total_tokens"] += int(meter.get("total_tokens") or 0)
        data["usd"] = round_usd(data["usd"] + float(meter.get("usd") or 0))
        if int(meter.get("calls") or 0) > 0:
            data["runs"] += 1
        _write_file(data)
    result = {
        "this_run": {
            "model": meter.get("model"),
            "prompt_tokens": meter.get("prompt_tokens", 0),
            "completion_tokens": meter.get("completion_tokens", 0),
            "total_tokens": meter.get("total_tokens", 0),
            "usd": meter.get("usd", 0.0),
            "usd_display": format_usd(float(meter.get("usd") or 0)),
            "calls": meter.get("calls", 0),
        },
        "total": {
            "prompt_tokens": data["prompt_tokens"],
            "completion_tokens": data["completion_tokens"],
            "total_tokens": data["total_tokens"],
            "usd": data["usd"],
            "usd_display": format_usd(data["usd"]),
            "runs": data["runs"],
        },
    }
    _current.meter = {
        "task_id": meter.get("task_id"),
        "model": meter.get("model"),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usd": 0.0,
        "calls": 0,
    }
    return result


def read_lifetime() -> dict[str, Any]:
    with _lock:
        return _read_file()


def public_spend() -> dict[str, Any]:
    data = read_lifetime()
    return {
        "prompt_tokens": data["prompt_tokens"],
        "completion_tokens": data["completion_tokens"],
        "total_tokens": data["total_tokens"],
        "usd": data["usd"],
        "usd_display": format_usd(data["usd"]),
        "runs": data["runs"],
    }


def _usage_int(usage: Any, *names: str) -> int:
    if usage is None:
        return 0
    for name in names:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _default() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usd": 0.0,
        "runs": 0,
    }


def _read_file() -> dict[str, Any]:
    data = _default()
    if not SPEND_PATH.exists():
        return data
    try:
        loaded = json.loads(SPEND_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return data
    data.update({k: loaded.get(k, data[k]) for k in data})
    data["usd"] = float(data["usd"] or 0)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "runs"):
        data[key] = int(data[key] or 0)
    return data


def _write_file(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SPEND_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
