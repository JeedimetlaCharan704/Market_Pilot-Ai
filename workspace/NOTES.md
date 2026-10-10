# MarketPilot prototype — research notes

Hackathon: **BUILD IN CAFÉ 2026** (10 Oct 2026, judging 3 PM). Track 2 *The Marketing Agent*.
Rubric: idea quality 35% · end-to-end from two inputs 30% · model independence 15% · usefulness 15% · code+docs 5%.
Hard rule: **no mocks** — real calls, real data, working end-to-end. Opt-out + daily send cap mandatory.

This prototype exists to test ideas. Everything here gets rebuilt fresh tomorrow.
Source repo names are recorded for provenance only — **they must not appear in the final UI or branding.**

---

## Phase 1 — repos found and cloned

All cloned with `git clone --depth 1` into `workspace/repos/`. Disk sizes are all well under 200 MB.
`LICENSE` column = a license file actually present in the repo, not the GitHub sidebar badge.

| # | repo URL | license | language | last commit | what it does | runs with minimal setup? | which parts are reusable |
|---|---|---|---|---|---|---|---|
| 1 | https://github.com/Mahanaicoach/google-maps-scraper-kit | MIT | Python | 2026-10-05 | **Official Track 2 base repo.** Docker wrapper around gosom engine on `127.0.0.1:8080`, plus a Claude Code skill and a stdlib-only CLI that writes an outreach CSV (name, phone, emails, website, rating) | **Needs Docker** — not installed on this machine; no Go toolchain either | `scripts/scrape.py`: pure-stdlib REST client (`req`, `geocode` via free Nominatim, `LEAD` field whitelist, CSV writer). `.claude/skills/.../SKILL.md`: exact job payload + poll loop. `SETUP.md`: 5-min setup narrative |
| 2 | https://github.com/AI-Invention/lead-pipeline | MIT | Python | 2026-08-08 | Scrape → Pitch → Demo → Close: Apify actor for Maps leads → Google Sheets CRM → SMTP cold email, plus IMAP reply/bounce checker | **Needs paid keys** (Apify, Google service account, SMTP) | `outreach/pitch_sender.py::generate_pitch()` — 15-line placeholder generator, ideal "swap in an LLM" seam. `demo/landing.html` (12.9 KB, self-contained). `scraper/apify_scraper.py` run_input + poll loop |
| 3 | https://github.com/getlemnos32/b2b-outbound-sniper | MIT | Python | 2026-03-26 | Job-board hiring signals → blocklist + 3-layer dedup → Hunter verification → LLM personalisation (6 providers) → SMTP/Smartlead/Instantly, with per-run cost log | **Needs paid keys** (Apify, Hunter, LLM); Ollama is the free escape hatch. README's `python -m sniper run` is broken — real entry is the `sniper` console script | `verticals/{agency,saas,staffing}/templates.yaml` — best cold-email copy in the set (`initial_email` / `followup_day3` / `breakup_day7`). `adapters/anthropic_adapter.py::EMAIL_PROMPT`. `sniper/dedup.py`, `cost_tracker.py`. `docs/deliverability-guide.md` |
| 4 | https://github.com/AbdulAHAD968/Google-Maps-Lead-Generation | **none** | JavaScript | 2026-08-20 | "Mapleads": Next.js + Places API (New) click-point search, Playwright deep scan, MongoDB lead store, AI outreach | **Needs cloud account** (GCP billing, Places keys), MongoDB, Playwright, OpenAI | `app/lib/emailScraper.js` — fetch homepage + `/contact`,`/about` etc, extract `mailto:`, 8 s timeout. **Closest thing to a website auditor anywhere in this set.** `app/api/leads/[id]/outreach/route.js` — strongest outreach prompt. `DESIGN.md` — full design-token system. `app/models/Lead.js` — ready-made status enums |
| 5 | https://github.com/ashish7802/maps-lead-scraper | **none** (README shows MIT badge) | Python | 2026-04-05 | Async Playwright CLI: Google Maps search by query + city, scroll, extract, filter, export CSV/JSON. Keyless | **Runs with minimal setup** — `pip install` + `playwright install chromium`. No keys, no Docker, no cloud | `scraper/maps.py::GoogleMapsScraper.scrape()` — only keyless listings fetcher here. `scraper/filters.py::filter_no_website()` / `filter_by_rating()` / `apply_filters()`. `scraper/exporter.py`, `config.yaml` |
| 6 | https://github.com/merdandt/SalesShortcut | **none** (README claims MIT) | Python | 2025-06-28 | 5 FastAPI microservices on Google ADK + A2A: lead-finder, SDR agent (research → proposal → draft → fact-check → outreach), lead-manager, Gmail Pub/Sub listener, WebSocket dashboard | **Needs cloud account** — GCP + BigQuery, Gemini, Maps, ElevenLabs, SMTP. Too tied to one vendor stack | `ui_client/` — **FastAPI + Jinja2 + plain HTML/CSS/JS dashboard**, route list, WebSocket status push: closest structural match to our target stack. `sdr/sdr/prompts.py` — RESEARCH / DRAFT_WRITER / FACT_CHECKER / CONVERSATION_CLASSIFIER. `HACKATHON_STORY.md` — agent-pattern taxonomy |
| 7 | https://github.com/iPythoning/b2b-sdr-agent-template | MIT | Shell + Markdown | 2026-09-29 | Not an app: a Markdown "7-layer context system" driving an AI agent through a 10-stage B2B pipeline over WhatsApp/Telegram/email, plus deploy scripts | **Needs paid model key + remote Linux server + OpenClaw gateway.** Not runnable standalone | `workspace/AGENTS.md` — best state-machine design doc here: Stages 1–10 with stage gates, ICP scoring, anti-abuse policy. `SKILL.md` 7-layer architecture table |
| 8 | https://github.com/gosom/google-maps-scraper | MIT | Go | 2026-09-24 | The actual scraping engine that repo #1 wraps. CLI → CSV/JSON (incl. emails scraped from business sites), built-in web UI + REST API | **Needs Docker or a Go toolchain + Playwright** — neither present | `gmaps/entry.go` + `testdata/*.json` — canonical lead-record field schema. `skills/google-maps-scraper/SKILL.md` + `references/` — best-documented scrape workflow. `web/static/templates/` plain-HTML job table |

### Shortlisted by `gh search` but not cloned
`omkarcloud/google-maps-scraper` (MIT, 3593★), `kiryano/Scout` (MIT, 741★), `Madi-S/Lead-Generation` (MIT, 665★),
`adityajha2005/yc-outreach` (MIT, 162★, pushed 2026-10-06), `warmbly/warmbly` (Apache-2.0, 357★, pushed 2026-10-09).
Skipped to stay inside the 6–8 clone budget; revisit only if Phase 2 finds a gap.

`filip-michalsky/SalesGPT` (MIT, 2778★, last commit 2024-09) — brief says skip; recorded link-only.

### Environment constraints found during Phase 1
| check | result |
|---|---|
| Docker | **absent** (`docker` not on PATH) → repos #1 and #8 cannot run as documented |
| Go toolchain | **absent** → cannot build #8 from source |
| API keys in env / `.env` | **none** (Gemini, Groq, OpenAI, Anthropic, Google Maps, Resend, ElevenLabs, OpenRouter all missing) |
| Ollama | not running |
| Python | 3.11.9 and 3.12.10 available |
| Network / GitHub API | working |

**Consequence:** discovery must default to a keyless real source (OpenStreetMap Nominatim + Overpass), with
Google Places and the Docker scraper kept as config-selectable backends for when a key or Docker appears.

---

## Phase 2 — evaluation

Read each repo's README plus entry points (`scripts/scrape.py`, `main.py`, `sniper/main.py`,
`ui_client/main.py`, `app/api/**`, `workspace/AGENTS.md`). Choices below are what actually gets
used in `prototype/`, with the reasoning.

### Verdicts

| repo | verdict | reason |
|---|---|---|
| google-maps-scraper-kit | **architecture + contract only** | Correct base per the PS, but it needs Docker, which is not installed. We reuse its *REST contract* (`POST /api/v1/jobs` → poll → download), its free Nominatim geocode, and its `LEAD` column set, so the official engine drops in unchanged the moment Docker exists |
| maps-lead-scraper | **second backend, not the default** | Genuinely keyless and runnable (`pip` + `playwright install chromium`) — the only repo that fetches real listings with no account. But it automates Google Maps, which cuts against the track rule *"follow Google's terms"*, and Playwright + consent walls are the flakiest thing in a 5-hour demo. Available as a config backend, never the default |
| SalesShortcut | **prompts + route layout only** | FastAPI + Jinja2 + plain HTML/CSS/JS dashboard is exactly our stack, and its `sdr/prompts.py` set (research → draft → fact-check → classify) is the right shape. No LICENSE file, so nothing is copied verbatim. It also needs GCP + BigQuery + ElevenLabs to run — unusable here |
| Google-Maps-Lead-Generation | **harvest patterns, do not run** | `app/lib/emailScraper.js` is the only code in the whole set that fetches a business website and probes paths — the seed of our auditor. Its outreach route has the best tone instructions. Next.js + MongoDB + GCP billing makes it unrunnable, and it has no LICENSE, so we re-implement rather than copy |
| b2b-outbound-sniper | **email copy structure (MIT)** | `verticals/*/templates.yaml` shows how to structure initial / day-3 follow-up / breakup copy, and `EMAIL_PROMPT` shows the `.format(**lead_data)` seam. Pipeline itself needs Apify + Hunter — skipped. Its `python -m sniper run` entry point is broken in the README, worth knowing before trusting docs |
| b2b-sdr-agent-template | **architecture narrative (MIT)** | Not runnable — a Markdown agent config needing a remote gateway. But `workspace/AGENTS.md` is the best state-machine / stage-gate design doc here and directly feeds the architecture answer the jury will ask for |
| lead-pipeline | **one pattern (MIT)** | `generate_pitch()` is the clean "LLM slot" seam and `demo/landing.html` is a free visual win. The pipeline needs Apify + Google Sheets + SMTP creds, so the flow is studied, not reused |
| google-maps-scraper (gosom) | **field schema (MIT)** | The engine itself. `gmaps/entry.go` + `testdata/*.json` define the canonical lead record, which we mirror so our CSVs are drop-in compatible with the official kit |

### Gap found
**No repo implements a website audit.** Booking-link / contact-form / chat-widget / viewport detection
exists only as an unbuilt roadmap item in `Google-Maps-Lead-Generation/README.md`. This module is
written from scratch — which is fine, it is the part that has to be trustworthy for the evidence cards.

### Chosen design for `prototype/`

**1. Discovery — one interface, three backends.**
`osm` (Nominatim geocode + Overpass query, keyless, ToS-friendly, `User-Agent` + 1 req/s) is the
default. `google_places` activates when `GOOGLE_MAPS_API_KEY` is set. `scraper_docker` speaks the
base repo's exact REST contract and activates when `SCRAPER_BASE_URL` responds. The backend is
selected in `.env`, never hard-coded. Business-type → `(category, location, osm_tag)` planning is an
LLM call with a deterministic string parser as fallback, so discovery never depends on a model being up.

**2. Audit — written fresh, pattern borrowed.**
Fetch homepage + candidate paths with a 10 s timeout and robots.txt check, then detect four gaps:
no booking link, no contact form, no WhatsApp/chat widget, missing `<meta name="viewport">`.
Shape follows `emailScraper.js`; the checks are ours.

**3. Evidence card — the Gravity borrow.**
Every finding carries `{business, source_url, observation, retrieved_at, confidence, why_it_matters}`.
Unfetchable site → `confidence: low` and say so explicitly. No observation, no claim.

**4. LLM — one swappable file, no framework.**
Chose a ~100-line `httpx` adapter over LiteLLM: four REST endpoints (Gemini, Groq, any
OpenAI-compatible incl. OpenRouter and Ollama), picked by `LLM_PROVIDER` in `.env`. Reasoning:
`model independence` is 15% of the rubric and a jury question — a single inspectable file beats a
library dependency whose install is slow and whose version drift can break the demo. Absent key →
static template fallback, **visibly tagged `generated: template` in the UI** so it can never be
mistaken for a model output.

**5. Email draft — MIT sources only.**
Structure from `b2b-outbound-sniper/verticals/*/templates.yaml`, tone rules adapted from
SalesShortcut's `DRAFT_WRITER_PROMPT` (re-implemented, not copied). Mandatory opt-out line.
**Send writes `outbox.log` always and delivers over SMTP when `SMTP_*` is set** — the rules say
*"Demonstrate real calls only; have a controlled test recipient"*, so log-only would read as a mock.

**6. Dashboard — FastAPI + Jinja2 + one HTML file.**
Route layout mirrors `SalesShortcut/ui_client/main.py` (`/`, `/api/search`, `/api/audit/{id}`,
`/api/demo/{id}`, `/api/draft/{id}`, `/api/send`, `/demo/{slug}`); markup written from scratch.

**7. State machine — `workspace/AGENTS.md` stage-gate pattern (MIT).**
As built in `prototype/modules/store.py::STATUS_FLOW`:

```
DISCOVERED -> AUDITED | NO_WEBSITE | FAILED
AUDITED    -> DEMO_GENERATED | DRAFTED | FAILED
NO_GAP     -> DEMO_GENERATED | DRAFTED | FAILED
DEMO_GENERATED -> DRAFTED | FAILED
DRAFTED    -> SENT | FAILED
SENT       -> (terminal)
FAILED     -> DISCOVERED | AUDITED | DEMO_GENERATED | DRAFTED   (retriable)
```

The planned `EVIDENCE_READY` state was **dropped**: evidence is written in the same
transaction as the audit, so a separate state would have been a no-op gate. `SENT` is
terminal on purpose - the rules allow one message per business, so nothing can move a
lead back out of it. Persisted in SQLite, so nothing is re-computed or re-sent on refresh.

### Explicitly not reused
Apify / Hunter / Google Sheets / BigQuery / ElevenLabs integrations (paid or cloud-locked),
Next.js frontend (build step), SalesShortcut's GCP deploy scripts, `lead-pipeline`'s SMTP+IMAP loop.

