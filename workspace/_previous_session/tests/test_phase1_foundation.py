"""Phase 1 tests: config, status graph, idempotent step log, schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketpilot import steprun
from marketpilot.config import get_settings
from marketpilot.models import StepRun
from marketpilot.schemas import Idea, IdeasOutput, ResearchOutput
from marketpilot.statuses import (
    ALLOWED,
    TERMINAL,
    InvalidTransition,
    Status,
    assert_transition,
    can_transition,
    next_for_stage,
)


def test_all_required_statuses_exist():
    required = [
        "DISCOVERED", "ENRICHED", "IDEA_VERIFIED", "READY_TO_SEND", "SENT",
        "SEND_UNKNOWN", "REPLY_RECEIVED", "CLASSIFIED", "SUPPRESSED",
        "NO_CONTACT", "FAILED",
    ]
    assert [s.value for s in Status] == required


def test_forward_path_is_allowed():
    path = [
        Status.DISCOVERED, Status.ENRICHED, Status.IDEA_VERIFIED,
        Status.READY_TO_SEND, Status.SENT, Status.REPLY_RECEIVED, Status.CLASSIFIED,
    ]
    for current, target in zip(path, path[1:]):
        assert can_transition(current, target), f"{current} -> {target}"


def test_no_skipping_stages():
    assert not can_transition(Status.DISCOVERED, Status.SENT)
    assert not can_transition(Status.ENRICHED, Status.READY_TO_SEND)
    assert not can_transition(Status.IDEA_VERIFIED, Status.SENT)


def test_terminal_statuses_reject_all_moves():
    for status in TERMINAL:
        assert ALLOWED[status] == set()
        with pytest.raises(InvalidTransition):
            assert_transition(status, Status.ENRICHED)


def test_failed_is_terminal_until_reset():
    with pytest.raises(InvalidTransition):
        assert_transition(Status.FAILED, Status.ENRICHED)
    assert next_for_stage(Status.FAILED, "ENRICHED") is Status.ENRICHED
    with pytest.raises(InvalidTransition):
        next_for_stage(Status.FAILED, "NOT_A_STAGE")


def test_invalid_transition_raises_with_clear_message():
    with pytest.raises(InvalidTransition) as exc:
        assert_transition(Status.DISCOVERED, Status.CLASSIFIED)
    assert "DISCOVERED -> CLASSIFIED" in str(exc.value)


# --- config -----------------------------------------------------------------


def test_settings_load_from_env_and_yaml():
    settings = get_settings()
    assert settings.llm.primary.startswith(("gemini/", "groq/", "openai/", "anthropic/"))
    assert settings.llm.fallback != ""
    assert settings.discovery.provider in ("google_places", "osm")
    assert settings.limits.max_emails_per_lead == 1


def test_secrets_never_come_from_yaml():
    from marketpilot.config import ROOT

    source = (ROOT / "config.yaml").read_text(encoding="utf-8")
    for forbidden in ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "DATABASE_URL"):
        assert forbidden not in source
    assert (ROOT / ".env.example").exists()


# --- idempotent step log ----------------------------------------------------


def test_step_runs_once_and_second_call_is_skipped(session, lead):
    calls = {"n": 0}

    def work():
        calls["n"] += 1
        return {"done": True}

    _, first, skipped = steprun.run_step(session, "research", "lead:1", work, lead)
    assert not skipped and calls["n"] == 1 and first.outcome == "OK"

    _, second, skipped = steprun.run_step(session, "research", "lead:1", work, lead)
    assert skipped and calls["n"] == 1 and second.id == first.id
    assert session.query(StepRun).count() == 1


def test_failed_step_is_logged_and_can_be_retried(session, lead):
    def boom():
        raise ValueError("provider down")

    with pytest.raises(ValueError):
        steprun.run_step(session, "research", "lead:1", boom, lead)
    record = session.query(StepRun).one()
    assert record.outcome == "FAILED"
    assert "provider down" in record.error

    ok, record, skipped = steprun.run_step(
        session, "research", "lead:1", lambda: {"recovered": True}, lead
    )
    assert not skipped and record.outcome == "OK" and ok == {"recovered": True}
    assert session.query(StepRun).count() == 1


def test_same_step_different_leads_are_separate_records(session, lead, job):
    from marketpilot.models import Lead

    other = Lead(job_id=job.id, name="Other", source="OSM", external_id="x-2", status="DISCOVERED")
    session.add(other)
    session.flush()

    steprun.run_step(session, "research", f"lead:{lead.id}", lambda: {"a": 1}, lead)
    steprun.run_step(session, "research", f"lead:{other.id}", lambda: {"a": 2}, other)
    assert session.query(StepRun).count() == 2


def test_set_status_enforces_graph(session, lead):
    steprun.set_status(lead, Status.ENRICHED, "ok")
    assert lead.status == "ENRICHED"
    with pytest.raises(InvalidTransition):
        steprun.set_status(lead, Status.SENT, "nope")


def test_fail_records_stage_in_reason(session, lead):
    steprun.fail(lead, "RESEARCH", "timeout")
    assert lead.status == "FAILED"
    assert lead.status_reason == "RESEARCH: timeout"


# --- schema validation ------------------------------------------------------


def _valid_research() -> dict:
    return {
        "business_summary": "A single-origin coffee roaster with three city outlets.",
        "audience": "Office workers aged 25-40 buying espresso daily",
        "facts": [
            {"statement": "Opens at 7am on weekdays", "source": "places", "evidence": "opening_hours"},
            {"statement": "Rated 4.6 over 212 reviews", "source": "reviews", "evidence": "rating field"},
            {"statement": "No loyalty programme found on site", "source": "website", "evidence": "homepage scan"},
        ],
    }


def test_research_schema_accepts_valid_output():
    parsed = ResearchOutput.model_validate(_valid_research())
    assert len(parsed.facts) == 3


def test_research_schema_rejects_missing_evidence():
    bad = _valid_research()
    bad["facts"][0].pop("evidence")
    with pytest.raises(ValidationError):
        ResearchOutput.model_validate(bad)


def test_research_schema_rejects_bad_email():
    bad = _valid_research()
    bad["contact_email"] = "not-an-email"
    with pytest.raises(ValidationError):
        ResearchOutput.model_validate(bad)


def test_idea_must_cite_at_least_one_fact():
    with pytest.raises(ValidationError):
        IdeasOutput.model_validate(
            {"ideas": [{"title": "Loyalty punch card", "hook": "x" * 25, "rationale": "y" * 25,
                        "fact_indices": [], "expected_outcome": "z" * 15}]}
        )


def test_ideas_schema_round_trips():
    payload = {
        "ideas": [
            {
                "title": "Morning punch card for office walkers",
                "hook": "Your 7am regulars leave for a cheaper flat white by 9am.",
                "rationale": "Early opening plus office-worker audience makes a before-work habit play.",
                "fact_indices": [0, 1],
                "expected_outcome": "10 repeat visits in the first fortnight",
            }
        ]
    }
    parsed = IdeasOutput.model_validate(payload)
    assert isinstance(parsed.ideas[0], Idea)
    assert parsed.model_dump() == payload
