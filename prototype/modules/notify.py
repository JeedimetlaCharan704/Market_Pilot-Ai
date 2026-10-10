"""Telegram alerts.

Fires on things a human needs to decide.
Never raises when unconfigured or when delivery fails.
"""

from __future__ import annotations

import httpx

from .settings import settings

API = "https://api.telegram.org/bot{token}/sendMessage"

URGENCY = {
    "interested": "YES - a human should reply",
    "question": "needs an answer",
    "opt_out": "opted out - do not contact again",
    "not_interested": "closed",
    "unclear": "could not classify",
}


def configured() -> bool:
    cfg = settings()
    return bool(cfg["telegram_bot_token"] and cfg["telegram_chat_id"])


def send(text: str, *, silent: bool = False) -> bool:
    """Deliver one message. Returns False (never raises) when unconfigured or refused."""
    cfg = settings()
    if not (cfg["telegram_bot_token"] and cfg["telegram_chat_id"]):
        return False
    try:
        response = httpx.post(
            API.format(token=cfg["telegram_bot_token"]),
            json={
                "chat_id": cfg["telegram_chat_id"],
                "text": text[:4000],
                "disable_notification": silent,
            },
            timeout=20,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def notify_reply(lead: dict, classification: str, body: str) -> bool:
    action = URGENCY.get(classification, "could not classify")
    text = (
        f"MarketPilot reply\n"
        f"{lead.get('name', '?')} <{lead.get('email') or 'unknown'}>\n"
        f"classified as: {classification}\n"
        f"-> {action}\n\n"
        f"{(body or '').strip()[:600]}"
    )
    return send(text, silent=classification in {"opt_out", "not_interested"})


def notify_send(lead: dict, mode: str, to: str | None) -> bool:
    text = (
        f"MarketPilot sent\n{lead.get('name', '?')} -> {to or 'no recipient'}\n"
        f"mode: {mode}"
    )
    return send(text, silent=True)


def notify_human_decision(lead: dict, trigger: str, body: str, suggested_reply: str | None = None) -> bool:
    """Alert operator when human decision/negotiation is needed."""
    text = (
        f"🚨 MarketPilot: Action Needed for Lead\n"
        f"Business: {lead.get('name', 'Unknown')}\n"
        f"Contact: {lead.get('email') or lead.get('phone') or 'Not listed'}\n"
        f"Reason: {trigger}\n\n"
        f"Inbound Message:\n{(body or '').strip()[:600]}\n\n"
        f"💡 Suggested Action:\n{suggested_reply or 'Open lead studio in dashboard and follow up directly.'}"
    )
    return send(text, silent=False)

