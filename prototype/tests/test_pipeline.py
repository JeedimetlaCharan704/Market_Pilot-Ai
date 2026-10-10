"""Offline tests for the MarketPilot pipeline.

No test here touches the network: discovery, audit-fetching and the model are
exercised through their pure functions or canned HTML, so the suite is fast and
deterministic on a jury laptop. The live path is recorded in TEST_REPORT.md.
"""

from __future__ import annotations

import pytest

from modules import discovery, email_draft, evidence, store
from modules.audit import _scan
from modules.demo_generator import _validate, generate

# ---------------------------------------------------------------- store


def test_status_flow_rejects_illegal_moves(db, lead):
    with pytest.raises(store.InvalidTransition):
        db.set_status(lead["id"], "SENT")  # DISCOVERED -> SENT skips every gate
    with pytest.raises(store.InvalidTransition):
        db.set_status(lead["id"], "NOT_A_STATUS")


def test_sent_is_terminal(db, lead):
    for target in ("AUDITED", "DEMO_GENERATED", "DRAFTED", "SENT"):
        db.set_status(lead["id"], target)
    assert db.get_lead(lead["id"])["status"] == "SENT"
    with pytest.raises(store.InvalidTransition):
        db.set_status(lead["id"], "DRAFTED")
    with pytest.raises(store.InvalidTransition):
        db.set_status(lead["id"], "FAILED")  # one message per lead, ever


def test_failed_is_retriable(db, lead):
    db.set_status(lead["id"], "AUDITED", "booking")
    db.set_status(lead["id"], "FAILED", "timeout")
    assert db.get_lead(lead["id"])["status"] == "FAILED"
    assert db.get_lead(lead["id"])["status_reason"] == "timeout"
    db.set_status(lead["id"], "DEMO_GENERATED")
    assert db.get_lead(lead["id"])["status"] == "DEMO_GENERATED"


def test_upsert_never_duplicates_a_row(db, lead):
    again = db.upsert_lead({"source": "TEST", "external_id": "1", "name": "Renamed"})
    assert again["id"] == lead["id"]
    assert again["name"] == "Renamed"
    assert len(db.list_leads()) == 1


def test_save_rejects_unknown_columns(db, lead):
    with pytest.raises(ValueError):
        db.save(lead["id"], secret_column="x")


# ---------------------------------------------------------------- rewind


def test_reset_clears_every_stage_but_the_lead_survives(db, lead):
    db.save(lead["id"], audit={"fetch_ok": True}, evidence=[{"gap": "booking"}])
    db.set_status(lead["id"], "AUDITED", "booking")
    db.save(lead["id"], demo_slug="abc123")

    out = db.reset_lead(lead["id"])

    assert out["id"] == lead["id"]
    assert out["status"] == "DISCOVERED"
    assert out["audit"] is None and out["evidence"] is None
    assert out["demo_slug"] is None and out["email_draft"] is None
    assert len(db.list_leads()) == 1  # a rewind is not a delete


def test_reset_refuses_a_sent_lead(db, lead):
    for target in ("AUDITED", "DEMO_GENERATED", "DRAFTED", "SENT"):
        db.set_status(lead["id"], target)

    with pytest.raises(db.ResetRefused, match="terminal"):
        db.reset_lead(lead["id"])
    assert db.get_lead(lead["id"])["status"] == "SENT"


def test_every_status_except_sent_is_rewindable(db):
    assert set(db.STATUSES) - db.REWINDABLE == {"SENT"}


# ---------------------------------------------------------------- discovery


def test_overpass_query_is_well_formed():
    plan = discovery.plan_query("cafes", "Hyderabad")
    assert plan["tag_key"] in {"amenity", "shop", "craft", "office", "tourism"}
    assert plan["location"] == "Hyderabad"

    box = discovery._bbox(17.4, 78.4)
    selectors = discovery._selectors(plan, box, require_website=True)
    assert selectors
    for selector in selectors:
        assert selector.startswith(("node", "way"))
        assert selector.endswith(box)
        assert '["website"]' in selector


def test_bbox_is_ordered_and_narrow():
    south, west, north, east = [float(v) for v in discovery._bbox(17.4, 78.4).strip("()").split(",")]
    assert north > south and east > west
    assert (north - south) < 1 and (east - west) < 1  # a city, not a continent


# ---------------------------------------------------------------- audit


BROKEN_SITE = """
<html><head><title>Cafe</title></head><body>
<a href="/reserve">Make a reservation</a>
<a href="https://wa.me/919999999999">WhatsApp us</a>
<form action="/enquiry"><textarea name="q"></textarea></form>
<meta name="viewport" content="width=device-width, initial-scale=1">
</body></html>
"""

PLAIN_SITE = """
<html><head><title>Plain</title></head><body>
<p>Call us on 040 1234567 or mail hello@example.com.</p>
<a href="/about">About us</a>
</body></html>
"""


def _empty_observed():
    return {"booking": [], "contact": [], "chat": [], "mobile": []}


def test_scan_finds_every_cue_on_a_complete_site():
    observed = _empty_observed()
    _scan("https://x.test/", BROKEN_SITE, observed)
    assert observed["booking"] and observed["contact"]
    assert observed["chat"] and observed["mobile"]


def test_scan_reports_nothing_on_a_bare_site():
    observed = _empty_observed()
    _scan("https://x.test/", PLAIN_SITE, observed)
    assert observed == {"booking": [], "contact": [], "chat": [], "mobile": []}


def test_scan_needs_a_real_form_not_just_a_mailto():
    observed = _empty_observed()
    _scan("https://x.test/", '<html><body><a href="mailto:hi@x.test">Email us</a></body></html>', observed)
    assert observed["contact"] == []


# ---------------------------------------------------------------- evidence


def _audit(**over):
    base = {
        "url": "https://example.com/",
        "fetched_at": "2026-10-09T09:00:00+00:00",
        "fetch_ok": True,
        "gaps": ["booking", "contact"],
        "confidence": "medium",
        "pages_read": 1,
        "pages_probed": [{"url": "https://example.com/", "error": None}],
        "observations": {
            "booking": {"found": False, "observation": "No booking link.", "why_it_matters": "buyers leave"},
            "contact": {"found": False, "observation": "No form.", "why_it_matters": "hard to ask"},
            "chat": {"found": True, "observation": "wa.me", "why_it_matters": "-"},
            "mobile": {"found": True, "observation": "viewport", "why_it_matters": "-"},
        },
    }
    base.update(over)
    return base


def test_every_card_carries_a_url_and_a_date():
    cards = evidence.build_cards({"name": "Roast CCx"}, _audit())
    for card in cards:
        assert card["source_url"] == "https://example.com/"
        assert card["retrieved_at"]
        assert card["observation"]


def test_primary_prefers_booking_then_chat_then_contact():
    cards = evidence.build_cards({"name": "X"}, _audit(gaps=["contact", "booking", "chat"]))
    assert evidence.primary(cards)["gap"] == "booking"


def test_unreadable_site_yields_exactly_one_no_claim_card():
    cards = evidence.build_cards({"name": "X"}, _audit(fetch_ok=False, reason="site could not be fetched", gaps=[]))
    assert len(cards) == 1
    assert cards[0]["gap"] == "unverified"
    assert cards[0]["confidence"] == "low"
    assert evidence.primary(cards)["gap"] == "unverified"


def test_full_compliance_yields_a_no_gap_card_not_a_fabricated_one():
    clean = _audit(gaps=[], observations={
        k: {"found": True, "observation": "present", "why_it_matters": "-"}
        for k in ("booking", "contact", "chat", "mobile")
    })
    cards = evidence.build_cards({"name": "X"}, clean)
    assert len(cards) == 1
    assert cards[0]["gap"] == "none"


# ---------------------------------------------------------------- demo


def test_template_demo_is_complete_self_contained_and_labelled(tmp_path, monkeypatch):
    from modules import demo_generator

    monkeypatch.setattr(demo_generator, "DEMOS", tmp_path)
    result = generate(
        {"name": "Roast CCx", "address": "Banjara Hills", "phone": "040 1234567",
         "search": "cafes"},
        "booking",
        "No booking link found.",
    )
    html = result["html"]
    assert html.lower().startswith("<!doctype")
    assert "</html>" in html.lower()
    assert "Roast CCx" in html
    assert "not a live service" in html
    assert "viewport" in html
    assert result["generated_by"].startswith("template")
    # no external stylesheet or script
    assert "<link rel=" not in html and "<script src=" not in html
    assert 'src="http' not in html


def test_validator_rejects_truncated_or_leaky_model_output():
    with pytest.raises(ValueError, match="truncated"):
        _validate("<!doctype html><html><body>Roast CCx", "Roast CCx")
    with pytest.raises(ValueError, match="business name"):
        _validate("<!doctype html><html></html>", "Roast CCx")
    with pytest.raises(ValueError, match="external resources"):
        _validate(
            '<!doctype html><html><body>Roast CCx'
            '<img src="https://cdn.example.com/x.png"></body></html>',
            "Roast CCx",
        )
    # Markdown fences from a chat model are stripped, not fatal.
    fenced = "```html\n<!doctype html><html><body>Roast CCx</body></html>\n```"
    assert "Roast CCx" in _validate(fenced, "Roast CCx")


# ---------------------------------------------------------------- email


def test_template_email_cites_the_observation_and_opts_out():
    card = evidence.build_cards({"name": "Roast CCx"}, _audit())[0]
    out = email_draft._template(
        {"name": "Roast CCx", "search": "cafes"},
        card,
        "http://localhost:8000/demo/roast-ccx-b575ea",
    )
    body, subject = out["body"], out["subject"]
    assert "Roast CCx" in subject
    assert card["source_url"] in body
    assert card["observation"].rstrip(".") in body
    assert "demo/roast-ccx-b575ea" in body
    assert email_draft.OPT_OUT in body
    assert email_draft.DISCLAIMER in body
    assert body.count("!") == 0  # no hype


def test_send_always_writes_outbox_and_never_opens_a_socket(tmp_path, monkeypatch, lead):
    monkeypatch.setattr(email_draft, "OUTBOX", tmp_path / "outbox.log")
    out = email_draft.send(lead, {"subject": "s", "body": "b", "generated_by": "template"})
    assert out["mode"] == "logged"
    assert out["to"] is None  # no SMTP_TEST_TO configured
    lines = (tmp_path / "outbox.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and '"lead_id"' in lines[0]


# --- the service input ------------------------------------------------------

SERVICE = "AI chatbot development"


def _stub_discovery(monkeypatch, app_module, name="Roast CCx", external_id="node/1"):
    """Offline stand-in for OSM, so no test depends on Overpass being up."""
    def fake(business_type, city, limit=8):
        return {
            "backend": "osm",
            "errors": [],
            "rows": [{
                "source": "OSM",
                "external_id": external_id,
                "name": name,
                "website": "https://example.com/",
                "address": city,
                "city": city,
            }],
        }

    monkeypatch.setattr(app_module.discovery, "search", fake)


def test_search_refuses_without_a_service(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    app_module.store.init_db()
    client = TestClient(app_module.app)

    assert client.post(
        "/api/search", json={"business_type": "cafes", "city": "Hyderabad"}
    ).status_code == 422
    assert client.post(
        "/api/search", json={"service": "   ", "business_type": "cafes", "city": "Hyderabad"}
    ).status_code == 422
    assert client.post(
        "/api/search", json={"service": SERVICE, "business_type": "cafes"}
    ).status_code == 422


def test_service_reaches_the_lead_the_demo_and_the_email(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    app_module.store.init_db()
    _stub_discovery(monkeypatch, app_module)
    client = TestClient(app_module.app)

    found = client.post("/api/search", json={
        "service": SERVICE, "business_type": "cafes", "city": "Hyderabad",
    })
    assert found.status_code == 200, found.text
    saved = found.json()["leads"][0]
    assert saved["service"] == SERVICE
    assert saved["search"] == "cafes in Hyderabad"

    app_module.store.save(saved["id"], evidence=evidence.build_cards(saved, _audit()))
    app_module.store.set_status(saved["id"], "AUDITED", "booking")

    from modules import demo_generator
    monkeypatch.setattr(demo_generator, "DEMOS", tmp_path / "demos")
    made = client.post(f"/api/demo/{saved['id']}")
    assert made.status_code == 200, made.text
    page = (tmp_path / "demos" / f"{made.json()['slug']}.html").read_text(encoding="utf-8")
    assert SERVICE in page
    # the fixed template must not pretend the seller's service was built for them
    assert "Prepared for a seller of" in page
    assert "not a build of that service" in page

    drafted = client.post(f"/api/draft/{saved['id']}")
    assert drafted.status_code == 200, drafted.text
    assert SERVICE in drafted.json()["draft"]["body"]
    assert email_draft.OPT_OUT in drafted.json()["draft"]["body"]


def test_service_is_collapsed_to_one_line(tmp_path, monkeypatch):
    """A multi-line service must not be able to break the page or the email."""
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    app_module.store.init_db()
    _stub_discovery(monkeypatch, app_module)
    client = TestClient(app_module.app)

    found = client.post("/api/search", json={
        "service": "  AI   chatbot \n development  ", "business_type": "cafes", "city": "Hyderabad",
    })
    assert found.json()["leads"][0]["service"] == "AI chatbot development"


def test_demo_template_escapes_the_service():
    from modules import demo_generator

    page = demo_generator._from_template(
        {"name": "Roast CCx", "service": "<script>alert(1)</script>"},
        "an online booking page",
        "Book an appointment",
    )
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_model_written_demo_is_forced_to_disclaim_a_custom_build(lead):
    """A model-written page got the honesty rule as a prompt instruction and dropped it.

    Same pattern as the opt-out line: enforced after the model, not requested from it.
    """
    from modules import demo_generator

    page = "<html><body><h1>Preview</h1></body></html>"
    out = demo_generator._add_service_note(page, "<script>alert(1)</script>")
    assert "not a build of that service" in out
    assert "&lt;script&gt;" in out
    assert "</body>" in out


def test_the_service_note_is_not_added_twice():
    from modules import demo_generator

    template = demo_generator._from_template(
        {"name": "Roast CCx", "service": "AI chatbot development"},
        "an online booking page", "Book",
    )
    once = demo_generator._add_service_note(template, "AI chatbot development")
    assert once == template
    assert once.count("Prepared for a seller of") == 1


def test_a_demo_without_a_service_gets_no_service_note():
    from modules import demo_generator

    page = "<html><body><h1>Preview</h1></body></html>"
    assert demo_generator._add_service_note(page, "") == page


def test_template_email_sells_the_service_without_claiming_a_finished_job(lead):
    card = evidence.build_cards({**lead, "service": SERVICE}, _audit())[0]
    body = email_draft._template({**lead, "service": SERVICE}, card, "http://x/demo/y")["body"]
    assert SERVICE in body
    # framed as the work we do, never as work already delivered to this client
    assert "I work on" in body
    assert "built for you" not in body.lower()
    assert email_draft.OPT_OUT in body


def test_a_lead_without_a_service_still_gets_a_valid_email_and_demo(lead):
    """Backwards compatible: older leads have no service column value."""
    card = evidence.build_cards(lead, _audit())[0]
    body = email_draft._template(lead, card, "http://x/demo/y")["body"]
    assert card["source_url"] in body
    assert email_draft.OPT_OUT in body

    from modules import demo_generator

    page = demo_generator._from_template(lead, "an online booking page", "Book")
    assert "Prepared for a seller of" not in page


def test_model_email_still_names_the_service_it_drops(lead, monkeypatch):
    """The model reliably omitted the service in three live runs, so we append it
    the same way we append the opt-out rather than hoping it complies."""
    from modules import llm

    card = evidence.build_cards({**lead, "service": SERVICE}, _audit())[0]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "provider", lambda: "groq")
    monkeypatch.setattr(llm, "model_name", lambda: "test-model")
    monkeypatch.setattr(
        llm, "complete_json",
        lambda prompt, system: {
            "subject": "Booking missing on your site",
            "body": (
                f"Hi {lead['name']} team,\n\nI read {card['source_url']} and saw no booking "
                f"link.\n\nPreview: http://x/demo/y\n\nIf you'd rather I didn't follow up, "
                "reply with 'stop' and I won't contact you again.\n\n- MarketPilot"
            ),
        },
    )
    out = email_draft.draft({**lead, "service": SERVICE}, card, "slug")
    assert out["generated_by"].startswith("model:")
    assert SERVICE in out["body"]
    assert "I work on" in out["body"]
    # opt-out still lands last
    assert out["body"].strip().endswith(email_draft.DISCLAIMER)


# ---------------------------------------------------------------- api guards


def test_api_refuses_a_demo_or_email_built_from_an_unreadable_site(tmp_path, monkeypatch, lead):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    unverified = evidence.build_cards(
        lead, {"url": lead["website"], "fetch_ok": False, "reason": "site could not be fetched",
               "fetched_at": "2026-10-09T09:00:00+00:00", "gaps": []}
    )
    app_module.store.save(lead["id"], evidence=unverified,
                          status_reason="site could not be fetched")
    app_module.store.set_status(lead["id"], "FAILED", "site could not be fetched")

    client = TestClient(app_module.app)
    assert client.post(f"/api/demo/{lead['id']}").status_code == 409
    assert client.post(f"/api/draft/{lead['id']}").status_code == 409
    assert client.get("/api/leads").status_code == 200


def test_api_builds_the_demo_once_the_gap_is_verified(tmp_path, monkeypatch, lead):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    cards = evidence.build_cards(lead, _audit())
    app_module.store.save(lead["id"], evidence=cards)
    app_module.store.set_status(lead["id"], "AUDITED", "booking")

    client = TestClient(app_module.app)
    made = client.post(f"/api/demo/{lead['id']}")
    assert made.status_code == 200, made.text
    assert made.json()["slug"]

    drafted = client.post(f"/api/draft/{lead['id']}")
    assert drafted.status_code == 200, drafted.text
    assert email_draft.OPT_OUT in drafted.json()["draft"]["body"]

    sent = client.post(f"/api/send/{lead['id']}")
    assert sent.status_code == 200, sent.text
    assert sent.json()["outcome"]["mode"] == "logged"
    assert app_module.store.get_lead(lead["id"])["status"] == "SENT"


def test_api_reset_cannot_unsend_a_message(tmp_path, monkeypatch, lead):
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    cards = evidence.build_cards(lead, _audit())
    app_module.store.save(lead["id"], evidence=cards)
    app_module.store.set_status(lead["id"], "AUDITED", "booking")

    client = TestClient(app_module.app)
    assert client.post(f"/api/reset/{lead['id']}").status_code == 200
    assert app_module.store.get_lead(lead["id"])["status"] == "DISCOVERED"

    app_module.store.set_status(lead["id"], "AUDITED", "booking")
    app_module.store.save(lead["id"], evidence=cards, demo_slug="x")
    app_module.store.set_status(lead["id"], "DEMO_GENERATED")
    app_module.store.save(lead["id"], email_draft={"subject": "s", "body": "b"})
    app_module.store.set_status(lead["id"], "DRAFTED")
    app_module.store.set_status(lead["id"], "SENT", "logged")

    refused = client.post(f"/api/reset/{lead['id']}")
    assert refused.status_code == 409
    assert app_module.store.get_lead(lead["id"])["status"] == "SENT"


def test_ui_page_renders():
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    page = client.get("/")
    assert page.status_code == 200
    assert "MarketPilot" in page.text


# --- outreach guard rails ---------------------------------------------------


def test_sent_today_counts_only_what_was_sent(db, lead):
    assert db.sent_today() == 0
    for target in ("AUDITED", "DEMO_GENERATED", "DRAFTED", "SENT"):
        db.set_status(lead["id"], target)
    assert db.sent_today() == 1
    db.set_status(lead["id"], "SENT")  # already sent, count must not double
    assert db.sent_today() == 1


def test_send_refuses_once_the_daily_cap_is_reached(tmp_path, monkeypatch, lead):
    """The cap is enforced in the route, not left to the model or the UI."""
    from fastapi.testclient import TestClient

    import app as app_module
    from modules.settings import describe as real_describe

    monkeypatch.setattr(app_module.store, "DB_PATH", tmp_path / "test.db")
    cards = evidence.build_cards(lead, _audit())
    app_module.store.save(lead["id"], evidence=cards)
    app_module.store.set_status(lead["id"], "AUDITED", "booking")
    client = TestClient(app_module.app)

    assert client.post(f"/api/demo/{lead['id']}").status_code == 200
    assert client.post(f"/api/draft/{lead['id']}").status_code == 200

    monkeypatch.setattr(
        app_module, "describe", lambda: {**real_describe(), "daily_send_cap": 0}
    )
    refused = client.post(f"/api/send/{lead['id']}")
    assert refused.status_code == 429, refused.text
    assert "cap" in refused.json()["detail"].lower()
    # the lead is still DRAFTED: nothing was contacted, so it can go out tomorrow
    assert app_module.store.get_lead(lead["id"])["status"] == "DRAFTED"


def test_config_advertises_the_opt_out_and_the_cap():
    from fastapi.testclient import TestClient

    import app as app_module

    cfg = TestClient(app_module.app).get("/api/config").json()
    assert cfg["daily_send_cap"] >= 0
    assert "stop" in cfg["opt_out"].lower()
    assert "once per business" in cfg["disclaimer"].lower()
    assert cfg["sent_today"] >= 0


# --- fetch layer -------------------------------------------------------------


class _ScriptedClient:
    """Stands in for net._client; first call fails, later ones succeed."""

    def __init__(self, failures: int, status: int = 200):
        self.failures = failures
        self.status = status
        self.calls = 0

    def get(self, url, timeout=None):  # mirrors httpx.Client.get
        import httpx

        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadTimeout("cold start")
        return httpx.Response(self.status, text="ok")


def test_get_retries_once_on_a_cold_start_timeout(monkeypatch):
    from modules import net

    scripted = _ScriptedClient(failures=1)
    monkeypatch.setattr(net, "_client", scripted)
    monkeypatch.setattr(net, "_throttle", lambda url: None)
    monkeypatch.setattr(net, "RETRY_BACKOFF_S", 0)

    response = net.get("https://example.com/", check_robots=False)
    assert response.status_code == 200
    assert scripted.calls == 2


def test_get_gives_up_after_the_single_retry(monkeypatch):
    from modules import net

    scripted = _ScriptedClient(failures=99)
    monkeypatch.setattr(net, "_client", scripted)
    monkeypatch.setattr(net, "_throttle", lambda url: None)
    monkeypatch.setattr(net, "RETRY_BACKOFF_S", 0)

    with pytest.raises(net.FetchError):
        net.get("https://example.com/", check_robots=False)
    assert scripted.calls == net.RETRIES + 1


def test_get_treats_an_http_error_status_as_an_answer_not_a_failure(monkeypatch):
    """A 500 is the server speaking. Retrying it would just hammer a sick host."""
    from modules import net

    scripted = _ScriptedClient(failures=0, status=500)
    monkeypatch.setattr(net, "_client", scripted)
    monkeypatch.setattr(net, "_throttle", lambda url: None)

    response = net.get("https://example.com/", check_robots=False)
    assert response.status_code == 500
    assert scripted.calls == 1


def test_audit_retries_once_when_every_probe_fails(monkeypatch):
    """A cold function fails its first contact with a host - that must not make
    a healthy site look unreadable."""
    from modules import audit

    pages_per_pass = 6  # audit._read stops after six probes
    calls = {"n": 0}
    good = '<html><head><title>Cafe</title></head><body><h1>Cafe</h1></body></html>'

    def scripted_probe(url):
        calls["n"] += 1
        if calls["n"] <= pages_per_pass:
            return {"url": url, "status": None, "html": "", "error": "ConnectError: cold", "blocked": False}
        return {"url": url, "status": 200, "html": good, "error": None, "blocked": False}

    monkeypatch.setattr(audit, "_probe", scripted_probe)
    result = audit.audit_site("https://example.com/")
    assert result["fetch_ok"] is True
    assert calls["n"] > pages_per_pass


def test_audit_reports_unreadable_only_after_the_retry_too(monkeypatch):
    from modules import audit

    pages_per_pass = 6
    calls = {"n": 0}

    def always_down(url):
        calls["n"] += 1
        return {"url": url, "status": None, "html": "", "error": "ConnectError: cold", "blocked": False}

    monkeypatch.setattr(audit, "_probe", always_down)
    result = audit.audit_site("https://example.com/")
    assert result["fetch_ok"] is False
    assert result["reason"] == "site could not be fetched"
    assert calls["n"] == 2 * pages_per_pass
