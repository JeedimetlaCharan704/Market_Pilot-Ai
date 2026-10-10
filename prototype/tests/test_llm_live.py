"""Live model-path tests. OFF by default so `pytest -q` never touches a network.

    RUN_LIVE=1 python -m pytest tests/test_llm_live.py -q

Requires a real key in prototype/.env. These are the checks that prove the
"model independence" claim is more than a config line: the adapter really calls
whichever provider is configured, and its output still passes the validators.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to hit the live provider"
)


BUSINESS = {
    "name": "Roast CCx",
    "address": "Banjara Hills, Hyderabad",
    "phone": "+91 8096116999",
    "website": "https://roastcoffee.in/",
    "city": "Hyderabad",
    "search": "cafes in Hyderabad",
}

CARD = {
    "gap": "booking",
    "title": "No online booking",
    "source_url": "https://roastcoffee.in/",
    "observation": (
        "No booking, reservation or appointment link or widget found on the pages read."
    ),
    "retrieved_at": "2026-10-09T09:00:00+00:00",
    "confidence": "medium",
    "why_it_matters": (
        "Visitors who are ready to buy have no way to pick a slot, so they call a "
        "competitor or drop off."
    ),
}


@pytest.fixture()
def live_llm():
    from modules import llm, settings

    settings.settings.cache_clear()
    if not llm.available():
        pytest.skip("no LLM key configured in .env")
    return llm


def test_adapter_resolves_a_real_provider(live_llm):
    assert live_llm.provider() in {"gemini", "groq", "openai", "openai_compatible"}
    assert live_llm.model_name()
    assert live_llm.status()["provider"] == live_llm.provider()


def test_json_completion_returns_a_dict(live_llm):
    data = live_llm.complete_json(
        'Reply with exactly: {"pong": true}', "You are a test harness."
    )
    assert data == {"pong": True}


def test_model_writes_a_valid_self_contained_demo(tmp_path, monkeypatch, live_llm):
    from modules import demo_generator

    monkeypatch.setattr(demo_generator, "DEMOS", tmp_path)
    result = demo_generator.generate(BUSINESS, CARD["gap"], CARD["observation"])
    html = result["html"]
    assert result["generated_by"].startswith("model:")
    assert html.lower().startswith("<!doctype") and "</html>" in html.lower()
    assert BUSINESS["name"] in html
    assert "viewport" in html
    assert "<link rel=" not in html and "<script src=" not in html


def test_model_email_keeps_every_compliance_line(live_llm):
    from modules import email_draft

    out = email_draft.draft(BUSINESS, CARD, "roast-ccx-b575ea")
    assert out["generated_by"].startswith("model:")
    body = out["body"]
    assert out["subject"].strip()
    assert BUSINESS["name"] in body
    assert CARD["source_url"] in body
    assert email_draft.OPT_OUT in body
    assert email_draft.DISCLAIMER in body
    assert "demo/roast-ccx-b575ea" in body
    assert len(body.split()) < 130
