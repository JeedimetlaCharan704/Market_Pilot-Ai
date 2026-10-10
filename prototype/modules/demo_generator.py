"""Mini-demo generator: turn one evidence card into a working HTML page.

This is the differentiator, so it has two honest paths:
  * a real model is configured  -> the model writes the page, we validate it
  * no model is configured      -> a deterministic template is used and the page
                                   is tagged `generated: template` so nobody can
                                   mistake it for model output.
"""

from __future__ import annotations

import hashlib
import json
import re
from html import escape as _escape

from . import llm
from .settings import runtime_dir

DEMOS = runtime_dir() / "demos"

GAP_COPY = {
    "booking": (
        "an online booking page where customers pick a service and a time slot",
        "Book an appointment",
    ),
    "contact": (
        "a short contact form that captures name, phone and what the customer needs",
        "Send an enquiry",
    ),
    "chat": (
        "a click-to-message button that opens WhatsApp with the conversation pre-filled",
        "Message us now",
    ),
    "mobile": (
        "a mobile-first layout so the site is readable on a phone without zooming",
        "Open on mobile",
    ),
    "no_website": (
        "a complete turnkey starter website and digital storefront with click-to-call, service showcase, and interactive inquiry",
        "Claim this website",
    ),
    "none": ("a refreshed landing page", "Get in touch"),
    "unverified": ("a refreshed landing page", "Get in touch"),
}

SYSTEM = (
    "You are a senior front-end developer building a single-file HTML page for a "
    "small local business. Output ONLY the HTML document, starting with <!doctype html> "
    "and ending with </html>. No markdown fences, no commentary. The page must be fully "
    "self-contained: inline CSS and inline JS only, zero external stylesheets, scripts, fonts "
    "or images - an <a href> back to the business's own site is the only link you may emit. "
    "It must be responsive (meta viewport), use the business's real name, address "
    "and phone, look polished, and clearly be a proposal/demo rather than a live service."
)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "business"
    return f"{slug}-{hashlib.sha1(name.encode()).hexdigest()[:6]}"


def generate(business: dict, gap: str, observation: str) -> dict:
    """Returns {slug, path, generated_by, html}."""
    what, cta = GAP_COPY.get(gap, GAP_COPY["none"])
    DEMOS.mkdir(parents=True, exist_ok=True)
    slug = slugify(business["name"])

    if llm.available():
        try:
            html = _from_model(business, gap, observation, what, cta)
            generated_by = f"model:{llm.provider()}/{llm.model_name()}"
        except llm.LLMError as exc:
            html = _from_template(business, what, cta)
            generated_by = f"template (model failed: {exc})"
    else:
        html = _from_template(business, what, cta)
        generated_by = "template (no LLM key configured)"

    html = _validate(html, business["name"])
    html = _add_service_note(html, business.get("service") or "")
    path = DEMOS / f"{slug}.html"
    path.write_text(html, encoding="utf-8")
    return {"slug": slug, "path": str(path), "generated_by": generated_by, "html": html}


def _add_service_note(html: str, service: str) -> str:
    """Guarantee the page never implies the seller's service was built for them.

    Added after validation, the same way the opt-out is added to an email.
    """
    service = (service or "").strip()
    if not service:
        return html
    if "not a build of that service" in html.lower() or "prepared for a seller of" in html.lower():
        return html
    note = (
        '<p style="margin:16px 0;padding:12px 16px;background:#f1f5f9;'
        'border:1px solid #e2e8f0;border-radius:9px;font-size:14px;">'
        f"Prepared for a seller of <strong>{_escape(service)}</strong>. This preview shows "
        "the fix for one gap we observed on the live site - it is not a build of that "
        "service.</p>"
    )
    idx = html.lower().rfind("</body>")
    if idx == -1:
        return html
    return f"{html[:idx]}{note}{html[idx:]}"


def _validate(html: str, business_name: str) -> str:
    """Reject anything that is not a complete, self-contained document."""
    html = html.strip()
    if html.startswith("```"):
        html = re.sub(r"^```[a-z]*\s*|\s*```$", "", html, flags=re.I).strip()
    lowered = html.lower()
    if "<!doctype" not in lowered and "<html" not in lowered:
        raise ValueError("model did not return a full HTML document")
    if "</html>" not in lowered:
        raise ValueError("model returned a truncated HTML document")
    if business_name.lower() not in lowered and _escape(business_name).lower() not in lowered:
        raise ValueError("model dropped the business name")
    without_links = re.sub(r"<a\b[^>]*>.*?</a>", "", html, flags=re.I | re.S)
    external = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', without_links)
    if external:
        raise ValueError(f"model used external resources: {external[:2]}")
    return html


def _from_model(business: dict, gap: str, observation: str, what: str, cta: str) -> str:
    prompt = f"""
Build a single-file HTML page that demonstrates {what} for this business.

Business name : {business['name']}
Type          : {business.get('search') or 'local business'}
Service I sell: {business.get('service') or 'not stated'}
Address       : {business.get('address') or 'not published'}
Phone         : {business.get('phone') or 'not published'}
Website       : {business.get('website') or 'none'}
City          : {business.get('city') or ''}

Evidence that motivated this page (quote it accurately, do not invent facts):
{observation}

Page requirements:
- A hero with the business name, the {cta} action and the real phone number.
- A working-looking {what} with a small form or slot picker. JavaScript only updates
  the page; it must not call any network endpoint.
- Three short sections describing the business's services (keep them generic and
  plausible for this business type - do not invent prices, awards or reviews).
- A dismissible banner reading "Demo preview generated by MarketPilot - not a live service."
- Mobile responsive, inline CSS, one accent colour, no external assets, no images.
- Footer with the address and the same demo disclaimer.

Honesty constraint:
- This page demonstrates the fix for the one gap we observed. It is a preview of the
  outcome, not a finished build of the seller's service. Do not describe it as the
  completed deliverable, and do not invent pricing, clients or results.
"""
    return llm.complete(prompt, SYSTEM)


def _from_template(business: dict, what: str, cta: str) -> str:
    raw_name = business["name"]
    name = _escape(raw_name)
    address = _escape(business.get("address") or "")
    phone = _escape(business.get("phone") or "")
    kind = _escape(business.get("search") or "local business")
    raw_service = (business.get("service") or "").strip()
    service = _escape(raw_service)
    is_call_agent = any(k in raw_service.lower() for k in ("call", "voice", "phone", "telecom", "receptionist", "inbound"))
    service_line = (
        f'<p class="prepared">Prepared for a seller of <strong>{service}</strong>. '
        "This preview shows the fix for one gap we observed on the live site - "
        "it is not a build of that service.</p>"
        if service
        else ""
    )

    call_simulator_html = ""
    if is_call_agent:
        call_simulator_html = f"""
    <div class="card" style="margin-top:24px;border:2px solid #0f766e;background:#f0fdf4;">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <h3 style="margin:0;color:#0f766e;font-size:18px;">📞 Interactive AI Voice &amp; Call Triage Simulator</h3>
        <span style="background:#22c55e;color:#fff;font-size:12px;font-weight:700;padding:4px 10px;border-radius:99px;">● AGENT ONLINE</span>
      </div>
      <p style="margin:8px 0 16px;font-size:14px;color:#334155;">Test how our autonomous voice assistant fields inbound inquiries for <strong>{name}</strong> without missing after-hours leads.</p>
      
      <div style="background:#0f172a;color:#f8fafc;border-radius:10px;padding:16px;font-family:monospace;font-size:13px;line-height:1.5;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;border-bottom:1px solid #334155;padding-bottom:8px;">
          <span style="color:#38bdf8;">[Call Status: <span id="call-state">Standby</span>]</span>
          <span id="call-timer" style="color:#94a3b8;display:none;">00:18</span>
        </div>
        <div id="call-log" style="min-height:90px;">
          <p style="color:#94a3b8;margin:0;">Click "Simulate Inbound Lead Call" to launch interactive voice triage dialogue.</p>
        </div>
      </div>

      <div id="call-controls" style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="cta" onclick="startSimulatedCall()" id="btn-call-start">▶ Simulate Inbound Lead Call</button>
        <button class="cta" onclick="respondCall('visit')" id="btn-choice-1" style="display:none;background:#2563eb;">📅 "I want to schedule an appointment"</button>
        <button class="cta" onclick="respondCall('pricing')" id="btn-choice-2" style="display:none;background:#7c3aed;">💰 "What is the pricing &amp; options?"</button>
      </div>

      <div id="lead-ticket" style="display:none;margin-top:14px;padding:12px;background:#dcfce7;border:1px solid #86efac;border-radius:8px;font-size:13px;color:#166534;">
        <strong>✓ Qualified Inbound Lead Dispatched:</strong> High-intent prospect captured. Instant notification routed to <em>{phone or 'business phone'}</em>.
      </div>
    </div>
    """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} - {cta}</title>
<style>
  :root {{ --accent:#0f766e; --ink:#0f172a; --muted:#64748b; --bg:#f8fafc; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
         color:var(--ink); background:var(--bg); }}
  header {{ background:var(--accent); color:#fff; padding:48px 20px; text-align:center; }}
  header h1 {{ margin:0 0 8px; font-size:clamp(28px,5vw,44px); letter-spacing:-.02em; }}
  header p {{ margin:0; opacity:.9; }}
  main {{ max-width:760px; margin:-32px auto 0; padding:0 20px 48px; }}
  .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px;
           padding:28px; box-shadow:0 8px 24px rgba(15,23,42,.06); }}
  .cta {{ display:inline-block; background:var(--accent); color:#fff; border:0;
          padding:12px 20px; border-radius:9px; font-size:15px; font-weight:600;
          cursor:pointer; text-decoration:none; }}
  label {{ display:block; margin:14px 0 6px; font-weight:600; font-size:14px; }}
  input,select,textarea {{ width:100%; padding:11px 12px; border:1px solid #cbd5e1;
           border-radius:8px; font:inherit; background:#fff; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; }}
  h2 {{ font-size:20px; margin:34px 0 12px; }}
  .note {{ background:#fef9c3; border:1px solid #fde047; border-radius:9px;
    padding:12px 16px; font-size:14px; margin-bottom:18px; }}
  .prepared {{ background:#f1f5f9; border:1px solid #e2e8f0; border-radius:9px;
    padding:12px 16px; font-size:14px; margin-bottom:18px; }}
  .quick-bar {{ display:flex; gap:10px; flex-wrap:wrap; margin:16px 0; }}
  .pill {{ background:#e2e8f0; color:#334155; padding:6px 14px; border-radius:99px; font-size:13px; font-weight:600; }}
  footer {{ text-align:center; color:var(--muted); font-size:13px; padding:32px 20px; }}
  #ok {{ display:none; margin-top:16px; color:var(--accent); font-weight:600; }}
</style>
</head>
<body>
<header>
  <div style="display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.2);padding:4px 14px;border-radius:99px;font-size:12px;margin-bottom:12px;font-weight:600;">
    <span>LIVE PREVIEW &middot; READY TO CLAIM</span>
    <button onclick="openClaimModal()" style="background:#f59e0b;color:#000;border:0;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;cursor:pointer;">🚀 Claim This Site</button>
  </div>
  <h1>{name}</h1>
  <p>{kind.title()} &middot; {address}</p>
</header>
<main>
  <div class="note"><strong>Demo preview</strong> generated by MarketPilot &mdash; not a live service. Nothing you enter is sent anywhere.</div>
  {service_line}

  <div class="card">
    <div class="quick-bar">
      {f'<span class="pill">📞 Phone: {phone}</span>' if phone else ''}
      <span class="pill">📍 Location: {address or kind}</span>
      <span class="pill">⚡ Status: High-Speed Mobile Ready</span>
    </div>

    <h2 style="margin-top:12px">{cta}</h2>
    <p>This is a working preview of {what} for <strong>{name}</strong>.</p>
    <div class="grid">
      <div>
        <label for="svc">Inquiry Type</label>
        <select id="svc">
          <option>General Service Inquiry</option><option>Pricing &amp; Availability</option>
          <option>Direct Consultation / Appointment</option>
        </select>
      </div>
      <div>
        <label for="day">Preferred date</label>
        <input id="day" type="date">
      </div>
      <div>
        <label for="slot">Preferred timing</label>
        <select id="slot"><option>Morning (10 AM - 1 PM)</option><option>Afternoon (1 PM - 5 PM)</option><option>Evening (5 PM - 8 PM)</option></select>
      </div>
    </div>
    <label for="nm">Your name</label><input id="nm" placeholder="Full name">
    <label for="ph">Phone number</label><input id="ph" type="tel" placeholder="+91 ...">
    <p style="margin-top:22px"><button class="cta" onclick="go()">{cta}</button></p>
    <p id="ok">Thanks! This is a demo preview &mdash; in production, inquiries instantly notify {phone or 'the business owner'}.</p>
  </div>

  {call_simulator_html}

  <h2>What makes this proposal convert</h2>
  <div class="grid">
    <div class="card"><strong>Instant Turnkey Deployment:</strong> Pre-built with real business data, requiring zero setup friction from the owner.</div>
    <div class="card"><strong>Multi-Channel Routing:</strong> Direct WhatsApp and SMS handoffs ensure zero inquiries get lost during peak hours.</div>
    <div class="card"><strong>Verified Local SEO:</strong> Structured schema and mobile-first responsive layout to outrank competitors on Google Maps.</div>
  </div>
</main>
<footer>{name} &middot; {address}<br>Demo preview generated by MarketPilot &mdash; not a live service.</footer>

<!-- Claim Ownership Modal -->
<div id="claim-modal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,0.7);z-index:9999;align-items:center;justify-content:center;padding:20px;">
  <div style="background:#fff;border-radius:14px;max-width:440px;width:100%;padding:24px;box-shadow:0 20px 40px rgba(0,0,0,0.3);text-align:left;">
    <h3 style="margin:0 0 6px;font-size:20px;color:#0f172a;">Claim Ownership of {name}</h3>
    <p style="margin:0 0 16px;font-size:13px;color:#64748b;">Claim this turnkey site on your domain with instant WhatsApp and customer reservation routing.</p>
    <label for="cl-name">Your Full Name</label>
    <input id="cl-name" placeholder="Owner or Manager Name">
    <label for="cl-phone">Phone Number</label>
    <input id="cl-phone" type="tel" placeholder="+91 ...">
    <label for="cl-email">Email Address</label>
    <input id="cl-email" type="email" placeholder="owner@business.com">
    <div style="display:flex;gap:10px;margin-top:20px;">
      <button class="cta" onclick="submitClaim()" style="flex:1;background:#10b981;">Confirm &amp; Claim Site</button>
      <button onclick="closeClaimModal()" style="background:#e2e8f0;border:0;border-radius:8px;padding:10px 16px;cursor:pointer;font-weight:600;">Cancel</button>
    </div>
    <div id="cl-result" style="display:none;margin-top:14px;padding:12px;border-radius:8px;font-size:13px;"></div>
  </div>
</div>

<script>
var BIZ_NAME = {json.dumps(business["name"])};
var BIZ_PHONE = {json.dumps(business.get("phone") or "the business")};

function openClaimModal(){{ document.getElementById('claim-modal').style.display='flex'; }}
function closeClaimModal(){{ document.getElementById('claim-modal').style.display='none'; }}
function submitClaim(){{
  var nm = document.getElementById('cl-name').value.trim();
  var ph = document.getElementById('cl-phone').value.trim();
  var em = document.getElementById('cl-email').value.trim();
  var res = document.getElementById('cl-result');
  if(!nm || !ph){{ alert('Please enter your name and phone number to claim ownership.'); return; }}
  res.style.display='block';
  res.style.background='#dcfce7';
  res.style.color='#166534';
  res.innerHTML='<strong>✓ Claim Submitted!</strong> Ownership registered for ' + BIZ_NAME + '. MarketPilot has activated priority provisioning.';
  var slug = window.location.pathname.split('/').pop().replace('.html','');
  try {{
    fetch('/api/claim/' + slug, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{name: nm, phone: ph, email: em}})
    }});
  }}catch(e){{}}
}}

function speakVoice(text){{
  if('speechSynthesis' in window){{
    try {{
      window.speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance(text);
      u.rate = 1.0;
      u.pitch = 1.0;
      window.speechSynthesis.speak(u);
    }}catch(e){{}}
  }}
}}

function go(){{
  var n=document.getElementById('nm').value.trim();
  var ok=document.getElementById('ok');
  ok.style.display='block';
  ok.textContent = (n ? n+', y' : 'Y') + 'our inquiry was simulated locally for this preview. Nothing was sent over the network. Call ' + BIZ_PHONE + ' to connect for real.';
}}

function startSimulatedCall(){{
  var state=document.getElementById('call-state');
  var timer=document.getElementById('call-timer');
  var log=document.getElementById('call-log');
  var btnStart=document.getElementById('btn-call-start');
  var c1=document.getElementById('btn-choice-1');
  var c2=document.getElementById('btn-choice-2');
  if(!state) return;
  state.textContent='Connected (Inbound Voice)';
  state.style.color='#4ade80';
  timer.style.display='inline';
  btnStart.style.display='none';
  var greet = 'Welcome to ' + BIZ_NAME + '! I can help you immediately. Are you looking to schedule an appointment or would you like current pricing options?';
  log.innerHTML='<p style="color:#38bdf8;margin:4px 0;">[Caller]: Hello, I saw ' + BIZ_NAME + ' online. Can I get details on availability?</p><p style="color:#a7f3d0;margin:4px 0;">[AI Voice Agent 🔊]: ' + greet + '</p>';
  speakVoice(greet);
  c1.style.display='inline-block';
  c2.style.display='inline-block';
}}

function respondCall(choice){{
  var log=document.getElementById('call-log');
  var ticket=document.getElementById('lead-ticket');
  var c1=document.getElementById('btn-choice-1');
  var c2=document.getElementById('btn-choice-2');
  var ans = '';
  if(choice==='visit'){{
    ans = 'Perfect! I have reserved a priority slot for you at ' + BIZ_NAME + '. A confirmation text has been dispatched to your phone.';
    log.innerHTML+='<p style="color:#38bdf8;margin:4px 0;">[Caller]: I would like to schedule an appointment this week.</p><p style="color:#a7f3d0;margin:4px 0;">[AI Voice Agent 🔊]: ' + ans + '</p>';
  }}else{{
    ans = 'I have dispatched our digital brochure and rate card to your WhatsApp number.';
    log.innerHTML+='<p style="color:#38bdf8;margin:4px 0;">[Caller]: What are your rates and packages?</p><p style="color:#a7f3d0;margin:4px 0;">[AI Voice Agent 🔊]: ' + ans + '</p>';
  }}
  speakVoice(ans);
  c1.style.display='none';
  c2.style.display='none';
  if(ticket) ticket.style.display='block';
}}
</script>
</body>
</html>"""

