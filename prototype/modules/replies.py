"""Reply handling: poll IMAP, classify, record, optionally answer.

Rules:
  * opt-out is recorded permanently and blocks every future send
  * auto-reply is sent only for `interested`, and only once per lead
  * reply that cannot be classified is reported, never answered
  * negative-first heuristic: "not interested" contains "interested"
"""

from __future__ import annotations

import email as email_lib
import imaplib
import re
from datetime import UTC, datetime

from . import llm, notify, store
from .settings import settings

SYSTEM = (
    "You classify one inbound B2B reply. Return JSON with exactly two keys: "
    '"classification" (one of interested, question, not_interested, opt_out, unclear) '
    'and "summary" (under 20 words). interested = wants to talk or asks for a call. '
    "question = asks something specific. opt_out = says stop, unsubscribe, do not contact, "
    "or Hindi/Hinglish 'nahi chahiye', 'mat bhejo', 'band karo'. "
    "not_interested = a clear no, refusal, or polite decline (e.g. 'not interested', 'abhi nahi', 'baad me', 'no thanks'). "
    "unclear = anything else. "
    "IMPORTANT: If someone says 'abhi nahi chahiye', 'baad me baat karenge', or 'no thanks', this is not_interested or opt_out, NEVER interested!"
)

OPT_OUT_RE = re.compile(
    r"\b(stop|unsubscribe|remove me|do not contact|don'?t contact|no more emails|nahi chahiye|mat bhejo|band karo|don'?t email)\b", re.I
)

AUTO_REPLY_BODY = (
    "Thanks for coming back to me. I'd be glad to talk it through - "
    "what time suits you? I'll follow up with a couple of slots.\n\n- MarketPilot"
)


def configured() -> bool:
    cfg = settings()
    return bool(cfg["imap_user"] and cfg["imap_password"])


def classify(text: str) -> str:
    """One of: interested, question, not_interested, opt_out, unclear."""
    text = (text or "").strip()
    if not text:
        return "unclear"
    if OPT_OUT_RE.search(text):
        return "opt_out"
    if llm.available():
        try:
            data = llm.complete_json(
                f"Classify this reply:\n\n{text[:2000]}", SYSTEM, retries=0
            )
            value = str(data.get("classification", "")).strip().lower()
            if value in {"interested", "question", "not_interested", "opt_out", "unclear"}:
                return value
        except (llm.LLMError, ValueError):
            pass
    return _heuristic(text)


def _heuristic(text: str) -> str:
    """Keyword fallback testing negatives first."""
    low = text.lower()
    if any(w in low for w in (
        "no thanks", "not interested", "no longer", "not for us", "pass on",
        "nahi chahiye", "abhi nahi", "baad me", "mat bhejo", "don't need", "dont need"
    )):
        return "not_interested"
    if any(w in low for w in ("yes", "call me", "interested", "let's talk", "sounds good", "demo")):
        return "interested"
    if "?" in text or any(w in low for w in ("how much", "what does", "can you", "could you", "pricing", "cost")):
        return "question"
    return "unclear"


def poll() -> dict:
    """Fetch unread replies, classify each, record and act. Never raises."""
    if not configured():
        return {"ok": False, "reason": "IMAP_USER / IMAP_PASSWORD not set", "processed": []}

    try:
        raw = _fetch_unseen()
    except (imaplib.IMAP4.error, OSError) as exc:
        return {"ok": False, "reason": f"IMAP error: {exc}", "processed": []}

    processed = []
    for message in raw:
        try:
            processed.append(_handle(message))
        except Exception as exc:
            processed.append({"error": f"{type(exc).__name__}: {exc}"})
    store.record_poll(datetime.now(UTC).isoformat(timespec="seconds"), len(processed))
    return {"ok": True, "processed": processed}


def _fetch_unseen() -> list[tuple[str, str]]:
    cfg = settings()
    port = int(cfg["imap_port"])
    newest = 10
    with imaplib.IMAP4_SSL(cfg["imap_host"], port, timeout=30) as client:
        client.login(cfg["imap_user"], cfg["imap_password"])
        client.select("INBOX")
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            return []
        nums = (data[0] or b"").split()
        out = []
        for num in reversed(nums[-newest:]):
            status, msg = client.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not msg or msg[0] is None:
                continue
            out.append((num.decode(), msg[0][1].decode(errors="replace")))
        return out


def _handle(raw: str | tuple[str, str]) -> dict:
    if isinstance(raw, tuple):
        raw = raw[1]
    message = email_lib.message_from_string(raw)
    sender = email_lib.utils.parseaddr(message.get("From", ""))[1].strip().lower()
    subject = message.get("Subject", "")
    body = _body(message)
    text = f"{subject}\n\n{body}"

    classification = classify(text)
    lead = store.get_lead_by_email(sender) if sender else None
    if not lead and (subject or body):
        clean_subj = re.sub(r"^(re|fwd|fw):\s*", "", subject, flags=re.I).strip()
        lead = store.find_lead_by_name(clean_subj)
        if not lead:
            all_leads = store.list_leads()[:30]
            for cand in all_leads:
                cand_name = (cand.get("name") or "").strip().lower()
                if cand_name and len(cand_name) >= 3 and (cand_name in text.lower()):
                    lead = cand
                    break

    record = {
        "from": sender,
        "subject": subject[:120],
        "classification": classification,
        "lead_id": lead["id"] if lead else None,
        "business": lead["name"] if lead else None,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    if classification == "opt_out":
        store.add_opt_out(sender or "unknown", lead["id"] if lead else None)
        record["opt_out_recorded"] = True
        if (lead and lead["status"] in store.STATUS_FLOW.get(lead["status"], set())) or (lead and "OPTED_OUT" in store.STATUS_FLOW.get(lead["status"], set())):
            try:
                store.set_status(lead["id"], "OPTED_OUT", "Prospect requested opt-out via reply")
            except Exception:
                pass
    elif classification == "interested":
        if lead and "INTERESTED" in store.STATUS_FLOW.get(lead["status"], set()):
            try:
                store.set_status(lead["id"], "INTERESTED", "Prospect expressed interest")
            except Exception:
                pass
        if lead and settings()["auto_reply"] and store.can_auto_reply(lead["id"]):
            store.mark_auto_replied(lead["id"])
            record["auto_replied"] = True
            record["auto_reply_error"] = _reply_to(sender, f"Re: {subject}")
    elif classification == "question" or any(w in text.lower() for w in ("pricing", "how much", "cost", "call me", "phone", "quote")):
        if lead and "NEEDS_HUMAN" in store.STATUS_FLOW.get(lead["status"], set()):
            try:
                store.set_status(lead["id"], "NEEDS_HUMAN", "Inquiry needs operator decision / pricing")
            except Exception:
                pass
        if lead and notify.configured():
            notify.notify_human_decision(
                lead,
                trigger=f"Inbound inquiry: {classification}",
                body=body,
                suggested_reply=f"Offer consultation slot or clarify pricing for {lead.get('service') or 'service'}",
            )
    elif lead and "REPLIED" in store.STATUS_FLOW.get(lead["status"], set()):
        try:
            store.set_status(lead["id"], "REPLIED", f"Reply received ({classification})")
        except Exception:
            pass

    if lead and classification in {"interested", "question", "opt_out"}:
        notify.notify_reply(lead, classification, body)
    record["notified"] = bool(notify.configured())
    record["id"] = store.record_reply(
        lead["id"] if lead else None,
        sender,
        subject,
        classification,
        bool(record.get("auto_replied")),
    ).get("id")
    return record


def _reply_to(to_addr: str, subject: str) -> str | None:
    import smtplib
    import ssl
    from email.message import EmailMessage

    if not (to_addr and settings()["smtp_host"]):
        return "SMTP not configured"
    cfg = settings()
    message = EmailMessage()
    message["From"] = cfg["smtp_from"] or cfg["smtp_user"]
    message["To"] = to_addr
    message["Subject"] = subject
    message.set_content(AUTO_REPLY_BODY)
    port = int(cfg["smtp_port"])
    try:
        with smtplib.SMTP(cfg["smtp_host"], port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if cfg["smtp_user"]:
                server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.send_message(message)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _body(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(errors="replace")
        return ""
    payload = message.get_payload(decode=True)
    return payload.decode(errors="replace") if payload else str(message.get_payload() or "")
