# TEST REPORT — MarketPilot prototype

**Date:** 2026-10-09 (afternoon build pass + evening key/network pass) · **Build:** Phase 3/4/5 · **Tester:** automated, run manually against the live network

---

## 1. Environment

| | |
|---|---|
| OS | Windows 10/11 (win32), PowerShell 5.1 |
| Python | 3.11.9 (`.venv`) |
| Key libraries | fastapi, uvicorn, httpx, beautifulsoup4, jinja2, python-dotenv, pytest |
| Docker | **not available** — `scraper_docker` backend could not be exercised |
| Go | **not available** |
| API keys | **Groq** and **Gemini** present in `prototype/.env` (gitignored). No Places key, no SMTP credentials |
| LLM in use | `groq / openai/gpt-oss-120b` (`LLM_PROVIDER=groq`) |
| Ollama | not installed |

Discovery and audit are keyless. The LLM and the geocoder are the only external services in the default path.

---

## 2. Automated suite

```
cd prototype

# offline, deterministic - never touches a network
python -m pytest tests -q
21 passed, 4 skipped, 1 warning in 1.78s

# live model path, opt-in
RUN_LIVE=1 python -m pytest tests/test_llm_live.py -q
4 passed in 10.62s
```

The 4 skips are `test_llm_live.py`, which refuses to run unless `RUN_LIVE=1` — an autouse
fixture forces `LLM_PROVIDER=none` otherwise, so a key sitting in `.env` can never make the
default suite call a provider.

The single warning is `StarletteDeprecationWarning` from `fastapi.testclient` suggesting
`httpx2`. It is upstream, harmless, and not ours.

No test in the default suite touches the network. Coverage by area:

| Area | Tests |
|---|---|
| Status machine | illegal edges rejected, `SENT` terminal, `FAILED` retriable, upsert idempotent, unknown column rejected |
| Discovery (pure) | Overpass query is well-formed, bbox ordered and city-sized |
| Audit (canned HTML) | all four cues detected, bare site reports nothing, `mailto:` alone does **not** count as a contact form |
| Evidence | every card carries URL + date, booking > chat > contact priority, unreadable site → exactly one no-claim card, fully-compliant site → `no gap`, not a fabricated one |
| Demo | template page complete/self-contained/labelled; validator rejects truncated output, missing business name, and external assets; markdown fences stripped |
| Email | cites observation + source URL, links demo, contains `OPT_OUT` and `DISCLAIMER`, zero `!`; `send()` always writes `outbox.log` and opens no socket |
| API guards | unreadable site → **409** on both `/api/demo` and `/api/draft`; verified gap → full 200 chain through `SENT`; `/` renders |

### Bugs the suite caught (fixed in this phase)

1. **`store.upsert_lead` could crash or null a NOT NULL column.** A refresh without a
   `search` key wrote `NULL` into `search NOT NULL` → `sqlite3.IntegrityError`. Now only
   keys actually present are written, and insert defaults `search` to `""`.
2. **The template email did not contain the `OPT_OUT` constant.** The sentence was
   hard-coded and wrapped across a line break, so the literal opt-out text was absent.
   The template now interpolates `email_draft.OPT_OUT` as its own line; the test asserts
   byte-exact presence.
3. **A demo could be generated from an unreadable site.** `/api/demo` only checked that
   *some* card existed. `_verified_card()` in `app.py` now refuses `gap == "unverified"`
   with 409 before any generation runs.

---

## 3. Live network tests

### 3.1 Discovery (OpenStreetMap, default backend)

| Query | Result |
|---|---|
| `cafes` / `Hyderabad` | `backend=osm`, 8 rows, 0 errors |
| `salons` / `Hyderabad` | `backend=osm`, rows returned, website-bearing rows sorted first |
| `dentists` / `Hyderabad` | `backend=osm`, 8 rows, 0 errors |

Sample live rows (nothing invented — all from Overpass):

```
Roast CCx      :: https://roastcoffee.in/
the chocolate room :: http://theChocolateRoomIndia.com
Appaji Dental Clinic :: (no website)
Hyderabad Dental Hospital :: phone 040 40047772; 040 24754140
```

**Findings that changed the code:**

* **Overpass `POST` returns HTTP 504 on this network.** `GET ?data=` succeeds.
  `_overpass()` now uses GET only.
* **Overpass QL syntax.** Every statement inside a union block needs its own trailing
  `;`, and the block itself needs one: `(a;b;);`. The first implementation produced
  `parse error: ';' expected - 'out' found` (HTTP 400).
* **Tag existence filter.** `node["shop"="hairdresser"]` is valid;
  `node["hairdresser"]` is not. Separately, `["website"]` can be used as an *existence*
  filter, so the first pass asks only for businesses that publish a site — a lead with
  no website can never produce evidence. A second, unfiltered pass fills the gap.
* **Mirrors are unreliable.** Four endpoints are tried in order, the whole list walked
  twice with a 3 s pause, because kumi/api.de/private.coffee/maps.mail.ru each return
  400/500/504 at different moments. Measured single-query latency: 1.7 s (warm) to
  27.5 s (rate-limited, retries). One run took 54.6 s end to end and still succeeded.
* **Nominatim began returning HTTP 403 — the critical find of the evening pass.**
  It blocks this IP regardless of User-Agent (verified with three different UAs, all
  `403 Access denied. See https://operations.osmfoundation.org/policies/nominatim/`).
  Discovery therefore now resolves coordinates through **Photon**
  (`photon.komoot.io`, keyless, OSM-sourced) first and Nominatim second, and caches
  every result in `data/geocode_cache.json` with its source and retrieval time.
  Verified: first lookup 2.01 s via photon, repeat lookup 0.01 s from cache.

| Geocode probe | Result |
|---|---|
| Nominatim, configured UA | HTTP 403 |
| Nominatim, httpx default UA | HTTP 403 |
| Nominatim, contact-style UA | HTTP 403 |
| Photon `q=Hyderabad` | HTTP 200 → `(17.36059, 78.47406)`, props `Telangana / India / city` |
| Photon `q=Banjara Hills, Hyderabad` | HTTP 200 → `(17.41775, 78.43990)` |
| Overpass area lookup (kumi) | HTTP 500 — rejected as an alternative |

### 3.2 Audit (live HTTP fetches)

| Site | `fetch_ok` | Pages read | Gaps |
|---|---|---|---|
| `https://roastcoffee.in/` | ✅ | 1 | `booking, contact, chat` |
| `https://ciclo.cafe/` | ✅ | 1 | `booking, contact, chat` |
| `https://naturals.in/` | ✅ | 2 | `contact` only (booking cue `Book A Service Today`, chat cue `wa.me`) |
| `http://theChocolateRoomIndia.com` | ✅ | — | — |
| `https://aakaaraasalon.com/` | ❌ | 0 | reason: `site could not be fetched` (`getaddrinfo failed` — dead domain) |
| `https://www.zomato.com/...` | ❌ | 0 | reason: `blocked by robots.txt` |

`pages_read` is often 1 because `/contact` and friends return 404 on small-business
sites; the audit records each probe with its error rather than hiding it.

Robots compliance was itself verified directly:

```
zomato.com  -> robots_allowed: False   (crawl refused, lead marked FAILED)
example.com -> robots_allowed: True
```

### 3.3 Full pipeline, end to end (real data)

```
POST /api/search   {"business_type":"cafes","city":"Hyderabad"}
  -> backend=osm, 8 leads, all DISCOVERED
POST /api/audit/2  (https://roastcoffee.in/)
  -> AUDITED, reason=booking, fetch_ok=true, gaps=booking,contact,chat
  -> 3 evidence cards: "No online booking" / "No contact form" / "No WhatsApp or chat widget"
POST /api/demo/2
  -> 200, slug=roast-ccx-b575ea, generated_by="template (no LLM key configured)"
POST /api/draft/2
  -> 200, subject="Your site at Roast CCx: one thing missing"
     OPT_OUT byte-exact present: True
     demo link present: True
     generated_by="template (no LLM key configured)"
POST /api/send/2
  -> 200, mode=logged, status=SENT
     outbox.log: 1 line, JSON, includes lead_id + full body

GET  /            -> 200 (dashboard)
GET  /demo/roast-ccx-b575ea -> 200, 4 172 bytes
GET  /demo/nope   -> 404
Final counts      -> {"DISCOVERED":6, "NO_WEBSITE":1, "SENT":1}
```

### 3.4 Refusal paths (the honesty rules under fire)

| Situation | Result |
|---|---|
| Lead with no website → `/api/audit` | **422** "No website to audit", status `NO_WEBSITE` |
| Site unreachable → `/api/demo` | **409** (not 500, not a silently generated page) |
| Site unreachable → `/api/draft` | **409** |
| Robots-blocked site → evidence | exactly one card: *"Site not verified - no claim made"*, confidence `low` |
| Unverified card → email | **refused**; no message reaches `outbox.log` |
| `DISCOVERED` → `SENT` directly | `InvalidTransition` |

### 3.5 Fallback snapshot (opt-in)

```
ALLOW_FALLBACK=1, city="zzzznowherecity"  (deliberately un-geocodable)
  -> backend=fallback
  -> errors: ["osm: could not geocode ...", "scraper_docker: ConnectError ...",
              "all live backends failed; showing FIXTURE data"]
  -> 6 rows, every one tagged source=FALLBACK
```

The file `data/fallback_businesses.json` carries its own provenance block:

```
_source      : https://overpass-api.de/api/interpreter (OpenStreetMap, ODbL)
_captured_at : 2026-10-09T09:05:39+00:00
_about       : Cached snapshot of a real OpenStreetMap Overpass query ...
               Nothing here is invented. The audit still fetches each website live.
```

The dashboard shows a `⚠ FIXTURE DATA` banner when this path is used. Default is
`ALLOW_FALLBACK=0`, so it cannot happen by accident.

### 3.6 Model path (evening pass, keys configured)

Both supplied keys authenticate. The **default model IDs in the code were stale** and
returned HTTP 404 — this is why the adapter's defaults were re-verified rather than trusted:

| Provider | Tried | Result |
|---|---|---|
| gemini | `gemini-2.0-flash` | **404** "no longer available" |
| gemini | `gemini-2.5-flash` | **404** "no longer available to new users" |
| gemini | `gemini-3-flash` | **404** not found |
| gemini | `gemini-3.8-flash` | **200** in 21.3 s |
| groq | `llama-3.3-70b-versatile` | **404** not accessible on this key |
| groq | `openai/gpt-oss-120b` | **200** in 0.7 s ✅ |
| groq | `qwen/qwen3.8-27b` | **200** in 0.3 s |
| groq | `openai/gpt-oss-20b` | **200** in 0.7 s |

`DEFAULT_MODELS` was updated from those results, and `LLM_PROVIDER=groq` set, because
Groq is 8–25× faster than the only working Gemini model — latency matters when the jury
is watching.

**Model-written demo, verified 3 ways:**

```
generate(Roast CCx, "booking", ...)
  -> 6.6s  generated_by=model:groq/openai/gpt-oss-120b  bytes=6449
  doctype True | </html> True | name present True | viewport True | external asset False
```

The first attempt **failed `_validate` with `model used external resources:
['href="https://roastcoffee.in/']`** — the model linked back to the business's own site.
The validator was wrong, not the model: it flagged `<a href>` navigation along with real
asset loads. `_validate` now strips `<a …>…</a>` before checking, so only stylesheets,
scripts, fonts and images are rejected. The system prompt was tightened to match.

**Model-written email, 3 consecutive runs:**

| Run | Time | Subject | Greeting | Name | `OPT_OUT` | Demo link | Words |
|---|---|---|---|---|---|---|---|
| 1 | 4.3 s | Reservation widget missing on Roast CCx site | `Hi Roast CCx team,` | ✅ | ✅ | ✅ | 59 |
| 2 | 3.6 s | Add a reservation link to Roast CCx site | `Hi Roast CCx team,` | ✅ | ✅ | ✅ | 65 |
| 3 | 2.4 s | Add a reservation link to Roast CCx site | `Hi Roast CCx team,` | ✅ | ✅ | ✅ | 60 |

Before tightening the prompt the model opened with a bare `Hi,` and omitted the business
name — caught by inspection, fixed by adding *"First line of the body must be exactly:
Hi {name} team,"* to the prompt. `DISCLAIMER` is now appended alongside `OPT_OUT`
*after* the model returns, so compliance never depends on the model remembering it.

**Full live chain through the API, model enabled:**

```
POST /api/search   cafes/Hyderabad -> backend=osm, 8 leads
POST /api/audit/3  (theChocolateRoomIndia.com)
  -> AUDITED, reason=booking, fetch_ok=true, gaps=booking,contact,chat
POST /api/demo/3   -> 200  generated_by=model:groq/openai/gpt-oss-120b
POST /api/draft/3  -> 200  generated_by=model:groq/openai/gpt-oss-120b
                       subject="Add a reservation link to The Chocolate Room site"
                       first line="Hi the chocolate room team,"
                       OPT_OUT=True  DISCLAIMER=True  demo link=True  80 words
POST /api/send/3   -> 200  mode=logged, status=SENT
POST /api/demo/2   -> 409  (lead 2 already SENT: the terminal state held)
```

---

## 4. Not tested, and why

| Path | Reason |
|---|---|
| `google_places` backend | no `GOOGLE_MAPS_API_KEY` in this environment |
| `scraper_docker` backend | Docker not installed; `localhost:8080` refused connection |
| Model path on **Gemini** end-to-end | `gemini-3.8-flash` answered the raw probe (200, 21.3 s) but only Groq is wired as the active provider, so only `groq` was driven through `demo_generator` and `email_draft`. The provider switch itself is one `.env` line |
| Real SMTP delivery | no credentials. `send()` was tested in `logged` mode, which is the required default; the SMTP branch is deliberately restricted to `SMTP_TEST_TO` |
| Browser-side rendering | only `GET /` and `GET /demo/{slug}` status/size were checked over HTTP, not a visual pass |

---

## 5. Known limitations

1. **Discovery latency.** A cold Overpass query ranged from 1.7 s to 54.6 s on
   2026-10-09 while mirrors rate-limited. Mitigated by endpoint rotation + one retry,
   but not eliminated — this is the single biggest live-demo risk.
2. **Nominatim is unusable from this network (HTTP 403, IP-level).** Photon replaces it
   and results are cached, but Photon is a third-party service with no uptime promise.
   `data/geocode_cache.json` is committed so the cities used in rehearsal work offline.
2. **`pages_read` is frequently 1.** Small-business sites rarely expose `/contact` or
   `/book`, so the audit leans on the homepage. Confidence is reported as `medium`
   (rather than `high`) for exactly this reason.
3. **OSM coverage is uneven.** Hyderabad cafés carry websites; Hyderabad dentists mostly
   do not. Those leads correctly land in `NO_WEBSITE` instead of being padded with
   guesses — but it means a search can return zero auditable rows.
4. **The demo link in the email uses `PUBLIC_BASE_URL` (default
   `http://localhost:8000`).** On the judging laptop it must be set to whatever URL is
   actually reachable by the reader.
5. **No reply handling or unsubscribe store.** Out of scope for the rubric; the opt-out
   line is text, not a suppression list. Stated openly here rather than implied away.

---

## 6. Verdict

The two required inputs produce a real, evidenced, end-to-end result with no mock
anywhere in the chain: every business row comes from OpenStreetMap, every gap is backed
by a fetched page, the demo and the email are written by a live model that still passes
the same validators the template path does, and the three refusal paths (no website /
unreadable site / illegal transition) return structured errors instead of inventing an
answer.
