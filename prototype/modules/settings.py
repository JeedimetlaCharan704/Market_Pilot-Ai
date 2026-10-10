"""All configuration from config.yaml, overridable by .env. Nothing hard-coded.

Precedence, highest first:
  1. environment variable   (what a serverless host injects)
  2. config.yaml            (what a human edits)
  3. the fallback in code   (why the app still boots with neither)

Secrets live only in .env. config.yaml is safe to commit.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

CONFIG_PATH = ROOT / "config.yaml"

ENV_ALIASES = {
    "outreach.daily_send_cap": "DAILY_SEND_CAP",
    "http.allow_fallback": "ALLOW_FALLBACK",
    "http.user_agent": "USER_AGENT",
    "http.timeout": "REQUEST_TIMEOUT",
    "http.rate_limit_seconds": "RATE_LIMIT_SECONDS",
    "discovery.scraper_base_url": "SCRAPER_BASE_URL",
    "discovery.limit": "DISCOVERY_LIMIT",
    "llm.model": "LLM_MODEL",
    "llm.provider": "LLM_PROVIDER",
    "discovery.backend": "DISCOVERY_BACKEND",
}


def is_serverless() -> bool:
    """True when running on Vercel (or any host that exports VERCEL=1)."""
    return (os.getenv("VERCEL") or "") == "1"


def runtime_dir() -> Path:
    """The one writable directory.

    A serverless host gives the function a read-only filesystem except /tmp,
    which is wiped on every cold start. Local runs keep using ./data.
    """
    return Path("/tmp/marketpilot") if is_serverless() else (ROOT / "data")


def _flag(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def get_public_base_url() -> str:
    env_url = _flag("PUBLIC_BASE_URL")
    if env_url and env_url != "http://localhost:8000":
        return env_url.rstrip("/")
    if is_serverless():
        vercel_url = os.getenv("VERCEL_URL")
        if vercel_url:
            return f"https://{vercel_url}".rstrip("/")
        return "https://prototype-ashy-chi-38.vercel.app"
    return (env_url or "http://localhost:8000").rstrip("/")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """config.yaml as a dict. Returns {} when missing, unreadable or PyYAML absent."""
    if yaml is None or not CONFIG_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _cfg(dotted: str, default: str) -> str:
    """Read a dotted path from config.yaml, then the matching env var, then default."""
    node: Any = load_config()
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            break
    file_value = "" if node is None else str(node)
    key = ENV_ALIASES.get(dotted) or dotted.upper().replace(".", "_")
    env = os.getenv(key)
    return (env if env is not None and env.strip() else (file_value or default)).strip()


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    return {
        "discovery_backend": _cfg("discovery.backend", "osm").lower(),
        "google_maps_api_key": _flag("GOOGLE_MAPS_API_KEY"),
        "scraper_base_url": _cfg("discovery.scraper_base_url", "http://127.0.0.1:8080").rstrip("/"),
        "scraper_api_key": _flag("SCRAPER_API_KEY"),
        "discovery_limit": _cfg("discovery.limit", "15"),
        "llm_provider": _cfg("llm.provider", "").lower(),
        "gemini_api_key": _flag("GEMINI_API_KEY"),
        "groq_api_key": _flag("GROQ_API_KEY"),
        "openai_api_key": _flag("OPENAI_API_KEY"),
        "openai_base_url": _flag("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        "ollama_base_url": _cfg("llm.ollama_base_url", "http://127.0.0.1:11434").rstrip("/"),
        "ollama_model": _cfg("llm.ollama_model", "llama3.1"),
        "llm_model": _cfg("llm.model", ""),
        "llm_temperature": _cfg("llm.temperature", "0.3"),
        "smtp_host": _flag("SMTP_HOST"),
        "smtp_port": _flag("SMTP_PORT", "587"),
        "smtp_user": _flag("SMTP_USER"),
        "smtp_password": _flag("SMTP_PASSWORD"),
        "smtp_from": _flag("SMTP_FROM"),
        "smtp_test_to": _flag("SMTP_TEST_TO"),
        "imap_host": _flag("IMAP_HOST", "imap.gmail.com"),
        "imap_port": _flag("IMAP_PORT", "993"),
        "imap_user": _flag("IMAP_USER"),
        "imap_password": _flag("IMAP_PASSWORD"),
        "telegram_bot_token": _flag("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": _flag("TELEGRAM_CHAT_ID"),
        "crm_webhook_url": _flag("CRM_WEBHOOK_URL"),
        "notion_api_key": _flag("NOTION_API_KEY"),
        "notion_database_id": _flag("NOTION_DATABASE_ID"),
        "auto_reply": _cfg("outreach.auto_reply", "1") in {"1", "true", "yes"},
        "notify_telegram": _cfg("outreach.notify_telegram", "1") in {"1", "true", "yes"},
        "public_base_url": get_public_base_url(),
        "user_agent": _cfg("http.user_agent", "MarketPilot-Prototype/1.0"),
        "request_timeout": _cfg("http.timeout", "10"),
        "rate_limit_seconds": _cfg("http.rate_limit_seconds", "1.0"),
        "allow_fallback": _cfg("http.allow_fallback", "0"),
        "daily_send_cap": _cfg("outreach.daily_send_cap", "20"),
    }


def _int(value: str, default: int) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return default


def scoring_weights() -> dict[str, float]:
    """Priority weights from config.yaml, normalised so they always sum to 1.0."""
    node = (load_config().get("scoring") or {}).get("weights")
    weights = {
        "revenue_impact": 0.40,
        "fix_effort": 0.25,
        "evidence_strength": 0.20,
        "service_fit": 0.15,
    }
    if isinstance(node, dict):
        for key in weights:
            try:
                weights[key] = max(0.0, float(node.get(key, weights[key])))
            except (TypeError, ValueError):
                continue
    total = sum(weights.values()) or 1.0
    return {k: round(v / total, 4) for k, v in weights.items()}


def llm_available() -> bool:
    """True when a real model can be called. Drives the UI's provider badge."""
    return detect_provider() != "none"


def detect_provider() -> str:
    """Resolve the configured provider, auto-detecting from which key exists."""
    cfg = settings()
    explicit = cfg["llm_provider"]
    if explicit in {"gemini", "groq", "openai", "openai_compatible", "ollama"}:
        return explicit
    if explicit == "none":
        return "none"
    if cfg["gemini_api_key"]:
        return "gemini"
    if cfg["groq_api_key"]:
        return "groq"
    if cfg["openai_api_key"]:
        return "openai"
    return "none"


def describe() -> dict[str, Any]:
    cfg = settings()
    return {
        "discovery_backend": cfg["discovery_backend"],
        "llm_provider": detect_provider(),
        "llm_model": cfg["llm_model"] or "(provider default)",
        "real_email_send": bool(cfg["smtp_host"] and cfg["smtp_test_to"]),
        "imap_configured": bool(cfg["imap_user"] and cfg["imap_password"]),
        "telegram_configured": bool(cfg["telegram_bot_token"] and cfg["telegram_chat_id"]),
        "allow_fallback": cfg["allow_fallback"] == "1",
        "request_timeout_s": cfg["request_timeout"],
        "rate_limit_s": cfg["rate_limit_seconds"],
        "daily_send_cap": _int(cfg["daily_send_cap"], 20),
        "auto_reply": cfg["auto_reply"],
        "notify_telegram": cfg["notify_telegram"],
    }
