"""Evidence cards: every claim tied to a URL, an observation and a date.

Rule enforced here - you cannot get a card without a concrete observation.
If the site could not be fetched, exactly one card is produced saying so, at
confidence `low`, and no problem is claimed about the business.

Each card also carries a priority score and a plain-English "why it fits".
Both are computed from what we observed - never from a model - so the ranking
is reproducible and can be argued with. Weights come from config.yaml.
"""

from __future__ import annotations

from .settings import scoring_weights, settings

GAP_LABELS = {
    "booking": "No online booking",
    "contact": "No contact form",
    "chat": "No WhatsApp or chat widget",
    "mobile": "Not mobile-friendly",
    "no_website": "Zero website / Missing digital storefront",
}

REVENUE_IMPACT = {"no_website": 0.98, "booking": 0.90, "mobile": 0.80, "chat": 0.60, "contact": 0.40, "none": 0.0, "unverified": 0.0}
FIX_EFFORT = {"no_website": 0.85, "booking": 0.70, "contact": 0.90, "chat": 0.85, "mobile": 0.45, "none": 0.0, "unverified": 0.0}
CONFIDENCE_STRENGTH = {"high": 0.90, "medium": 0.60, "low": 0.30}

SERVICE_HINTS = {
    "no_website": {"web", "website", "site", "landing", "online", "presence", "design", "digital", "developer", "marketing", "call", "voice", "agent"},
    "chat": {"chat", "chatbot", "bot", "whatsapp", "assistant", "messaging", "ai", "call", "voice"},
    "booking": {"booking", "appointment", "schedule", "reservation", "calendar", "slot"},
    "mobile": {"mobile", "responsive", "web", "website", "site", "ui", "ux", "design"},
    "contact": {"contact", "form", "crm", "lead", "enquiry", "inquiry"},
}

FIT_PHRASE = {
    "no_website": "launching a starter digital presence delivers immediate conversion value before competitors reach them",
    "booking": "a booking flow is the most direct way to turn that into revenue",
    "chat": "a chat widget is the most direct way to turn that into conversations",
    "mobile": "a mobile rebuild is the most direct way to turn that into traffic",
    "contact": "a contact form is the most direct way to turn that into enquiries",
}


def _service_fit(gap: str, service: str) -> float:
    """0..1. Deterministic keyword overlap - no model, so it is reproducible."""
    service = (service or "").strip().lower()
    if not service or gap not in SERVICE_HINTS:
        return 0.5
    words = {w for w in service.replace("/", " ").split() if len(w) > 2}
    hints = SERVICE_HINTS[gap]
    if words & hints:
        return 1.0
    if any(w in service for w in ("marketing", "agency", "local business", "small business")):
        return 0.7
    return 0.5


def score_card(gap: str, confidence: str, pages_read: int, service: str) -> dict:
    """The priority score and its reasoning, both deterministic."""
    weights = scoring_weights()

    if gap in {"none", "unverified"}:
        return {
            "score": 0,
            "score_parts": {k: 0.0 for k in weights},
            "why_it_fits": (
                "No gap was evidenced, so there is nothing to score and nothing to pitch."
            ),
        }

    evidence_strength = CONFIDENCE_STRENGTH.get(confidence, 0.3)
    evidence_strength = min(1.0, evidence_strength + min(pages_read, 5) * 0.02)

    parts = {
        "revenue_impact": REVENUE_IMPACT.get(gap, 0.0),
        "fix_effort": FIX_EFFORT.get(gap, 0.0),
        "evidence_strength": round(evidence_strength, 3),
        "service_fit": _service_fit(gap, service),
    }
    total = round(sum(parts[k] * weights[k] for k in weights) * 100)

    if parts["service_fit"] >= 1.0:
        why = (
            f"You sell {service or 'this kind of work'}, and {FIT_PHRASE.get(gap, 'this gap')}"
            " - it is the closest match between what you offer and what they are missing."
        )
    else:
        why = (
            f"{GAP_LABELS.get(gap, gap)} on a site we actually read "
            f"({confidence} confidence). A reasonable pitch for a generalist, though not a "
            "specialist match for what you said you sell."
        )
    return {"score": total, "score_parts": parts, "why_it_fits": why}


def _pages_read(audit: dict) -> str:
    urls = [p["url"] for p in audit.get("pages_probed", []) if p.get("error") is None]
    return ", ".join(_short(u, audit.get("url", "")) for u in urls)


def _short(url: str, base: str) -> str:
    if url.rstrip("/") == base.rstrip("/"):
        return "/"
    return "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else url


def synthesize_creative_idea(gap: str, business: dict) -> str:
    """Turn a technical gap into a creative, business-specific concept."""
    kind = (business.get("search") or "").lower()

    if gap == "no_website":
        if any(w in kind for w in ("cafe", "coffee", "restaurant", "bakery", "food", "bar", "pub")):
            return "Instant Digital Cafe Storefront & QR Menu (with mobile-friendly layout, click-to-call, and directions)"
        if any(w in kind for w in ("real estate", "realtor", "property", "developer", "builder")):
            return "Turnkey Property Showcase & Inbound Buyer Lead Triage Portal"
        if any(w in kind for w in ("salon", "spa", "beauty", "hair")):
            return "Modern Salon Visual Storefront & Service Menu (with direct WhatsApp booking)"
        if any(w in kind for w in ("gym", "fitness", "yoga")):
            return "Turnkey Fitness Studio Hub & Trial Session Pass Landing Page"
        return "Complete Turnkey Starter Website & Digital Storefront (ready to claim with localized branding, click-to-call, and map)"
    elif gap == "booking":
        if any(w in kind for w in ("cafe", "coffee", "restaurant", "bakery", "food", "bar", "pub")):
            return "AI Table Concierge & Instant Slot Reservation widget (lets customers reserve seating during rush hours without phone delays)"
        if any(w in kind for w in ("salon", "hair", "barber", "spa", "beauty")):
            return "Interactive Stylist Lookbook & Real-Time Availability Booking Flow (lets clients choose treatments and pick their specialist)"
        if any(w in kind for w in ("gym", "fitness", "yoga", "training")):
            return "Class Schedule Pass & Trial Session Booking Portal (converts casual website traffic into confirmed workout attendees)"
        if any(w in kind for w in ("clinic", "dentist", "doctor", "health")):
            return "Patient Self-Triage & Direct Slot Booking Flow (streamlines check-ins and cuts receptionist telephone overhead)"
        return "Instant Self-Service Slot Reservation Flow (captures buying intent before visitors look up a competitor)"
    elif gap == "chat":
        if any(w in kind for w in ("cafe", "coffee", "restaurant")):
            return "WhatsApp Dining Assistant (answers dietary/vegan questions and routes visitors to reservation links)"
        if any(w in kind for w in ("salon", "spa", "beauty")):
            return "WhatsApp Style Consultant (pre-consultation chat answering pricing and treatment options)"
        return "1-Click Direct WhatsApp Messaging Widget with pre-filled context"
    elif gap == "contact":
        if any(w in kind for w in ("cafe", "restaurant")):
            return "Private Events & Group Catering Enquiry System (captures headcount, date, and dietary requirements)"
        return "Frictionless 3-Field Lead Capture Form with automated email acknowledgment"
    elif gap == "mobile":
        return "Mobile-First Touch Layout (thumb-friendly buttons, instant loading, and zero pinch-to-zoom)"
    return "Optimized Conversion Landing Page"


def build_cards(business: dict, audit: dict) -> list[dict]:
    stamp = {
        "business": business["name"],
        "retrieved_at": audit.get("fetched_at"),
        "audited_url": audit.get("url"),
    }
    service = business.get("service") or ""
    pages_read = audit.get("pages_read", 0) or 0

    if not audit.get("fetch_ok"):
        card = {
            **stamp,
            "gap": "unverified",
            "title": "Site not verified - no claim made",
            "creative_concept": "Verify domain accessibility before proposing enhancements",
            "source_url": audit.get("url"),
            "observation": (
                f"We could not read this site ({audit.get('reason')}). "
                "No audit conclusion is drawn from it."
            ),
            "confidence": "low",
            "why_it_matters": (
                "Reporting a gap we did not observe would be a guess. "
                "Re-run the audit once the site is reachable."
            ),
        }
        return [{**card, **score_card("unverified", "low", pages_read, service)}]

    scope = _pages_read(audit)
    cards = []
    for gap in audit.get("gaps", []):
        finding = (audit.get("observations") or {}).get(gap, {})
        if finding.get("found"):
            continue
        card = {
            **stamp,
            "gap": gap,
            "title": GAP_LABELS.get(gap, gap),
            "creative_concept": synthesize_creative_idea(gap, business),
            "source_url": audit.get("url"),
            "observation": f"{finding.get('observation', 'Not found.')} Pages read: {scope}.",
            "confidence": audit.get("confidence", "low"),
            "why_it_matters": finding.get("why_it_matters", ""),
        }
        cards.append({**card, **score_card(gap, card["confidence"], pages_read, service)})

    if not cards:
        card = {
            **stamp,
            "gap": "none",
            "title": "No gap found in the four checks",
            "source_url": audit.get("url"),
            "observation": (
                f"Booking, contact form, chat widget and viewport tag were all present "
                f"across {audit.get('pages_read', 0)} pages: {scope}."
            ),
            "confidence": audit.get("confidence", "medium"),
            "why_it_matters": (
                "We only pitch what we can evidence. If another angle is wanted, "
                "audit a different site from this search."
            ),
        }
        cards.append({**card, **score_card("none", card["confidence"], pages_read, service)})
    return cards


def primary(cards: list[dict]) -> dict | None:
    real = [c for c in cards if c["gap"] not in {"none", "unverified"}]
    if real:
        real.sort(key=lambda c: (-int(c.get("score", 0)), c["gap"]))
        return real[0]
    return cards[0] if cards else None


def source_is_fixture() -> bool:
    return settings()["allow_fallback"] == "1"
