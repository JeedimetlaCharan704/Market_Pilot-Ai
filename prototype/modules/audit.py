"""Website audit: fetch a business's site and detect ONE clear, observable gap.

Nothing here guesses. A gap is only reported when a concrete observation backs it;
if the site cannot be fetched we report that instead and drop confidence to low.

Checklist (all four, always evaluated):
  booking   online booking / appointment link or widget
  contact   a contact form (not just a mailto link)
  chat      WhatsApp / live-chat widget
  mobile    <meta name="viewport"> present
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import net

BOOKING_HINT = re.compile(
    r"\b(book|booking|bookings|reserve|reservation|appointment|appointments|"
    r"schedul[ei]|set.*appointment|slot)\b|calendly|fresha|booksy|treatwell|"
    r"setmore|appointy|squareup\.com/appointments|zoho.*appointment|"
    r"timify|simplybook|acuityscheduling|cliniko|vagaro|mindbody",
    re.I,
)
CHAT_HINT = re.compile(
    r"wa\.me|api\.whatsapp\.com|whatsapp\.com/|whatsapp-widget|"
    r"tidio|tawk\.to|intercom|crisp\.chat|zendesk.*chat|livechat|"
    r"hubspot.*chat|drift\.com|getcrisp|facebook\.com/plugins/messages|"
    r"m\.me/|line\.me|Rasa|landbot|sendgrid.*chat|chat-widget|"
    r"client\.tawk|widget\.co|jivo|olark|purechat|chatra|tidiochat",
    re.I,
)
CONTACT_FORM_HINT = re.compile(r"contact|enquir|inquir|query|message|write.*us|get.*touch", re.I)

CANDIDATE_PATHS = [
    "/",
    "/contact",
    "/contact-us",
    "/contactus",
    "/book",
    "/booking",
    "/book-appointment",
    "/appointments",
    "/reserve",
    "/reservation",
    "/about",
    "/services",
]


class AuditError(RuntimeError):
    pass


def _normalize(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _probe(url: str) -> dict:
    """Fetch one page. Returns status/body/error - never raises."""
    result = {"url": url, "status": None, "html": "", "error": None, "blocked": False}
    try:
        response = net.get(url)
    except net.FetchError as exc:
        result["error"] = str(exc)
        result["blocked"] = "robots.txt" in str(exc)
        return result
    result["status"] = response.status_code
    if response.status_code >= 400:
        result["error"] = f"HTTP {response.status_code}"
        return result
    if "html" not in (response.headers.get("content-type") or "html"):
        result["error"] = f"not HTML ({response.headers.get('content-type')})"
        return result
    result["html"] = response.text[:400_000]
    return result


def _read(base: str, limit: int = 6) -> tuple[list[dict], list[str], dict, list[str]]:
    """Probe the homepage plus a few likely paths. Never raises."""
    pages: list[dict] = []
    titles: list[str] = []
    observed: dict[str, list[dict]] = {"booking": [], "contact": [], "chat": [], "mobile": []}
    discovered_emails: list[str] = []
    email_re = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

    for path in CANDIDATE_PATHS:
        url = base if path == "/" else urljoin(base + "/", path.lstrip("/"))
        if any(p["url"] == url for p in pages):
            continue
        probe = _probe(url)
        html = probe.pop("html")
        pages.append(probe)
        if probe["error"] is None:
            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.string:
                titles.append(soup.title.string.strip()[:120])
            _scan(url, html, observed)

            # Scrape customer contact emails from mailto links and page text
            for a in soup.find_all("a", href=True):
                href = a.get("href", "").strip()
                if href.lower().startswith("mailto:"):
                    em = href.split(":", 1)[1].split("?")[0].strip().lower()
                    if "@" in em and "." in em and not any(em.endswith(bad) for bad in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".js", ".css")):
                        discovered_emails.append(em)

            for em in email_re.findall(html):
                em_low = em.strip().lower()
                if not any(em_low.endswith(bad) for bad in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".js", ".css", ".ico")):
                    if not any(bad in em_low for bad in ("sentry", "wix", "wordpress", "schema.org", "example.com", "bootstrap", "googleapis")):
                        discovered_emails.append(em_low)
        if len(pages) >= limit:
            break
    return pages, titles, observed, discovered_emails


def audit_site(website: str) -> dict:
    """Fetch the homepage plus a few likely paths and score the four checks."""
    base = _normalize(website or "")
    if not base:
        return {
            "url": None,
            "domain": None,
            "fetched_at": _now(),
            "fetch_ok": True,
            "reason": "zero_online_presence",
            "pages_probed": [],
            "pages_read": 0,
            "page_titles": [],
            "gaps": ["no_website"],
            "primary_gap": "no_website",
            "observations": {
                "no_website": {
                    "found": False,
                    "observation": "Zero website found on Google Maps or OpenStreetMap. Operating entirely without an online storefront or landing page.",
                    "evidence_url": None,
                    "why_it_matters": "Local customers searching online default to competitors with an active website, catalog, and instant contact. Building their starter presence immediately captures lost leads.",
                }
            },
            "discovered_email": None,
            "confidence": "high",
        }


    domain = urlparse(base).netloc
    pages, titles, observed, emails = _read(base)
    if not any(page["error"] is None for page in pages):
        pages, titles, observed, emails = _read(base)

    fetched = [p for p in pages if p["error"] is None]
    if not fetched:
        blocked = any(p.get("blocked") for p in pages)
        return {
            "url": base,
            "domain": domain,
            "fetched_at": _now(),
            "fetch_ok": False,
            "reason": "blocked by robots.txt" if blocked else "site could not be fetched",
            "pages_probed": pages,
            "gaps": [],
            "primary_gap": None,
            "observations": {},
            "discovered_email": None,
            "confidence": "low",
        }

    findings = {
        "booking": _finding(
            observed["booking"],
            missing="No booking, reservation or appointment link or widget found on the pages read.",
            why="Visitors who are ready to buy have no way to pick a slot, so they call a competitor or drop off.",
        ),
        "contact": _finding(
            observed["contact"],
            missing="No contact form found - only static details or a mailto link.",
            why="A form is the lowest-friction way for a hesitant visitor to ask one question before buying.",
        ),
        "chat": _finding(
            observed["chat"],
            missing="No WhatsApp or live-chat widget detected.",
            why="Local buyers often prefer a quick message; without one, intent decays before anyone replies.",
        ),
        "mobile": _finding(
            observed["mobile"],
            missing='<meta name="viewport"> is absent, so the site is not mobile-friendly.',
            why="Most local searches open on a phone; without a viewport tag the layout renders zoomed-out.",
        ),
    }

    gaps = [key for key, value in findings.items() if value["found"] is False]
    discovered_email = next(iter(dict.fromkeys(emails)), None)
    return {
        "url": base,
        "domain": domain,
        "fetched_at": _now(),
        "fetch_ok": True,
        "reason": None,
        "pages_probed": pages,
        "pages_read": len(fetched),
        "page_titles": titles[:6],
        "gaps": gaps,
        "primary_gap": gaps[0] if gaps else None,
        "observations": findings,
        "discovered_email": discovered_email,
        "confidence": "high" if len(fetched) >= 3 else "medium",
    }


def _scan(url: str, html: str, observed: dict[str, list[dict]]) -> None:
    soup = BeautifulSoup(html, "html.parser")

    if soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
        observed["mobile"].append(
            {"url": url, "detail": "<meta name=\"viewport\"> present"}
        )

    for tag in soup.find_all(["a", "button"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        href = tag.get("href", "") or ""
        blob = f"{text} {href}"
        if BOOKING_HINT.search(blob):
            detail = f"booking cue {text[:60]!r}"
            if href:
                detail += f" -> {href[:80]}"
            observed["booking"].append({"url": url, "detail": detail})

    for tag in soup.find_all(src=True):
        blob = str(tag.get("src", ""))
        if BOOKING_HINT.search(blob):
            observed["booking"].append({"url": url, "detail": f"booking script {blob[:80]}"})

    for form in soup.find_all("form"):
        action = str(form.get("action", "") or "")
        raw = form.decode() if hasattr(form, "decode") else str(form)
        has_email_input = bool(form.find("input", attrs={"type": "email"}))
        has_textarea = form.find("textarea") is not None
        labelled = bool(CONTACT_FORM_HINT.search(f"{action} {raw[:400]}"))
        if (has_email_input or has_textarea) and labelled:
            observed["contact"].append(
                {"url": url, "detail": f"contact form (action={action[:60] or 'self'})"}
            )

    html_lower = html
    if CHAT_HINT.search(html_lower):
        match = CHAT_HINT.search(html_lower)
        observed["chat"].append(
            {"url": url, "detail": f"chat widget cue {match.group(0)[:60]}"}
        )


def _finding(hits: list[dict], *, missing: str, why: str) -> dict:
    """Turn raw observations into a single claim. `hits` empty => gap claimed."""
    found = bool(hits)
    return {
        "found": found,
        "observation": hits[0]["detail"] if found else missing,
        "evidence_url": hits[0]["url"] if found else None,
        "why_it_matters": why,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
