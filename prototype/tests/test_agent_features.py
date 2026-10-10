"""Tests for the reply, opt-out, scoring, worker and provider layers."""

import pytest


def _audit(fetch_ok=True, gaps=("booking", "contact"), found=None):
    found = found or {}
    return {
        "url": "https://example.com/",
        "fetch_ok": fetch_ok,
        "reason": None,
        "fetched_at": "2026-10-09T12:00:00+00:00",
        "pages_read": 1,
        "pages_probed": [{"url": "https://example.com/", "status": 200, "error": None}],
        "gaps": list(gaps),
        "observations": {
            g: {
                "found": found.get(g, False),
                "observation": f"no {g} element found",
                "why_it_matters": "visitors cannot act",
            }
            for g in gaps
        },
        "confidence": "high",
    }


# --- scoring -----------------------------------------------------------------


def test_every_card_carries_a_score_and_a_reason():
    from modules import evidence

    cards = evidence.build_cards({"name": "Roast CCx", "service": "AI chatbot development"}, _audit())
    assert cards
    for card in cards:
        assert 0 <= card["score"] <= 100
        assert card["why_it_fits"]
        assert set(card["score_parts"]) == {
            "revenue_impact", "fix_effort", "evidence_strength", "service_fit",
        }


def test_the_score_is_deterministic():
    from modules import evidence

    business = {"name": "Roast CCx", "service": "AI chatbot development"}
    first = evidence.build_cards(business, _audit())[0]["score"]
    second = evidence.build_cards(business, _audit())[0]["score"]
    assert first == second


def test_a_service_that_matches_the_gap_outranks_a_mismatch():
    from modules import evidence

    chat_fit = evidence.score_card("chat", "high", 1, "AI chatbot development")
    contact_fit = evidence.score_card("contact", "high", 1, "AI chatbot development")
    assert chat_fit["score_parts"]["service_fit"] == 1.0
    assert contact_fit["score_parts"]["service_fit"] < 1.0
    assert chat_fit["score"] > contact_fit["score"]


def test_a_no_gap_card_scores_nothing_and_says_so():
    from modules import evidence

    cards = evidence.build_cards(
        {"name": "Roast CCx"}, _audit(gaps=(), found={})
    )
    assert cards[0]["gap"] == "none"
    assert cards[0]["score"] == 0
    assert "nothing to pitch" in cards[0]["why_it_fits"]


def test_primary_picks_the_highest_score_not_the_first_gap():
    from modules import evidence

    cards = evidence.build_cards(
        {"name": "Roast CCx", "service": "AI chatbot development"},
        _audit(gaps=("contact", "chat")),
    )
    assert {c["gap"] for c in cards} == {"contact", "chat"}
    assert evidence.primary(cards)["gap"] == "chat"


def test_scoring_weights_always_sum_to_one():
    from modules.settings import scoring_weights

    weights = scoring_weights()
    assert abs(sum(weights.values()) - 1.0) < 0.001


# --- opt-out registry --------------------------------------------------------


def test_opt_outs_are_recorded_listed_and_removed(db):
    assert db.add_opt_out("Owner@Example.com ", lead_id=1) is True
    assert db.is_opted_out("owner@example.com") is True
    assert db.is_opted_out("nobody@example.com") is False
    assert len(db.list_opt_outs()) == 1
    # A repeat is not a new entry - the list cannot be padded.
    assert db.add_opt_out("owner@example.com") is False
    assert len(db.list_opt_outs()) == 1
    assert db.remove_opt_out("owner@example.com") is True
    assert db.is_opted_out("owner@example.com") is False


def test_an_empty_address_is_never_recorded(db):
    assert db.add_opt_out("   ") is False
    assert db.list_opt_outs() == []


def test_opt_out_blocks_a_send_before_the_cap_is_even_considered(db, lead, monkeypatch):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", db.DB_PATH)
    app_module.store.init_db()
    app_module.store.save(
        lead["id"], email="owner@example.com", email_draft={"subject": "s", "body": "b"}
    )
    app_module.store.set_status(lead["id"], "AUDITED", "chat")
    app_module.store.set_status(lead["id"], "DEMO_GENERATED")
    app_module.store.set_status(lead["id"], "DRAFTED")

    client = TestClient(app_module.app)
    # SMTP_TEST_TO is empty in tests, so the recipient falls back to the lead's own address.
    app_module.store.add_opt_out("owner@example.com", lead["id"])

    refused = client.post(f"/api/send/{lead['id']}")
    assert refused.status_code == 403
    assert "do-not-contact" in refused.json()["detail"]
    assert app_module.store.get_lead(lead["id"])["status"] == "DRAFTED"


# --- reply classification ----------------------------------------------------


def test_an_opt_out_is_honoured_by_regex_before_any_model_is_consulted(monkeypatch):
    from modules import replies

    def boom(*a, **k):
        raise AssertionError("the model must not be consulted for an opt-out")

    monkeypatch.setattr(replies.llm, "available", lambda: True)
    monkeypatch.setattr(replies.llm, "complete_json", boom)
    assert replies.classify("Please STOP emailing me, unsubscribe me now.") == "opt_out"


def test_classification_falls_back_to_keywords_with_no_model():
    from modules import replies

    assert replies.classify("Yes, call me tomorrow please") == "interested"
    assert replies.classify("How much does it cost?") == "question"
    assert replies.classify("No thanks, not interested") == "not_interested"
    assert replies.classify("asdf") == "unclear"
    assert replies.classify("") == "unclear"


def test_a_model_that_says_opt_out_is_still_honoured(monkeypatch):
    from modules import replies

    monkeypatch.setattr(replies.llm, "available", lambda: True)
    monkeypatch.setattr(
        replies.llm, "complete_json",
        lambda prompt, system, retries=1: {"classification": "opt_out", "summary": "stop"},
    )
    assert replies.classify("kind regards") == "opt_out"


def test_a_rubbish_model_answer_degrades_to_the_heuristic(monkeypatch):
    from modules import replies

    monkeypatch.setattr(replies.llm, "available", lambda: True)
    monkeypatch.setattr(
        replies.llm, "complete_json",
        lambda prompt, system, retries=1: {"classification": "banana"},
    )
    assert replies.classify("Yes please, call me") == "interested"


# --- auto-reply and the store ----------------------------------------------


def test_a_lead_can_only_ever_be_auto_replied_to_once(db, lead):
    assert db.can_auto_reply(lead["id"]) is True
    db.mark_auto_replied(lead["id"])
    assert db.can_auto_reply(lead["id"]) is False


def test_replies_are_recorded_and_lookup_by_address_is_case_insensitive(db, lead):
    db.save(lead["id"], email="Owner@RoastCoffee.in")
    found = db.get_lead_by_email("owner@roastcoffee.in")
    assert found["id"] == lead["id"]
    db.record_reply(lead["id"], "owner@roastcoffee.in", "Re: hello", "interested")
    assert db.list_replies(lead["id"])[0]["classification"] == "interested"


# --- the worker --------------------------------------------------------------


def test_the_worker_refuses_to_start_on_a_serverless_host(monkeypatch):
    from modules import worker

    monkeypatch.setattr(worker, "is_serverless", lambda: True)
    with pytest.raises(worker.WorkerUnavailable):
        worker.start()
    assert worker.status()["supported"] is False


def test_a_worker_tick_with_no_imap_configured_reports_cleanly(monkeypatch):
    from modules import replies, worker

    monkeypatch.setattr(replies, "configured", lambda: False)
    result = worker.tick()
    assert result["ok"] is False
    assert "IMAP" in result["reason"]


# --- notify ------------------------------------------------------------------


def test_telegram_is_a_no_op_when_unconfigured(monkeypatch):
    from modules import notify

    monkeypatch.setattr(notify, "configured", lambda: False)
    assert notify.send("hello") is False
    assert notify.notify_send({"name": "x"}, "logged", None) is False


# --- config ------------------------------------------------------------------


def test_config_yaml_is_loaded_and_env_still_wins(monkeypatch):
    from modules import settings as settings_module

    cfg = settings_module.load_config()
    assert cfg["scoring"]["weights"]["revenue_impact"] == 0.40

    monkeypatch.setenv("DAILY_SEND_CAP", "7")
    settings_module.settings.cache_clear()
    try:
        assert settings_module.settings()["daily_send_cap"] == "7"
    finally:
        monkeypatch.delenv("DAILY_SEND_CAP", raising=False)
        settings_module.settings.cache_clear()


def test_describe_reports_the_new_capabilities():
    from fastapi.testclient import TestClient

    import app as app_module

    cfg = TestClient(app_module.app).get("/api/config").json()
    for key in ("imap_configured", "telegram_configured", "auto_reply", "notify_telegram",
                "opt_outs_recorded", "worker", "discovery"):
        assert key in cfg, key
    # Three discovery backends, honestly reported.
    assert set(cfg["discovery"]) >= {"osm", "google_places", "scraper_docker"}


def test_discovery_health_names_the_active_backend():
    from modules import discovery

    health = discovery.health()
    assert health["_active"] in health
    assert health["osm"]["hint"]


# --- the endpoints -----------------------------------------------------------


def test_optout_endpoints_round_trip():
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    added = client.post("/api/optout", json={"email": "owner@example.com"})
    assert added.status_code == 200 and added.json()["added"] is True

    assert client.post("/api/optout", json={"email": "not-an-email"}).status_code == 422

    listed = client.get("/api/optout").json()["opt_outs"]
    assert any(o["email"] == "owner@example.com" for o in listed)

    removed = client.delete("/api/optout/owner@example.com")
    assert removed.status_code == 200
    assert client.delete("/api/optout/owner@example.com").status_code == 404


def test_polling_with_no_imap_is_a_503_not_a_crash():
    from fastapi.testclient import TestClient

    import app as app_module

    response = TestClient(app_module.app).post("/api/replies/poll")
    assert response.status_code == 503
    assert "IMAP" in response.json()["detail"]


def test_the_worker_endpoint_refuses_on_serverless(monkeypatch):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.worker, "is_serverless", lambda: True)
    response = TestClient(app_module.app).post("/api/worker")
    assert response.status_code == 409


def test_zero_website_lead_audit_and_starter_demo(monkeypatch, tmp_path):
    from modules import audit, demo_generator, email_draft, evidence

    monkeypatch.setattr(demo_generator, "DEMOS", tmp_path / "demos")
    # 1. Audit site with no website URL
    res = audit.audit_site("")
    assert res["fetch_ok"] is True
    assert "no_website" in res["gaps"]

    # 2. Build evidence card
    lead = {
        "id": 42,
        "name": "Hyderabad Luxury Estates",
        "search": "real estate in Hyderabad",
        "phone": "+91 98765 43210",
        "city": "Hyderabad",
        "service": "AI voice agent for real estate",
    }
    cards = evidence.build_cards(lead, res)
    assert len(cards) >= 1
    assert cards[0]["gap"] == "no_website"
    assert cards[0]["score"] >= 80

    # 3. Generate starter site demo with AI call simulator
    demo = demo_generator.generate(lead, "no_website", cards[0]["observation"])
    assert "Hyderabad Luxury Estates" in demo["html"]
    assert "Interactive AI Voice" in demo["html"]
    assert "Simulate Inbound Lead Call" in demo["html"]

    # 4. Draft outreach with multi-channel options
    draft = email_draft.draft(lead, cards[0], demo["slug"])
    assert "Hyderabad Luxury Estates" in draft["subject"]
    assert draft["whatsapp_url"] is not None
    assert "wa.me/919876543210" in draft["whatsapp_url"]
    assert "30-second telephone script" in draft["call_script"].lower() or "1. opener:" in draft["call_script"].lower()


def test_inbound_inquiry_triggers_needs_human_transition(db, lead, monkeypatch):
    from modules import notify, replies

    alerts = []
    monkeypatch.setattr(notify, "configured", lambda: True)
    monkeypatch.setattr(notify, "notify_human_decision", lambda *a, **k: alerts.append(k or a))

    # Give lead an email and transition to SENT
    db.save(lead["id"], email="owner@roastccx.in")
    db.set_status(lead["id"], "AUDITED", "booking")
    db.set_status(lead["id"], "DRAFTED", "ready")
    db.set_status(lead["id"], "SENT", "logged")

    raw_inquiry = (
        "From: owner@roastccx.in\r\n"
        "Subject: Re: Your pitch\r\n\r\n"
        "What are your pricing packages and how much does this cost?\r\n"
    )

    _ = replies._handle(raw_inquiry)
    updated = db.get_lead(lead["id"])
    assert updated["status"] == "NEEDS_HUMAN"
    assert len(alerts) >= 1


def test_hinglish_negation_classified_as_not_interested():
    from modules import replies

    assert replies.classify("Abhi nahi chahiye, baad me baat karenge") in {"not_interested", "opt_out"}
    assert replies.classify("nahi chahiye stop") == "opt_out"
    assert replies.classify("mat bhejo") == "opt_out"
    assert replies.classify("band karo please") == "opt_out"


def test_reply_attaches_to_lead_via_subject_matching(db, lead):
    from modules import replies

    # Lead has name 'Roast Coffee' but no email
    db.save(lead["id"], email=None)
    db.set_status(lead["id"], "AUDITED", "booking")
    db.set_status(lead["id"], "DRAFTED", "ready")
    db.set_status(lead["id"], "SENT", "logged")

    raw_reply = (
        "From: unknown_person@gmail.com\r\n"
        f"Subject: Re: Website review for {lead['name']}\r\n\r\n"
        "Yes, we are interested, please call me.\r\n"
    )

    res = replies._handle(raw_reply)
    assert res["lead_id"] == lead["id"]
    assert res["classification"] == "interested"
    updated = db.get_lead(lead["id"])
    assert updated["status"] == "INTERESTED"


def test_demo_page_serves_from_db_when_disk_missing(db, lead):
    from fastapi.testclient import TestClient

    import modules.demo_generator as dg
    from app import app

    slug = "test-resilient-demo-slug"
    test_html = f"<!doctype html><html><body><h1>{lead['name']}</h1><p>Demo Content</p></html>"
    db.save(lead["id"], demo_slug=slug, demo_html=test_html)

    # Ensure file is NOT on disk
    disk_file = dg.DEMOS / f"{slug}.html"
    if disk_file.exists():
        disk_file.unlink()

    client = TestClient(app)
    resp = client.get(f"/demo/{slug}")
    assert resp.status_code == 200
    assert lead["name"] in resp.text
    assert "Demo Content" in resp.text


def test_dialect_detection_for_metropolitan_regions():
    from modules.email_draft import detect_dialect

    assert detect_dialect("Hyderabad")[0] == "telugu"
    assert detect_dialect("Secunderabad")[0] == "telugu"
    assert detect_dialect("Pune, Maharashtra")[0] == "marathi"
    assert detect_dialect("Mumbai")[0] == "marathi"
    assert detect_dialect("New Delhi")[0] == "hindi"
    assert detect_dialect("Gurgaon")[0] == "hindi"
    assert detect_dialect("London, UK")[0] == "english"
    # Explicit request overrides city
    assert detect_dialect("London", requested_lang="telugu")[0] == "telugu"
    assert detect_dialect("Hyderabad", requested_lang="marathi")[0] == "marathi"


def test_one_click_site_claim_flow(db, lead):
    from fastapi.testclient import TestClient
    from app import app

    slug = "claimable-cafe-demo"
    db.save(lead["id"], demo_slug=slug, demo_html="<html><body>Demo</body></html>")
    client = TestClient(app)

    claim_resp = client.post(
        f"/api/claim/{slug}",
        json={"name": "Rajesh Kumar", "phone": "+919876543210", "email": "rajesh@cafe.com", "notes": "Need custom domain"}
    )
    assert claim_resp.status_code == 200
    data = claim_resp.json()
    assert data["ok"] is True
    assert data["lead"]["status"] == "CLAIMED"
    assert "Rajesh Kumar" in data["lead"]["claimed_by"]
    assert "claimable-cafe-demo.marketpilot.live" in data["vanity_url"]


def test_crm_synchronization_pipeline(db, lead):
    from fastapi.testclient import TestClient
    from modules import crm
    from app import app

    status = crm.status()
    assert "mode" in status

    sync_res = crm.sync_lead(lead, event="unit_test")
    assert sync_res["ok"] is True
    assert sync_res["destinations"]["local"] is True

    client = TestClient(app)
    resp = client.post(f"/api/crm/sync/{lead['id']}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_demo_includes_speech_voice_and_claim_controls():
    from modules.demo_generator import generate

    biz = {"name": "Roast CCx", "phone": "+919876543210", "address": "Banjara Hills", "service": "AI voice agent"}
    res = generate(biz, "booking", "No booking link")
    html = res["html"]
    # Verify Web Speech API integration
    assert "speechSynthesis" in html
    assert "SpeechSynthesisUtterance" in html
    assert "speakVoice" in html
    # Verify Claim Ownership modal integration
    assert "openClaimModal" in html
    assert "Claim Ownership" in html



