"""Test fixtures.

Unit tests run against a throwaway SQLite file so `pytest` works with no services.
Integration tests (Places, Resend, IMAP, Telegram, LiteLLM) run only when the
matching real credentials are present - they are never mocked.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from marketpilot.config import get_settings
from marketpilot.models import Base, Job, Lead


def _database_url() -> str:
    return (
        os.getenv("TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or f"sqlite+pysqlite:///{os.path.abspath(os.path.dirname(__file__))}/.test_{uuid.uuid4().hex}.db"
    )


@pytest.fixture(scope="session")
def engine():
    url = _database_url()
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()
    if not url.startswith("sqlite"):
        return
    db_file = url.split("///")[-1]
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture
def session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess = factory()
    try:
        yield sess
        sess.rollback()
    finally:
        sess.close()


@pytest.fixture
def job(session: Session) -> Job:
    row = Job(service="cafe loyalty app", target_clients="independent coffee shops in Bengaluru")
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def lead(session: Session, job: Job) -> Lead:
    row = Lead(
        job_id=job.id,
        name="Example Roasters",
        source="GOOGLE_PLACES",
        external_id="test-external-1",
        status="DISCOVERED",
    )
    session.add(row)
    session.flush()
    return row


def has_env(name: str) -> bool:
    return bool(os.getenv(name))


requires_env = pytest.mark.skipif(
    not all(has_env(n) for n in ()),
    reason="credentials not configured",
)


def settings():
    return get_settings()
