"""Persist local app settings (AI keys, optional source keys)."""

from __future__ import annotations

import json
import os

from src.db import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
OPENAI_MODEL = "openai:gpt-4o"
GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
RETIRED_GEMINI_MODELS = {
    "gemini-3.6-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-pro",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-pro",
    "gemini-3.1-pro",
    "gemini-3.1-pro-preview",
    "gemini-3-pro",
    "gemini-3-pro-preview",
}
FIXED_LLM_CALLS = 8
GEMINI_RPD_LOW = 250
GEMINI_RPD_HIGH = 1500
GEMINI_RPM = "10-15"
GEMINI_TPM = "250,000-1,000,000"
PAPER_COUNT_OPTIONS = (3, 5, 8, 10, 12, 15, 30)


def _read() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_openai_key() -> str:
    data = _read()
    key = str(data.get("openai_api_key") or "").strip()
    if key:
        return key
    return os.getenv("OPENAI_API_KEY", "").strip()


def get_gemini_key() -> str:
    data = _read()
    key = str(data.get("gemini_api_key") or "").strip()
    if key:
        return key
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def get_model() -> str:
    data = _read()
    return str(data.get("openai_model") or os.getenv("OPENAI_MODEL") or OPENAI_MODEL).strip()


def get_gemini_model() -> str:
    data = _read()
    name = str(data.get("gemini_model") or os.getenv("GEMINI_MODEL") or GEMINI_MODEL).strip()
    if not name or name in RETIRED_GEMINI_MODELS:
        return GEMINI_MODEL
    return name


def get_preferred_provider() -> str:
    data = _read()
    saved = str(data.get("preferred_provider") or "").strip().lower()
    if saved in {"openai", "gemini"}:
        return saved
    if get_openai_key():
        return "openai"
    if get_gemini_key():
        return "gemini"
    return "openai"


def get_contact_email() -> str:
    data = _read()
    email = str(data.get("contact_email") or "").strip()
    if email:
        return email
    return os.getenv("CONTACT_EMAIL", "").strip()


def get_s2_key() -> str:
    """Optional free Semantic Scholar key; without it S2 shares a throttled pool."""
    data = _read()
    key = str(data.get("semantic_scholar_api_key") or "").strip()
    if key:
        return key
    return os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()


def has_openai_key() -> bool:
    return bool(get_openai_key())


def has_gemini_key() -> bool:
    return bool(get_gemini_key())


def has_llm_key() -> bool:
    return has_openai_key() or has_gemini_key()


def llm_calls_per_run(paper_count: int) -> int:
    n = max(3, min(30, int(paper_count or 8)))
    return FIXED_LLM_CALLS + n


def gemini_run_estimate(paper_count: int) -> dict:
    calls = llm_calls_per_run(paper_count)
    return {
        "paper_count": max(3, min(30, int(paper_count or 8))),
        "calls_per_run": calls,
        "runs_per_day_low": calls and GEMINI_RPD_LOW // calls,
        "runs_per_day_high": calls and GEMINI_RPD_HIGH // calls,
        "rpd_low": GEMINI_RPD_LOW,
        "rpd_high": GEMINI_RPD_HIGH,
        "rpm": GEMINI_RPM,
        "tpm": GEMINI_TPM,
    }


def gemini_quota() -> dict:
    rows = [gemini_run_estimate(n) for n in PAPER_COUNT_OPTIONS]
    return {
        "model": "Gemini Flash",
        "free": True,
        "rpd": f"{GEMINI_RPD_LOW}-{GEMINI_RPD_HIGH} requests/day",
        "rpm": f"{GEMINI_RPM} requests/minute",
        "tpm": f"{GEMINI_TPM} tokens/minute",
        "note": "Limits reset each day. Two runs at once can hit the per-minute cap.",
        "rows": rows,
        "default": gemini_run_estimate(8),
    }


def resolve_provider(requested: str | None = None) -> str:
    want = (requested or "").strip().lower()
    openai_ok = has_openai_key()
    gemini_ok = has_gemini_key()
    if want == "openai":
        if not openai_ok:
            raise RuntimeError("Add an OpenAI API key in Settings, then click Save.")
        return "openai"
    if want == "gemini":
        if not gemini_ok:
            raise RuntimeError("Add a Gemini API key in Settings, then click Save.")
        return "gemini"
    if openai_ok and not gemini_ok:
        return "openai"
    if gemini_ok and not openai_ok:
        return "gemini"
    if openai_ok and gemini_ok:
        preferred = get_preferred_provider()
        return preferred if preferred in {"openai", "gemini"} else "openai"
    raise RuntimeError("Add an OpenAI or Gemini API key in Settings, then click Save.")


def active_model_id(provider: str) -> str:
    if provider == "gemini":
        return f"google:{get_gemini_model()}"
    return get_model() or OPENAI_MODEL


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "••••••••"
    return f"{key[:7]}…{key[-4:]}"


def save_settings(
    *,
    openai_api_key: str | None = None,
    openai_model: str | None = None,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
    preferred_provider: str | None = None,
    semantic_scholar_api_key: str | None = None,
    contact_email: str | None = None,
) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = _read()
    if openai_api_key is not None:
        cleaned = openai_api_key.strip()
        if cleaned:
            data["openai_api_key"] = cleaned
            os.environ["OPENAI_API_KEY"] = cleaned
        elif "openai_api_key" in data:
            del data["openai_api_key"]
            os.environ.pop("OPENAI_API_KEY", None)
    if gemini_api_key is not None:
        cleaned = gemini_api_key.strip()
        if cleaned:
            data["gemini_api_key"] = cleaned
            os.environ["GEMINI_API_KEY"] = cleaned
            os.environ["GOOGLE_API_KEY"] = cleaned
        elif "gemini_api_key" in data:
            del data["gemini_api_key"]
            os.environ.pop("GEMINI_API_KEY", None)
    if semantic_scholar_api_key is not None:
        cleaned = semantic_scholar_api_key.strip()
        if cleaned:
            data["semantic_scholar_api_key"] = cleaned
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = cleaned
        elif "semantic_scholar_api_key" in data:
            del data["semantic_scholar_api_key"]
            os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    if openai_model is not None and openai_model.strip():
        data["openai_model"] = openai_model.strip()
    if gemini_model is not None and gemini_model.strip():
        data["gemini_model"] = gemini_model.strip()
    if preferred_provider is not None:
        choice = preferred_provider.strip().lower()
        if choice in {"openai", "gemini"}:
            data["preferred_provider"] = choice
    if contact_email is not None:
        cleaned = contact_email.strip()
        if cleaned:
            data["contact_email"] = cleaned
            os.environ["CONTACT_EMAIL"] = cleaned
        elif "contact_email" in data:
            del data["contact_email"]
            os.environ.pop("CONTACT_EMAIL", None)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return public_settings()


def public_settings() -> dict:
    openai_key = get_openai_key()
    gemini_key = get_gemini_key()
    s2_key = get_s2_key()
    contact = get_contact_email()
    preferred = get_preferred_provider()
    return {
        "configured": bool(openai_key or gemini_key),
        "openai_configured": bool(openai_key),
        "gemini_configured": bool(gemini_key),
        "both_configured": bool(openai_key and gemini_key),
        "masked_key": mask_key(openai_key),
        "gemini_masked_key": mask_key(gemini_key),
        "openai_model": get_model(),
        "gemini_model": get_gemini_model(),
        "preferred_provider": preferred,
        "recommend": "openai",
        "recommend_label": "OpenAI gpt-4o is recommended for quality. Gemini Flash is the free option (Pro has no free quota on new keys).",
        "s2_configured": bool(s2_key),
        "s2_masked_key": mask_key(s2_key),
        "contact_email": contact,
        "contact_configured": bool(contact),
        "gemini_quota": gemini_quota(),
    }


def apply_env() -> None:
    """Copy saved keys into the process env so providers can see them."""
    key = get_openai_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    gemini = get_gemini_key()
    if gemini:
        os.environ["GEMINI_API_KEY"] = gemini
        os.environ["GOOGLE_API_KEY"] = gemini
    model = get_model()
    if model:
        os.environ["OPENAI_MODEL"] = model
    s2_key = get_s2_key()
    if s2_key:
        os.environ["SEMANTIC_SCHOLAR_API_KEY"] = s2_key
    contact = get_contact_email()
    if contact:
        os.environ["CONTACT_EMAIL"] = contact
