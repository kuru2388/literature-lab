"""Thin AISuite / Gemini wrapper (provider:model, no agent framework)."""

from __future__ import annotations

import json
import re
import threading
import time

import aisuite as ai
from dotenv import load_dotenv
from openai import OpenAI

from src.settings import (
    GEMINI_MODEL,
    GEMINI_OPENAI_BASE,
    active_model_id,
    apply_env,
    get_gemini_key,
    get_gemini_model,
    get_openai_key,
    has_gemini_key,
    has_openai_key,
    resolve_provider,
)
from src.usage import record_response

load_dotenv()
apply_env()

_run = threading.local()


def use_provider(name: str) -> None:
    _run.provider = name


def current_provider() -> str:
    saved = getattr(_run, "provider", None)
    if saved:
        return saved
    return resolve_provider()


def get_client() -> ai.Client:
    apply_env()
    if not get_openai_key():
        raise RuntimeError("Add your OpenAI API key in Settings, then click Save.")
    return ai.Client()


def _gemini_client() -> OpenAI:
    apply_env()
    key = get_gemini_key()
    if not key:
        raise RuntimeError("Add your Gemini API key in Settings, then click Save.")
    return OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE)


def _clean_messages(messages: list[dict]) -> list[dict]:
    cleaned = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in messages
        if str(item.get("content") or "").strip()
    ]
    if not cleaned:
        raise RuntimeError("The tutor needs a question to answer.")
    return cleaned


def _openai_native() -> OpenAI:
    apply_env()
    key = get_openai_key()
    if not key:
        raise RuntimeError("Add your OpenAI API key in Settings, then click Save.")
    return OpenAI(api_key=key)


def _openai_model_name() -> str:
    return (active_model_id("openai") or "openai:gpt-4o").split(":", 1)[-1]


def _delta_text(chunk) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return str(getattr(delta, "content", None) or "")


def _iter_chat_stream(
    client: OpenAI,
    model: str,
    messages: list[dict],
    temperature: float,
    *,
    include_usage: bool = False,
    timeout: float = 35.0,
):
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "timeout": timeout,
    }
    if include_usage:
        kwargs["stream_options"] = {"include_usage": True}
    try:
        stream = client.chat.completions.create(**kwargs)
    except TypeError:
        kwargs.pop("stream_options", None)
        stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage:
            record_response(chunk, model)
        text = _delta_text(chunk)
        if text:
            yield text


def chat_messages(messages: list[dict], *, provider: str | None = None, temperature: float = 0.2) -> str:
    """Chat with a full message list. Optional provider override for the tutor."""
    return "".join(chat_messages_stream(messages, provider=provider, temperature=temperature)).strip()


def chat_messages_stream(messages: list[dict], *, provider: str | None = None, temperature: float = 0.2):
    """Yield reply text as the model writes it, ChatGPT-style."""
    previous = getattr(_run, "provider", None)
    try:
        if provider:
            use_provider(resolve_provider(provider))
        active = current_provider()
        cleaned = _clean_messages(messages)
        if active == "gemini":
            primary = get_gemini_model()
            candidates = [primary]
            for cand in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest"]:
                if cand not in candidates:
                    candidates.append(cand)

            last_err = None
            for cand_model in candidates:
                try:
                    had_chunk = False
                    for chunk in _iter_chat_stream(_gemini_client(), cand_model, cleaned, temperature, timeout=25.0):
                        had_chunk = True
                        yield chunk
                    if had_chunk:
                        return
                except Exception as exc:
                    last_err = exc
                    err_str = str(exc).lower()
                    if any(tok in err_str for tok in ("429", "503", "404", "resource_exhausted", "high demand", "unavailable", "rate limit", "quota", "limit:")):
                        continue
                    continue

            # If Gemini fails and OpenAI key is available, seamlessly fall back to OpenAI
            if has_openai_key():
                try:
                    yield from _iter_chat_stream(_openai_native(), _openai_model_name(), cleaned, temperature, include_usage=True, timeout=35.0)
                    return
                except Exception:
                    pass

            if last_err:
                raise last_err
            return

        # Active provider is OpenAI
        try:
            yield from _iter_chat_stream(_openai_native(), _openai_model_name(), cleaned, temperature, include_usage=True, timeout=35.0)
            return
        except Exception as oai_exc:
            # If OpenAI fails and Gemini is configured, fall back to Gemini
            if has_gemini_key():
                for cand in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash"]:
                    try:
                        had_chunk = False
                        for chunk in _iter_chat_stream(_gemini_client(), cand, cleaned, temperature, timeout=25.0):
                            had_chunk = True
                            yield chunk
                        if had_chunk:
                            return
                    except Exception:
                        continue
            raise oai_exc
    finally:
        if previous:
            _run.provider = previous
        elif hasattr(_run, "provider"):
            delattr(_run, "provider")


def _is_busy_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("503", "high demand", "unavailable", "429", "rate limit", "resource_exhausted")
    )


def _provider_chain() -> list[str]:
    active = current_provider()
    chain = [active]
    other = "gemini" if active == "openai" else "openai"
    if other == "openai" and has_openai_key():
        chain.append("openai")
    if other == "gemini" and has_gemini_key():
        chain.append("gemini")
    return chain


def chat(system: str, user: str, *, temperature: float = 0.2) -> str:
    last: Exception | None = None
    previous = getattr(_run, "provider", None)
    try:
        for provider in _provider_chain():
            use_provider(provider)
            for attempt in range(4):
                try:
                    return chat_messages(
                        [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=temperature,
                    )
                except Exception as exc:
                    last = exc
                    if not _is_busy_error(exc):
                        raise
                    time.sleep(4 * (attempt + 1))
        raise last or RuntimeError("The model did not respond.")
    finally:
        if previous:
            _run.provider = previous
        elif hasattr(_run, "provider"):
            delattr(_run, "provider")


try:
    import json_repair
except ImportError:
    json_repair = None


def chat_json(system: str, user: str, *, temperature: float = 0.1, retries: int = 1) -> dict | list:
    full_system = system + "\n\nReturn ONLY valid JSON. No markdown fences, no commentary."
    last_err: Exception | None = None
    prompt = user
    for attempt in range(retries + 1):
        try:
            raw = chat(full_system, prompt, temperature=temperature)
            return parse_json(raw)
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                prompt = (
                    f"{user}\n\n"
                    f"NOTE: Your previous attempt failed JSON parsing with error: {exc}.\n"
                    f"Please respond with STRICTLY valid JSON only. Ensure all strings use double quotes, "
                    f"all commas are present between array/object items, and there are no trailing commas."
                )
    raise last_err or RuntimeError("Failed to parse JSON response.")


def parse_json(text: str) -> dict | list:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # 1. Fast direct attempt
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Extract JSON boundary
    match = re.search(r"[\{\[].*[\}\]]", cleaned, re.DOTALL)
    candidate = match.group(0) if match else cleaned

    try:
        return json.loads(candidate)
    except Exception:
        pass

    # 3. Robust repair with json_repair library
    if json_repair:
        try:
            repaired = json_repair.repair_json(candidate, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
        except Exception:
            pass

    # 4. Regex repairs: trailing commas, comments, missing commas
    fixed = candidate
    fixed = re.sub(r"(?m)^\s*//.*$", "", fixed)
    fixed = re.sub(r',\s*([\}\]])', r'\1', fixed)
    fixed = re.sub(r'(\})\s*(\{)', r'\1,\2', fixed)
    fixed = re.sub(r'(\])\s*(\[)', r'\1,\2', fixed)
    try:
        return json.loads(fixed)
    except Exception:
        pass

    # 5. Final fallback on entire raw text with json_repair
    if json_repair:
        try:
            repaired = json_repair.repair_json(text, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
        except Exception:
            pass

    return json.loads(candidate)
