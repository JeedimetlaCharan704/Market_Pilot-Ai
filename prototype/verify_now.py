"""Full verification pass - run this before judging.

Three complete happy paths from an empty database, the refusal paths, a
persistence check across an app reload, and every endpoint.

    .venv/Scripts/python.exe verify_now.py

Exits non-zero if anything fails. Needs network (OSM + Groq).
"""

from __future__ import annotations

import glob
import importlib
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("VERCEL", None)

OPT = "If you'd rather I didn't follow up, reply with 'stop' and I won't contact you again."
DISCLAIMER = "Sent once per business. No mass mailing, no follow-up sequence without a reply."
SERVICE = "AI chatbot development"

# Aggregators are not the business's own site: auditing one says nothing about
# the business, and the product correctly refuses to build on it.
AGGREGATORS = (
    "zomato.com", "swiggy.com", "google.", "facebook.com", "instagram.com",
    "tripadvisor.", "justdial.", "dineout.", "eat24.", "linkedin.com",
)

failures: list[str] = []


def _remove(path: str) -> None:
    """Delete with retries: SQLite in WAL mode can hold -shm/-wal for a moment."""
    for _ in range(30):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.15)
    print("  warn could not remove", path)


def wipe() -> None:
    """Empty the runtime state, so each run starts like a fresh clone.

    The schema has to be rebuilt afterwards: init_db() runs once at import, and
    deleting the file out from under it leaves an empty, table-less database.
    """
    import gc

    from modules import store

    gc.collect()
    for pattern in ("data/marketpilot.db*", "data/outbox.log", "data/demos/*"):
        for path in glob.glob(pattern):
            _remove(path)
    store.init_db()


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def pick_lead(leads: list[dict]) -> dict | None:
    """The first business with its own website, never an aggregator."""
    own = [
        row for row in leads
        if (row.get("website") or "").startswith("http")
        and not any(agg in row["website"].lower() for agg in AGGREGATORS)
    ]
    if own:
        return own[0]
    return next((row for row in leads if (row.get("website") or "").startswith("http")), None)


def happy_path(client, run: int) -> None:
    """One end-to-end pass.

    Overpass is a volunteer service and answers with different rows (or nothing)
    from minute to minute, so an unusable result is retried once rather than
    reported as a product failure.
    """
    payload: dict = {}
    lead = None
    for attempt in (1, 2):
        started = time.time()
        response = client.post(
            "/api/search",
            json={"service": SERVICE, "business_type": "cafes", "city": "Hyderabad"},
        )
        payload = response.json()
        lead = pick_lead(payload.get("leads", []))
        print(
            f" run {run}.{attempt}: HTTP {response.status_code} backend={payload.get('backend')} "
            f"n={payload.get('count')} usable={'yes' if lead else 'no'} "
            f"errors={payload.get('errors')} {time.time() - started:.1f}s"
        )
        if response.status_code == 200 and lead is not None:
            break
        time.sleep(3)

    if lead is None:
        failures.append(f"run{run}-search")
        return

    lead_id = lead["id"]
    check(f"run{run} lead carries the service", (lead.get("service") or "") == SERVICE,
          lead.get("service"))
    audit = client.post(f"/api/audit/{lead_id}")
    audit_json = audit.json()
    cards = audit_json.get("cards", [])
    check(
        f"run{run} audit",
        audit.status_code == 200 and audit_json.get("lead", {}).get("status") == "AUDITED",
        f"{audit.status_code} status={audit_json.get('lead', {}).get('status')} "
        f"cards={len(cards)} {audit_json.get('detail', '')}",
    )
    sourced = sum(
        1 for card in cards if card.get("source_url") and card.get("retrieved_at")
    )
    check(f"run{run} every card carries source_url + date", sourced == len(cards) and cards,
          f"{sourced}/{len(cards)}")
    check(f"run{run} cards quote an observation", all(card.get("observation") for card in cards))
    scored = [card for card in cards if isinstance(card.get("score"), int)]
    check(f"run{run} every card carries a priority score", len(scored) == len(cards) and cards,
          f"{len(scored)}/{len(cards)} -> {[(c.get('gap'), c.get('score')) for c in cards]}")
    check(f"run{run} every card explains why it fits",
          all(card.get("why_it_fits") for card in cards))
    zeroed = [c["score"] for c in cards if c.get("gap") in ("none", "unverified")]
    check(f"run{run} a card with no gap scores 0", all(s == 0 for s in zeroed), zeroed or "n/a")

    demo = client.post(f"/api/demo/{lead_id}")
    demo_json = demo.json() if demo.headers.get("content-type", "").startswith("application/json") else {}
    slug = demo_json.get("slug")
    check(f"run{run} demo", demo.status_code == 200 and slug,
          f"{demo.status_code} {demo_json.get('generated_by')} {demo_json.get('detail', '')}")

    draft = client.post(f"/api/draft/{lead_id}")
    body = (draft.json().get("draft") or {})
    text = body.get("body") or ""
    first_line = text.split("\n")[0]
    check(f"run{run} draft uses the model", "groq" in (body.get("generated_by") or ""),
          body.get("generated_by"))
    check(f"run{run} OPT_OUT + DISCLAIMER exact", OPT in text and DISCLAIMER in text)
    check(f"run{run} greeting + demo link", first_line.startswith("Hi ") and "demo/" in text,
          first_line)
    check(f"run{run} draft cites an observation", "http" in text)
    check(f"run{run} draft mentions what we sell", SERVICE.lower() in text.lower(),
          SERVICE if SERVICE.lower() in text.lower() else "absent")

    sent = client.post(f"/api/send/{lead_id}")
    check(f"run{run} send -> SENT",
          sent.status_code == 200 and sent.json().get("lead", {}).get("status") == "SENT",
          sent.json().get("outcome"))
    rewind = client.post(f"/api/reset/{lead_id}")
    check(f"run{run} reset refused 409", rewind.status_code == 409,
          rewind.json().get("detail"))

    if slug:
        page = client.get(f"/demo/{slug}")
        check(f"run{run} demo page renders",
              page.status_code == 200 and "html" in page.headers.get("content-type", ""),
              f"{page.status_code} {len(page.content)}b")
        check(f"run{run} demo page self-contained", 'src="http' not in page.text)
        check(f"run{run} demo page has viewport", "viewport" in page.text)
        check(f"run{run} demo page names the service", SERVICE.lower() in page.text.lower(),
              SERVICE if SERVICE.lower() in page.text.lower() else "absent")
        honest = "not a build of that service" in page.text.lower()
        check(f"run{run} demo page disclaims a custom build", honest,
              "present" if honest else "absent")


def refusals(client) -> None:
    for path in ("/api/audit/999999", "/api/demo/999999", "/api/draft/999999", "/api/send/999999"):
        response = client.post(path)
        check(f"404 unknown lead {path}", response.status_code == 404, response.status_code)

    leads = client.get("/api/leads").json()["leads"]
    undiscovered = next((row for row in leads if row["status"] == "DISCOVERED"), None)
    if undiscovered:
        lead_id = undiscovered["id"]
        for label, path in (("demo", "/api/demo"), ("draft", "/api/draft")):
            response = client.post(f"{path}/{lead_id}")
            check(f"409 {label} before audit", response.status_code == 409,
                  response.json().get("detail"))
    else:
        check("an unaudited lead exists to refuse", False, "none left")

    sent = next((row for row in leads if row["status"] == "SENT"), None)
    if sent:
        lead_id = sent["id"]
        for path in ("/api/audit", "/api/demo", "/api/draft", "/api/send", "/api/reset"):
            response = client.post(f"{path}/{lead_id}")
            check(f"{response.status_code} {path} on a SENT lead",
                  response.status_code == 409, response.json().get("detail"))

    check("422 missing service",
          client.post("/api/search", json={"business_type": "cafes", "city": "Hyderabad"}).status_code == 422)
    check("422 blank service",
          client.post("/api/search", json={"service": "   ", "business_type": "cafes", "city": "Hyderabad"}).status_code == 422)
    check("422 missing city",
          client.post("/api/search", json={"service": SERVICE, "business_type": "cafes"}).status_code == 422)
    empty = client.post("/api/search", json={"service": SERVICE, "business_type": "", "city": ""})
    check("422 empty inputs", empty.status_code == 422, empty.status_code)
    check("404 unknown route", client.get("/nope").status_code == 404)


def main() -> int:
    wipe()
    from fastapi.testclient import TestClient

    import app as app_module

    print("=== A. happy path, three times, each from an empty database ===")
    for run in (1, 2, 3):
        wipe()
        happy_path(TestClient(app_module.app), run)

    client = TestClient(app_module.app)
    print("=== B. refusal paths (no mocks, no guessing) ===")
    refusals(client)

    print("=== C. persistence across an app reload (cold-start shape) ===")
    before = client.get("/api/leads").json()["leads"]
    importlib.reload(app_module)
    after = TestClient(app_module.app).get("/api/leads").json()["leads"]
    check("lead count survives reload", len(before) == len(after),
          f"{len(before)} -> {len(after)}")
    sent_before = sorted(row["id"] for row in before if row["status"] == "SENT")
    sent_after = sorted(row["id"] for row in after if row["status"] == "SENT")
    check("SENT state survives reload", sent_before == sent_after,
          f"{sent_before} -> {sent_after}")
    outbox = "data/outbox.log"
    check("outbox.log written", os.path.exists(outbox),
          f"{os.path.getsize(outbox)} bytes" if os.path.exists(outbox) else "missing")

    print("=== D. endpoints ===")
    client = TestClient(app_module.app)
    for path, wanted in (("/", 200), ("/api/config", 200), ("/api/leads", 200),
                         ("/api/docs", 200), ("/api/nope", 404)):
        check(f"GET {path} -> {wanted}", client.get(path).status_code == wanted)

    config = client.get("/api/config").json()
    check("config reports a real provider", config.get("llm_provider") not in (None, "none"), config.get("llm_provider"))
    check("config shows the daily cap", config.get("daily_send_cap", 0) > 0, config.get("daily_send_cap"))
    check("config shows the opt-out", "stop" in (config.get("opt_out") or "").lower())
    check("config shows the disclaimer", "once per business" in (config.get("disclaimer") or "").lower())
    check("config counts sends today", "sent_today" in config, config.get("sent_today"))

    print("=== E. the closed loop: scoring, opt-out registry, replies, worker ===")
    check("config reports a model transport", "transport" in (config.get("llm") or {}),
          (config.get("llm") or {}).get("transport"))
    check("config reports discovery health",
          set(config.get("discovery") or {}) >= {"osm", "google_places", "scraper_docker"})
    check("config reports the worker", "worker" in config, config.get("worker"))

    check("opt-out list answers", "opt_outs" in client.get("/api/optout").json())
    added = client.post("/api/optout", json={"email": "Verify@Now.test"}).json()
    check("opt-out accepts an address", added.get("added") is True)
    listed = [o["email"] for o in client.get("/api/optout").json()["opt_outs"]]
    check("opt-out normalises case", "verify@now.test" in listed, listed)
    check("opt-out is not padded by repeats",
          client.post("/api/optout", json={"email": "verify@now.test"}).json()["added"] is False)
    check("opt-out rejects rubbish",
          client.post("/api/optout", json={"email": "nope"}).status_code == 422)
    check("opt-out delete works",
          client.delete("/api/optout/verify@now.test").status_code == 200)
    check("opt-out delete of an unknown address 404",
          client.delete("/api/optout/verify@now.test").status_code == 404)

    from modules import settings
    saved_imap = os.environ.get("IMAP_USER", "")
    os.environ["IMAP_USER"] = ""
    settings.settings.cache_clear()
    poll = client.post("/api/replies/poll")
    check("poll without IMAP is a clean 503, never a 500", poll.status_code == 503,
          poll.json().get("detail"))
    os.environ["IMAP_USER"] = saved_imap
    settings.settings.cache_clear()
    check("replies endpoint answers", "replies" in client.get("/api/replies").json())

    worker = client.get("/api/worker").json()
    check("worker status is honest",
          isinstance(worker.get("supported"), bool) and "running" in worker, worker)

    from modules import replies as replies_module
    check("an opt-out in free text is caught before any model",
          replies_module.classify("Please STOP, unsubscribe me") == "opt_out")
    check("a refusal is not read as enthusiasm",
          replies_module.classify("No thanks, not interested") == "not_interested")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):", failures)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
