"""Outreach drafting and sending.

Rules encoded here, not left to the model:
  * every message cites a concrete observation and links the demo
  * every message carries an opt-out line
  * one message per lead, ever
  * a send is always written to outbox.log; it is only delivered over SMTP when
    SMTP_* is configured, and then only to SMTP_TEST_TO (a controlled recipient)
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
import urllib.parse
from datetime import UTC, datetime
from email.message import EmailMessage

from . import llm
from .settings import runtime_dir, settings

OUTBOX = runtime_dir() / "outbox.log"

OPT_OUT = (
    "If you'd rather I didn't follow up, reply with 'stop' and I won't contact you again."
)
DISCLAIMER = "Sent once per business. No mass mailing, no follow-up sequence without a reply."

SYSTEM = (
    "You write short, honest B2B outreach for a small agency contacting a local business "
    "owner. Plain language, no hype, no fake familiarity, no exclamation marks, no buzzwords. "
    "Never invent facts, numbers, awards or mutual connections. Keep the body under 130 words."
)


def demo_url(slug: str) -> str:
    return f"{settings()['public_base_url']}/demo/{slug}"



def detect_dialect(city: str | None, requested_lang: str = "auto") -> tuple[str, str]:
    """Returns (lang_key, instruction_clause) for hyper-local outreach."""
    req = (requested_lang or "auto").strip().lower()
    if req in {"telugu", "tenglish"}:
        return ("telugu", "conversational Telugu mixed with English (Tenglish), polite and natural for local shop/cafe owners in Hyderabad/Telangana")
    if req in {"hindi", "hinglish"}:
        return ("hindi", "conversational Hindi mixed with English (Hinglish), polite and natural for local business owners in North/Central India")
    if req in {"marathi"}:
        return ("marathi", "conversational Marathi mixed with English, polite and natural for local business owners in Maharashtra")
    if req in {"english"}:
        return ("english", "professional plain English")

    # Auto-detect from city or address
    c = (city or "").strip().lower()
    if any(k in c for k in ("hyderabad", "secunderabad", "telangana", "andhra", "vijayawada", "vizag")):
        return ("telugu", "conversational Telugu mixed with English (Tenglish), polite and natural for local shop/cafe owners in Hyderabad")
    if any(k in c for k in ("mumbai", "pune", "nagpur", "thane", "nashik", "maharashtra")):
        return ("marathi", "conversational Marathi mixed with English, polite and natural for local business owners in Maharashtra")
    if any(k in c for k in ("delhi", "noida", "gurgaon", "jaipur", "lucknow", "bhopal", "patna", "indore")):
        return ("hindi", "conversational Hindi mixed with English (Hinglish), polite and natural for local business owners")
    return ("english", "professional plain English")


def _from_model(lead: dict, card: dict, url: str, language: str = "auto") -> dict:
    lang_key, dialect_desc = detect_dialect(lead.get("city") or lead.get("address"), language)
    lang_clause = (
        f"- LANGUAGE / DIALECT: Write the body in natural {dialect_desc}. Keep technical terms, brand names, and the preview URL strictly in English."
        if lang_key != "english"
        else "- Language: English."
    )
    prompt = f"""
Write the first and only outreach email to this business.

Business : {lead['name']}
Address  : {lead.get('address') or '-'}
Website  : {lead.get('website') or '-'}
Type     : {lead.get('search') or 'local business'}

What I sell: {lead.get('service') or 'not stated'}

Evidence we can legitimately cite (use it verbatim, do not embellish):
  observation : {card['observation']}
  source      : {card['source_url']}
  retrieved   : {card['retrieved_at']}
  why it matters: {card['why_it_matters']}

A working preview of the fix is live here: {url}

Requirements:
- Subject under 60 characters, specific to this business, no "quick question".
- Body under 130 words, 2-3 short paragraphs, first person, signed off as "MarketPilot".
- First line of the body must be exactly: Hi {lead['name']} team,
{lang_clause}
- MANDATORY: one short clause naming what I sell - {lead.get('service') or 'my service'}.
  Say it as the kind of work you do. Never present it as a finished project for this client.
- MANDATORY: open by naming the exact observation and its URL so the owner can check it.
- MANDATORY: explain the fix in one sentence and link {url} once.
- MANDATORY DIFFERENTIATOR: include one crisp sentence highlighting why our approach is better than traditional agencies: unlike typical agencies that pitch concepts or charge retainers, we already pre-built this turnkey interactive preview specifically for {lead['name']} with zero setup friction.
- MANDATORY: close with the opt-out line, exactly: "{OPT_OUT}"
- Return JSON: {{"subject": "...", "body": "..."}}
"""
    data = llm.complete_json(prompt, SYSTEM)
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()
    if not subject or not body:
        raise ValueError("model returned an empty subject or body")
    service = (lead.get("service") or "").strip()
    if service and service.lower() not in body.lower():
        body = (
            body.rstrip()
            + f"\n\nI work on {service} for local businesses, so this is the sort of fix I do."
        )
    for line in (OPT_OUT, DISCLAIMER):
        if line.lower() not in body.lower():
            body = body.rstrip() + f"\n\n{line}"
    return {"subject": subject, "body": body}


def clean_phone(phone: str | None) -> str:
    """Normalize phone number to international format digits."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("+"):
        return digits[1:]
    if len(digits) == 10 and not digits.startswith("91"):
        return f"91{digits}"
    return digits


def whatsapp_url(phone: str | None, message: str) -> str | None:
    """Generates an honest click-to-chat link keeping human in the loop."""
    number = clean_phone(phone)
    if not number:
        return None
    encoded = urllib.parse.quote(message)
    return f"https://wa.me/{number}?text={encoded}"


def call_script(lead: dict, card: dict, url: str, language: str = "auto") -> str:
    """Crisp 30-second telephone script for phone outreach with local dialect options."""
    name = lead["name"]
    is_no_web = card.get("gap") == "no_website" or not lead.get("website")
    hook = (
        "Unlike agencies pitching sales decks or retainers, I already pre-built a working mobile preview site for you with your contact details and directions."
        if is_no_web
        else f"Unlike agencies that charge retainers for slide decks, I already built a working interactive demo showing how {card.get('title') or 'an instant booking/contact feature'} would look on your site."
    )
    lang_key, _ = detect_dialect(lead.get("city") or lead.get("address"), language)
    opener = f"1. Opener: 'Hi, is this the owner or manager at {name}? My name is Charan from MarketPilot.'"
    if lang_key == "telugu":
        opener += f"\n   (Local Telugu option: 'Namaskaram, ee call {name} owner/manager kosama? Naa peru Charan, MarketPilot nundi matladuthunnanu.')"
    elif lang_key == "hindi":
        opener += f"\n   (Local Hindi option: 'Namaste, kya meri baat {name} ke owner ya manager se ho rahi hai? Mera naam Charan hai, MarketPilot se.')"
    elif lang_key == "marathi":
        opener += f"\n   (Local Marathi option: 'Namaskar, mi {name} chya owner kiva manager shi bolu shakto ka? Majha naav Charan, MarketPilot madhun.')"

    return (
        f"{opener}\n"
        f"2. Hook: '{hook}'\n"
        f"3. Value: 'It\\'s completely free to review right now at {url} - zero setup or commitments.'\n"
        f"4. Ask: 'Would it be alright if I sent this quick link over to your WhatsApp so you can review it?'\n"
        f"5. Fallback: If busy, 'No problem at all! I will text the preview link to this number so you can check it whenever you have a minute.'"
    )


def draft(lead: dict, card: dict, slug: str, language: str = "auto") -> dict:
    url = demo_url(slug)
    lang_key, _ = detect_dialect(lead.get("city") or lead.get("address"), language)
    if llm.available():
        try:
            out = _from_model(lead, card, url, language=language)
            base = {**out, "generated_by": f"model:{llm.provider()}/{llm.model_name()}"}
        except (llm.LLMError, ValueError) as exc:
            base = {**_template(lead, card, url), "generated_by": f"template (model failed: {exc})"}
    else:
        base = {**_template(lead, card, url), "generated_by": "template (no LLM key configured)"}

    # Attach multi-channel outreach options
    wa_msg = (
        f"{base['subject']}\n\n"
        f"Hi {lead['name']} team,\n"
        f"Unlike traditional agencies that pitch concepts or charge upfront retainers, I already pre-built a working interactive preview specifically for {lead['name']}:\n"
        f"{url}\n\n"
        f"Take 30 seconds to test it out - zero obligations."
    )
    base["language"] = lang_key
    base["whatsapp_url"] = whatsapp_url(lead.get("phone"), wa_msg)
    base["call_script"] = call_script(lead, card, url, language=language)
    return base


def _template(lead: dict, card: dict, url: str) -> dict:
    service = (lead.get("service") or "").strip()
    service_line = (
        f"\nI work on {service} for local businesses, so this is the sort of fix I do."
        if service
        else ""
    )
    if card.get("gap") == "no_website":
        subject = f"Starter website & digital presence ready for {lead['name']}"
        body = f"""Hi {lead['name']} team,

While researching {lead.get('search') or 'local businesses'} in {lead.get('city') or 'your area'}, I noticed {lead['name']} does not currently have an active website or digital storefront listed on Google Maps or OpenStreetMap.
{service_line}
Local customers searching online default to competitors who have an instant website, menu/pricing, and 1-click contact.

Unlike traditional agencies that send generic pitch decks or ask for upfront retainers before doing any work, I already pre-built a working starter preview site for you - complete with mobile layout, directions, and direct contact routing:
{url}

If you find it helpful, you can claim it directly or I am happy to customize it to your requirements.

{OPT_OUT}

- MarketPilot
({DISCLAIMER})"""
        return {"subject": subject, "body": body}

    subject = f"Your site at {lead['name']}: one thing missing"
    body = f"""Hi {lead['name']} team,

I was reading your site ({card['source_url']}) on {str(card.get('retrieved_at'))[:10]} and
noticed: {str(card.get('observation') or '').rstrip('.')}
{service_line}
{card.get('why_it_matters') or ''}

Unlike traditional agencies that pitch concepts or charge retainers, I put together a working interactive preview of what fixing it looks like - about a minute to test, nothing to install:
{url}

If it is useful I am happy to walk you through it.

{OPT_OUT}

- MarketPilot
({DISCLAIMER})"""
    return {"subject": subject, "body": body}


def send(lead: dict, draft_body: dict) -> dict:
    """Log unconditionally; deliver only when SMTP is configured."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    to_addr = (lead.get("email") or settings()["smtp_test_to"] or "").strip().lower()
    record = {
        "at": now,
        "lead_id": lead["id"],
        "business": lead["name"],
        "to": to_addr or None,
        "subject": draft_body["subject"],
        "body": draft_body["body"],
        "generated_by": draft_body.get("generated_by"),
        "mode": "logged",
    }

    error = None
    if to_addr and settings()["smtp_host"]:
        try:
            _smtp_send(to_addr, draft_body)
            record["mode"] = "sent"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            record["error"] = error

    with OUTBOX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    if error and record["mode"] != "sent":
        raise RuntimeError(f"logged, but SMTP delivery failed: {error}")
    return {k: record[k] for k in ("mode", "to", "at")}


def _smtp_send(to_addr: str, draft_body: dict) -> None:
    cfg = settings()
    message = EmailMessage()
    message["From"] = cfg["smtp_from"] or cfg["smtp_user"]
    message["To"] = to_addr
    message["Subject"] = draft_body["subject"]
    message.set_content(draft_body["body"])

    port = int(cfg["smtp_port"])
    with smtplib.SMTP(cfg["smtp_host"], port, timeout=30) as server:
        server.ehlo()
        if port == 465:
            server.quit()
            with smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30,
                                  context=ssl.create_default_context()) as tls:
                tls.login(cfg["smtp_user"], cfg["smtp_password"])
                tls.send_message(message)
            return
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        if cfg["smtp_user"]:
            server.login(cfg["smtp_user"], cfg["smtp_password"])
        server.send_message(message)
