"""MarketPilot prototype - FastAPI app.

Run:  uvicorn app:app --reload   (from the project directory)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from modules import crm, demo_generator, discovery, email_draft, evidence, llm, notify, replies, store, worker
from modules.audit import AuditError, audit_site
from modules.net import FetchError
from modules.settings import ROOT, describe

app = FastAPI(title="MarketPilot prototype", docs_url="/api/docs")
TEMPLATES = ROOT / "templates"

store.init_db()


# --- request models ---------------------------------------------------------


class SearchRequest(BaseModel):
    business_type: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    service: str = Field(min_length=2, max_length=120)


# --- pages ------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    path = TEMPLATES / "index.html"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "<h1>MarketPilot</h1>"


@app.get("/demo/{slug}", response_class=HTMLResponse)
def demo_page(slug: str) -> HTMLResponse:
    # 1. Check disk in runtime demos directory
    path = demo_generator.DEMOS / f"{slug}.html"
    if path.is_file():
        return HTMLResponse(content=path.read_text(encoding="utf-8"))

    # 2. Check bundled demos in repo ROOT / demos
    bundled = ROOT / "demos" / f"{slug}.html"
    if bundled.is_file():
        return HTMLResponse(content=bundled.read_text(encoding="utf-8"))

    # 3. Check database for persisted HTML (survives Vercel container recycling)
    lead = store.get_lead_by_slug(slug)
    if lead and lead.get("demo_html"):
        try:
            demo_generator.DEMOS.mkdir(parents=True, exist_ok=True)
            path.write_text(lead["demo_html"], encoding="utf-8")
        except Exception:
            pass
        return HTMLResponse(content=lead["demo_html"])

    # 4. If lead exists but demo_html wasn't cached, re-render on the fly
    if lead:
        card = evidence.primary(lead.get("evidence") or [])
        gap = card["gap"] if card else ("no_website" if not lead.get("website") else "none")
        obs = card["observation"] if card else "Verified gap analysis"
        res = demo_generator.generate(lead, gap, obs)
        store.save(lead["id"], demo_html=res["html"], demo_slug=slug)
        return HTMLResponse(content=res["html"])

    raise HTTPException(404, "no such demo")


# --- API --------------------------------------------------------------------


@app.get("/api/config")
def config() -> dict:
    """Everything a reviewer needs to see the guard rails without reading code."""
    return {
        **describe(),
        "llm": llm.status(),
        "counts": store.counts(),
        "sent_today": store.sent_today(),
        "opt_out": email_draft.OPT_OUT,
        "disclaimer": email_draft.DISCLAIMER,
        "opt_outs_recorded": len(store.list_opt_outs()),
        "discovery": discovery.health(),
        "worker": worker.status(),
        "last_poll": store.last_poll(),
        "idea_performance": store.idea_performance(),
    }


@app.get("/api/analytics")
def analytics() -> dict:
    """Reply learning analytics: conversion rates by idea & gap angle."""
    return {
        "idea_performance": store.idea_performance(),
        "sent_today": store.sent_today(),
        "daily_send_cap": describe()["daily_send_cap"],
        "counts": store.counts(),
    }


@app.post("/api/search")
def search(body: SearchRequest) -> dict:
    """Three inputs -> real local businesses, persisted with status DISCOVERED."""
    service = " ".join(body.service.split())
    if not service or len(service) < 3:
        raise HTTPException(422, "Service is required (at least 3 characters) - say what you sell.")
    if not body.business_type.strip():
        raise HTTPException(422, "business_type is required.")
    if not body.city.strip():
        raise HTTPException(422, "city is required.")

    try:
        result = discovery.search(body.business_type.strip(), body.city.strip())
    except (discovery.DiscoveryError, FetchError) as exc:
        raise HTTPException(502, str(exc)) from exc

    saved = []
    for row in result["rows"]:
        row["search"] = f"{body.business_type} in {body.city}"
        row["service"] = service
        saved.append(store.upsert_lead(row))
    return {
        "backend": result["backend"],
        "errors": result["errors"],
        "count": len(saved),
        "leads": saved,
    }


@app.get("/api/leads")
def leads(search: str | None = None) -> dict:
    rows = store.list_leads(search)
    return {"leads": rows, "counts": store.counts()}


@app.post("/api/audit/{lead_id}")
def run_audit(lead_id: int) -> dict:
    lead = _lead(lead_id)
    try:
        audit = audit_site(lead.get("website") or "")
    except AuditError as exc:
        store.set_status(lead_id, "FAILED", f"audit: {exc}")
        raise HTTPException(422, str(exc)) from exc
    except FetchError as exc:
        store.set_status(lead_id, "FAILED", f"fetch: {exc}")
        raise HTTPException(502, str(exc)) from exc

    disc_email = audit.get("discovered_email")
    if disc_email and not lead.get("email"):
        store.save(lead_id, email=disc_email)
        lead["email"] = disc_email

    cards = evidence.build_cards(lead, audit)
    store.save(lead_id, audit=audit, evidence=cards)

    if not audit["fetch_ok"]:
        store.set_status(lead_id, "FAILED", audit["reason"])
        return {"lead": store.get_lead(lead_id), "audit": audit, "cards": cards}

    target = "AUDITED" if audit["gaps"] else "NO_GAP"
    reason = audit["primary_gap"] or "all four checks passed"
    return {"lead": store.set_status(lead_id, target, reason), "audit": audit, "cards": cards}


@app.post("/api/demo/{lead_id}")
def run_demo(lead_id: int) -> dict:
    lead = _lead(lead_id)
    card = _verified_card(lead)
    try:
        result = demo_generator.generate(lead, card["gap"], card["observation"])
    except (ValueError, llm.LLMError) as exc:
        store.set_status(lead_id, "FAILED", f"demo: {exc}")
        raise HTTPException(502, str(exc)) from exc

    store.save(lead_id, demo_slug=result["slug"], demo_html=result["html"])
    lead = store.set_status(lead_id, "DEMO_GENERATED", result["generated_by"])
    return {
        "lead": lead,
        "slug": result["slug"],
        "generated_by": result["generated_by"],
        "url": email_draft.demo_url(result["slug"]),
    }


class DraftRequest(BaseModel):
    language: str | None = "auto"


class ClaimRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=6, max_length=40)
    email: str | None = None
    notes: str | None = None


@app.post("/api/draft/{lead_id}")
def run_draft(lead_id: int, body_req: DraftRequest | None = None) -> dict:
    lead = _lead(lead_id)
    if not lead.get("demo_slug"):
        raise HTTPException(409, "Generate the demo first - the email links to it.")

    # Auto-discover customer email from website if missing
    if not lead.get("email") and lead.get("website"):
        try:
            report = audit_site(lead["website"])
            if report.get("discovered_email"):
                lead["email"] = report["discovered_email"]
                store.save(lead_id, email=lead["email"])
        except Exception:
            pass

    card = _verified_card(lead)
    lang = (body_req.language if body_req and body_req.language else "auto")
    try:
        body = email_draft.draft(lead, card, lead["demo_slug"], language=lang)
    except (llm.LLMError, ValueError) as exc:
        store.set_status(lead_id, "FAILED", f"draft: {exc}")
        raise HTTPException(502, str(exc)) from exc

    store.save(lead_id, email_draft=body)
    return {
        "lead": store.set_status(lead_id, "DRAFTED", body["generated_by"]),
        "draft": body,
    }


@app.post("/api/send/{lead_id}")
def run_send(lead_id: int) -> dict:
    lead = _lead(lead_id)
    if lead["status"] != "DRAFTED":
        raise HTTPException(409, f"Lead must be DRAFTED before sending (is {lead['status']}).")
    if not lead.get("email_draft"):
        raise HTTPException(409, "No draft to send.")

    # Auto-discover customer email from website if missing
    if not lead.get("email") and lead.get("website"):
        try:
            report = audit_site(lead["website"])
            if report.get("discovered_email"):
                lead["email"] = report["discovered_email"]
                store.save(lead_id, email=lead["email"])
        except Exception:
            pass

    recipient = settings_recipient(lead)
    if recipient and store.is_opted_out(recipient):
        raise HTTPException(
            403,
            f"{recipient} asked not to be contacted and is on the permanent "
            "do-not-contact list. Remove it from /api/optout only if they re-consent.",
        )
    cap = describe()["daily_send_cap"]
    used = store.sent_today()
    if used >= cap:
        raise HTTPException(
            429,
            f"Daily outreach cap reached ({used}/{cap} today). No further business is "
            "contacted until tomorrow - this is what stops the tool becoming a mailer.",
        )
    try:
        outcome = email_draft.send(lead, lead["email_draft"])
    except RuntimeError as exc:
        store.set_status(lead_id, "FAILED", f"send: {exc}")
        raise HTTPException(502, str(exc)) from exc

    crm.sync_lead(lead, event="outreach_sent")
    if describe()["notify_telegram"]:
        notify.notify_send(lead, outcome["mode"], outcome.get("to"))
    return {"lead": store.set_status(lead_id, "SENT", outcome["mode"]), "outcome": outcome}


@app.post("/api/claim/{slug}")
def run_claim(slug: str, body: ClaimRequest) -> dict:
    """Owner claims turnkey demo preview site."""
    lead = store.get_lead_by_slug(slug)
    if not lead:
        raise HTTPException(404, f"No demo found for slug: {slug}")
    claimed_info = f"{body.name} ({body.phone}" + (f", {body.email})" if body.email else ")")
    updated = store.claim_lead(slug, claimed_info, body.notes or "")
    if not updated:
        raise HTTPException(400, "Failed to claim site.")

    if describe()["notify_telegram"]:
        notify.send(
            f"🎉 BUSINESS CLAIMED SITE!\n"
            f"Business: {updated['name']}\n"
            f"Claimed by: {claimed_info}\n"
            f"Demo URL: {email_draft.demo_url(slug)}\n"
            f"Notes: {body.notes or 'None'}"
        )
    crm.sync_lead(updated, event="business_claimed_site")
    return {
        "ok": True,
        "lead": updated,
        "vanity_url": f"https://{slug}.marketpilot.live",
        "message": f"Site claimed successfully by {body.name}. MarketPilot provisioning activated!",
    }


@app.get("/api/crm/status")
def get_crm_status() -> dict:
    """Check active CRM sync destinations."""
    return crm.status()


@app.post("/api/crm/sync/{lead_id}")
def run_crm_sync(lead_id: int) -> dict:
    """Manually trigger CRM sync for a specific lead."""
    lead = _lead(lead_id)
    result = crm.sync_lead(lead, event="manual_sync")
    return {"ok": True, "lead": store.get_lead(lead_id), "result": result}


def settings_recipient(lead: dict) -> str:
    """Who this send would actually reach, for opt-out purposes."""
    from modules.settings import settings

    return (lead.get("email") or settings()["smtp_test_to"] or "").strip().lower()


class DraftUpdateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)


@app.get("/api/lead/{lead_id}")
def get_lead_detail(lead_id: int) -> dict:
    return {"lead": _lead(lead_id)}


class LeadUpdateRequest(BaseModel):
    email: str | None = None
    phone: str | None = None
    website: str | None = None


@app.put("/api/lead/{lead_id}")
def update_lead(lead_id: int, body: LeadUpdateRequest) -> dict:
    _lead(lead_id)
    fields = {}
    if body.email is not None:
        fields["email"] = body.email.strip() or None
    if body.phone is not None:
        fields["phone"] = body.phone.strip() or None
    if body.website is not None:
        fields["website"] = body.website.strip() or None
    if fields:
        store.save(lead_id, **fields)
    return {"lead": store.get_lead(lead_id)}


@app.put("/api/draft/{lead_id}")
def update_draft(lead_id: int, body: DraftUpdateRequest) -> dict:
    lead = _lead(lead_id)
    draft = lead.get("email_draft") or {}
    draft["subject"] = body.subject
    draft["body"] = body.body
    draft["edited"] = True
    store.save(lead_id, email_draft=draft)
    return {"lead": store.get_lead(lead_id), "draft": draft}


@app.post("/api/reset/{lead_id}")
def reset_lead(lead_id: int) -> dict:
    """Wind a lead back to DISCOVERED for a re-audit. Refuses a SENT lead."""
    _lead(lead_id)
    try:
        return {"lead": store.reset_lead(lead_id)}
    except store.ResetRefused as exc:
        raise HTTPException(409, str(exc)) from exc


# --- opt-out registry --------------------------------------------------------


class OptOutRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    lead_id: int | None = None
    source: str = Field(default="manual", max_length=40)


@app.get("/api/optout")
def list_opt_outs() -> dict:
    """The permanent do-not-contact list. A send to any of these is refused."""
    return {"opt_outs": store.list_opt_outs()}


@app.post("/api/optout")
def add_opt_out(body: OptOutRequest) -> dict:
    address = body.email.strip().lower()
    if "@" not in address or " " in address:
        raise HTTPException(422, "That does not look like an email address.")
    added = store.add_opt_out(address, body.lead_id, body.source)
    return {"added": added, "email": address, "opt_outs": store.list_opt_outs()}


@app.delete("/api/optout/{email}")
def remove_opt_out(email: str) -> dict:
    if not store.remove_opt_out(email):
        raise HTTPException(404, "that address is not on the list")
    return {"removed": True, "email": email.strip().lower()}


# --- replies and the worker --------------------------------------------------


@app.get("/api/replies")
def list_replies(lead_id: int | None = None) -> dict:
    return {
        "replies": store.list_replies(lead_id),
        "last_poll": store.last_poll(),
        "imap_configured": replies.configured(),
    }


@app.post("/api/replies/poll")
def poll_replies() -> dict:
    result = replies.poll()
    if not result.get("ok"):
        raise HTTPException(503, result.get("reason", "IMAP unavailable"))
    return result


@app.get("/api/worker")
def worker_status() -> dict:
    return worker.status()


@app.post("/api/worker")
def worker_start() -> dict:
    try:
        return worker.start()
    except worker.WorkerUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/worker")
def worker_stop() -> dict:
    return worker.stop()


def _lead(lead_id: int) -> dict:
    lead = store.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


def _verified_card(lead: dict) -> dict:
    card = evidence.primary(lead.get("evidence") or [])
    if card is None:
        raise HTTPException(409, "Run the audit first - a demo needs an evidence card.")
    if card["gap"] == "unverified":
        raise HTTPException(
            409,
            "This site could not be read, so there is no verified gap to build on. "
            "Fix or change the URL, then re-run the audit.",
        )
    return card


# --- error shaping ----------------------------------------------------------


@app.exception_handler(store.InvalidTransition)
async def shape_transition(_, exc: store.InvalidTransition) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(store.ResetRefused)
async def shape_reset_refused(_, exc: store.ResetRefused) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def shape_error(_, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
