"""One swappable LLM adapter. Model independence is a judged criterion, so the
whole provider surface lives in this single file.

Providers
  gemini             Google AI Studio              GEMINI_API_KEY
  groq               Groq                          GROQ_API_KEY
  ollama             Local model, no key           OLLAMA_BASE_URL + OLLAMA_MODEL
  openai             OpenAI                        OPENAI_API_KEY
  openai_compatible  OpenRouter / any              OPENAI_API_KEY (or none) + OPENAI_BASE_URL

Two transports, same interface:
  * LiteLLM, when it is installed - one call site for every provider
  * direct HTTP fallback
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .settings import detect_provider, settings

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "groq": "openai/gpt-oss-120b",
    "ollama": None,
    "openai": "gpt-4o-mini",
    "openai_compatible": "google/gemini-2.0-flash-001",
}

LITELLM_PREFIX = {
    "gemini": "gemini/",
    "groq": "groq/",
    "ollama": "ollama/",
    "openai": "",
    "openai_compatible": "",
}

_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLMError(RuntimeError):
    pass


def _litellm():
    """LiteLLM when installed; None otherwise."""
    try:
        import litellm
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        return litellm
    except ImportError:
        return None


def available() -> bool:
    return detect_provider() != "none"


def provider() -> str:
    return detect_provider()


def model_name() -> str:
    cfg = settings()
    name = detect_provider()
    if cfg["llm_model"]:
        return cfg["llm_model"]
    if name == "ollama":
        return cfg["ollama_model"]
    return DEFAULT_MODELS.get(name, "")


def transport() -> str:
    return "litellm" if _litellm() is not None else "http"


def complete(prompt: str, system: str | None = None) -> str:
    name = detect_provider()
    if name == "none":
        raise LLMError("no LLM provider configured (set a key in .env)")
    engine = _litellm()
    if engine is not None:
        return _via_litellm(engine, prompt, system, name)
    if name == "gemini":
        return _gemini(prompt, system)
    return _openai_compatible(prompt, system, name)


def complete_json(prompt: str, system: str | None = None, *, retries: int = 1) -> dict[str, Any]:
    instruction = (system or "") + "\n\nRespond with one JSON object only. No prose, no fences."
    last = ""
    for attempt in range(retries + 1):
        raw = complete(
            prompt if attempt == 0
            else f"{prompt}\n\nYour last answer was invalid JSON:\n{last[:400]}",
            instruction,
        )
        last = raw
        data = _extract(raw)
        if data is not None:
            return data
    raise LLMError(f"model did not return parseable JSON after {retries + 1} attempts")


def _extract(raw: str) -> dict | None:
    text = raw.strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _temperature() -> float:
    try:
        return float(settings()["llm_temperature"])
    except ValueError:
        return 0.3


def _via_litellm(engine, prompt: str, system: str | None, name: str) -> str:
    cfg = settings()
    model = model_name()
    full = f"{LITELLM_PREFIX.get(name, '')}{model}" if LITELLM_PREFIX.get(name) else model

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": full,
        "messages": messages,
        "temperature": _temperature(),
        "timeout": 60,
    }
    api_key = {"groq": cfg["groq_api_key"], "gemini": cfg["gemini_api_key"]}.get(name)
    if api_key:
        kwargs["api_key"] = api_key
    if name == "ollama":
        kwargs["api_base"] = cfg["ollama_base_url"].rstrip("/").removesuffix("/v1")

    try:
        response = engine.completion(**kwargs)
    except Exception as exc:
        raise LLMError(f"{name} (litellm) failed: {exc}") from exc
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise LLMError(f"unexpected {name} response: {str(response)[:300]}") from exc
    if not (text or "").strip():
        raise LLMError(f"{name} returned an empty response")
    return str(text).strip()


def _gemini(prompt: str, system: str | None) -> str:
    cfg = settings()
    model = model_name().removeprefix("gemini/")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        response = httpx.post(
            url,
            json=body,
            params={"key": cfg["gemini_api_key"]},
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"gemini request failed: {exc}") from exc
    if response.status_code >= 400:
        raise LLMError(f"gemini HTTP {response.status_code}: {response.text[:300]}")
    try:
        parts = response.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected gemini response: {response.text[:300]}") from exc
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise LLMError("gemini returned an empty response")
    return text


def _openai_compatible(prompt: str, system: str | None, name: str) -> str:
    cfg = settings()
    if name == "ollama":
        base = f"{cfg['ollama_base_url']}/v1"
        key = None
    else:
        base = cfg["openai_base_url"] if name == "openai_compatible" else _BASE_URLS.get(name, "")
        key = cfg["groq_api_key"] if name == "groq" else cfg["openai_api_key"]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": model_name(),
                "messages": messages,
                "temperature": _temperature(),
            },
            headers=headers,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"{name} request failed: {exc}") from exc
    if response.status_code >= 400:
        raise LLMError(f"{name} HTTP {response.status_code}: {response.text[:300]}")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected {name} response: {response.text[:300]}") from exc
    if not (text or "").strip():
        raise LLMError(f"{name} returned an empty response")
    return text.strip()


def status() -> dict:
    return {
        "provider": provider(),
        "model": model_name() if available() else None,
        "transport": transport(),
    }
