"""Make `modules` importable and keep the test database out of the real one."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Keep the default suite offline, deterministic and hermetic.

    A real key in .env would otherwise make two tests call Groq. The same is
    true of SMTP, IMAP and Telegram: once the developer has put real
    credentials in .env the "not configured" branches silently stop being
    exercised, and tests start passing or failing on whether a secret happens
    to be present. The model and outbound paths are covered separately by
    tests/test_llm_live.py when RUN_LIVE=1 is set - in which case we
    deliberately do NOT force anything off.
    """
    import os

    from modules import settings

    if os.getenv("RUN_LIVE") == "1":
        settings.settings.cache_clear()
        yield
        settings.settings.cache_clear()
        return

    monkeypatch.setenv("LLM_PROVIDER", "none")
    for key in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "SMTP_FROM", "SMTP_TEST_TO",
        "IMAP_HOST", "IMAP_PORT", "IMAP_USER", "IMAP_PASSWORD",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    settings.settings.cache_clear()
    yield
    settings.settings.cache_clear()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A throwaway SQLite file, replaced for every test."""
    from modules import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db()
    return store


@pytest.fixture()
def lead(db):
    return db.upsert_lead(
        {
            "search": "salons in Hyderabad",
            "source": "TEST",
            "external_id": "1",
            "name": "Roast CCx",
            "website": "https://example.com/",
            "phone": "040 1234567",
            "address": "Banjara Hills, Hyderabad",
            "city": "Hyderabad",
        }
    )
