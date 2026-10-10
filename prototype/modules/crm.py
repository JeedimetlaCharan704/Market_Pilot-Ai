"""CRM Integration Module for MarketPilot.

Synchronizes leads, audit evidence, and outreach status to:
1. Webhooks (Zapier, Make, n8n, Slack, or custom CRM).
2. Notion Database API.
3. Local SQLite persistence.
Never raises on failure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from . import store
from .settings import settings


def status() -> dict[str, Any]:
    cfg = settings()
    has_webhook = bool(cfg.get("crm_webhook_url"))
    has_notion = bool(cfg.get("notion_api_key") and cfg.get("notion_database_id"))
    return {
        "webhook_configured": has_webhook,
        "notion_configured": has_notion,
        "active": has_webhook or has_notion,
        "mode": "Notion API" if has_notion else ("Webhook" if has_webhook else "Built-in SQLite CRM"),
    }


def sync_lead(lead: dict, event: str = "lead_updated") -> dict[str, Any]:
    """Sync one lead to configured CRM destinations."""
    cfg = settings()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    demo_url = f"{cfg.get('public_base_url', '')}/demo/{lead.get('demo_slug')}" if lead.get("demo_slug") else None

    payload = {
        "event": event,
        "synced_at": now,
        "lead_id": lead.get("id"),
        "name": lead.get("name"),
        "website": lead.get("website"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "address": lead.get("address"),
        "city": lead.get("city"),
        "status": lead.get("status"),
        "status_reason": lead.get("status_reason"),
        "service": lead.get("service"),
        "demo_url": demo_url,
    }

    result: dict[str, Any] = {
        "ok": True,
        "lead_id": lead.get("id"),
        "synced_at": now,
        "destinations": {"local": True},
    }

    # 1. Custom Webhook
    webhook_url = cfg.get("crm_webhook_url")
    if webhook_url:
        try:
            resp = httpx.post(webhook_url, json=payload, timeout=6.0)
            result["destinations"]["webhook"] = resp.status_code in {200, 201, 202, 204}
        except Exception as exc:
            result["destinations"]["webhook"] = False
            result["webhook_error"] = str(exc)

    # 2. Notion API
    notion_key = cfg.get("notion_api_key")
    db_id = cfg.get("notion_database_id")
    if notion_key and db_id:
        try:
            headers = {
                "Authorization": f"Bearer {notion_key}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            }
            notion_page = {
                "parent": {"database_id": db_id},
                "properties": {
                    "Name": {"title": [{"text": {"content": (lead.get("name") or "Unknown")[:100]}}]},
                    "Status": {"select": {"name": (lead.get("status") or "DISCOVERED")[:50]}},
                },
            }
            if lead.get("phone"):
                notion_page["properties"]["Phone"] = {"phone_number": lead["phone"][:30]}
            if lead.get("email"):
                notion_page["properties"]["Email"] = {"email": lead["email"][:80]}
            if lead.get("city"):
                notion_page["properties"]["City"] = {"rich_text": [{"text": {"content": lead["city"][:100]}}]}
            if demo_url:
                notion_page["properties"]["Demo URL"] = {"url": demo_url[:2000]}
            resp = httpx.post("https://api.notion.com/v1/pages", headers=headers, json=notion_page, timeout=8.0)
            result["destinations"]["notion"] = resp.status_code in {200, 201}
        except Exception as exc:
            result["destinations"]["notion"] = False
            result["notion_error"] = str(exc)

    # Update SQLite timestamp
    if lead.get("id"):
        try:
            store.save(lead["id"], crm_synced_at=now)
        except Exception:
            pass

    return result
