# RUNBOOK — build day, 10 Oct 2026 (10:00 → 15:00, judging 15:00)

Work from this file. It is the *sketch* the rules allow you to bring; the code itself is
written fresh today. Commit after every block — judges will read the history.

**Reference only (do not copy files from it):**
`https://github.com/sudhamshkala-collab/test-project` — the working prototype.
Local path: `C:\3rd year charan\CAFE HACKATHON\prototype`

---

## 00:00 — before 10:00 (10 min, do not skip)

```powershell
# fresh repo, fresh history
gh repo create marketpilot --private --source . --push   # on your MAIN account
cd prototype 2>$null; New-Item -ItemType Directory -Path prototype\modules, prototype\templates, prototype\tests, prototype\data | Out-Null
py -3.11 -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt        # write this file first, it is 6 lines
```

`.venv`, `data/`, `demos/`, `outbox.log` must be gitignored **before the first commit**.

**Warm the external services now, not at 14:30:** one Nominatim/Photon call, one Overpass
call, one Groq call. See the landmine list below.

---

## Build order

Each block ends with a verification command and a commit. Do not start the next block
until the previous one passes — that is what keeps 13:00 from becoming 15:00.

**Blocks 1–8 are the demo.** Blocks 11–14 are the extras built on 9 Oct; do them only
after block 8 is green end to end, and be willing to stop at any of them.

| # | Block | Done when | Verify | Commit |
|---|---|---|---|---|
| 1 | `config.yaml` + `modules/settings.py` + `modules/store.py` | YAML loads, env still wins, status graph + SQLite persist | `pytest tests/test_agent_features.py -k config` | `Phase 1: config, status graph and lead store` |
| 2 | `modules/net.py` | robots + throttle + one UA | `python -c "from modules.net import robots_allowed; print(robots_allowed('https://example.com/'))"` | `Phase 2: polite HTTP layer` |
| 3 | `modules/discovery.py` | 8 real rows for `cafes/Hyderabad`, `health()` names three backends | `python -c "from modules import discovery; print(discovery.search('cafes','Hyderabad')['backend'])"` | `Phase 3: OSM discovery` |
| 4 | `modules/audit.py` + `modules/evidence.py` | 4 checks, cards with URL+date **and a 0–100 score + why_it_fits** | audit `roastcoffee.in` → 3 scored cards | `Phase 4: audit, evidence cards and scoring` |
| 5 | `modules/llm.py` | provider resolves (LiteLLM or HTTP), JSON round-trip | `RUN_LIVE=1 pytest tests/test_llm_live.py` | `Phase 5: swappable LLM adapter` |
| 6 | `modules/demo_generator.py` | self-contained HTML, validator rejects bad output, service disclaimer enforced | `pytest tests/test_demo.py` | `Phase 6: demo generator` |
| 7 | `modules/email_draft.py` + **opt-out registry in `store.py`** | OPT_OUT + DISCLAIMER in every body; `add_opt_out` / `is_opted_out` work | `pytest -k opt_out` | `Phase 7: outreach, send and the opt-out registry` |
| 8 | `app.py` + `templates/index.html` | three inputs → visible end-to-end; `/api/optout` round-trips | full chain, `curl` the endpoints | `Phase 8: API and dashboard` |
| 9 | `tests/` + `README.md` | suite green from a clean clone | `pytest` → all green | `Phase 9: tests and docs` |
| 10 | `vercel deploy` | public URL serves the chain | see "Deployment" below | `Phase 10: deploy to Vercel` |

### Stretch — only after block 8 is green

| # | Block | Done when | Verify | Rebuild cost |
|---|---|---|---|---|
| 11 | `modules/replies.py` | poll → classify → record; opt-out regex beats the model | `pytest -k classif` | ~90 min |
| 12 | `modules/notify.py` | Telegram sends when configured, returns False when not | `pytest -k telegram` | ~45 min |
| 13 | `modules/worker.py` | refuses on Vercel with 409, runs a thread locally | `pytest -k worker` | ~45 min |
| 14 | `start_scraper.ps1` | Docker kit starts, `health()` reports it reachable | `.\start_scraper.ps1` | ~60 min, needs Docker |

Target: block 8 done by **13:10**. Blocks 9–10 are what you drop if you are behind —
**never** drop block 8 to chase a stretch item.

---

## Landmines (each one cost time on 9 Oct — do not rediscover them)

1. **Overpass must be GET, never POST.** POST returns 504 on this network.
   `GET https://overpass.kumi.systems/api/interpreter?data=<urlencoded query>`
2. **Overpass QL syntax.** Every statement inside a union needs its own `;`, *and* the
   block needs one: `"[out:json];(a;b;);out center tags 8;"`.
   Wrong: `(a;b)out ...` → `parse error: ';' expected`.
3. **Tag filters vs name filters.** `node["shop"="hairdresser"]` is valid;
   `node["hairdresser"]` is not. `["website"]` works as an *existence* filter — use it for
   the first pass so you get auditable leads, then an unfiltered pass to fill up.
4. **Four Overpass mirrors, walked twice with a 3 s pause.** kumi / overpass-api.de /
   private.coffee / maps.mail.ru each fail at different moments. Latency 1.7 s → 54.6 s.
   Set `maxDuration: 60` or you will be killed mid-request.
5. **Nominatim returns HTTP 403 to this IP** (and very likely to Vercel) regardless of
   User-Agent. Use **Photon** `https://photon.komoot.io/api/?q=...&limit=1` first,
   Nominatim second, and cache coordinates locally.
6. **Both default LLM model IDs were stale (HTTP 404).** Working on 2026-10-09:
   `groq → openai/gpt-oss-120b` (0.3–0.7 s) and `gemini → gemini-3.8-flash` (21.3 s).
   Re-verify with a raw call before you build on them.
7. **A model that links to the business's own site will fail a naive validator.**
   Strip `<a …>…</a>` before checking for external assets.
8. **The model drops the business name from the greeting.** Put the exact first line in
   the prompt: `Hi {name} team,`. Append `OPT_OUT` and `DISCLAIMER` *after* the model
   returns — never trust it to remember.
9. **A lead whose site could not be read must never get a demo or an email.** Refuse with
   409 on `gap == "unverified"`. This is the single rule most likely to be asked about.
10. **`SENT` must be terminal**, including across any "reset"/"retry" affordance.
11. **OSM hands you aggregator URLs** (Zomato, Swiggy, Google). Auditing one tells you
    nothing about the business and the product correctly refuses it — so pick a lead with
    the business's own domain, or your demo stalls on a lead you cannot audit.
12. **A cold function's first contact with a host fails** and takes every probe with it,
    so the site looks unreadable. `_read()` in `modules/audit.py` therefore re-runs the
    whole probe set once when nothing was fetched; `modules/net.py` additionally retries
    a single request on a transient transport error. Without both, the first audit of the
    day returns `FAILED` in front of the jury.
13. **Deleting `data/marketpilot.db` does not reset the app.** `init_db()` runs once at
    import; recreate the schema after wiping, or every write fails with
    `no such table: leads`.
14. **Vercel blocks the deploy if the HEAD commit's author email is not recognised.**
    Symptom: `vercel deploy` succeeds locally but the deployment sits at **`Blocked`**,
    with `vercel inspect <url>` saying *"the commit author doesn't have permission to
    create deployments for this project."* Vercel identifies you by the email on the
    commit, so an address it does not know as a team member is refused. Fix the identity
    and then make a **new** commit (amending the old one is not enough, Vercel reads
    `HEAD`):

    ```powershell
    git config --global user.email "<the email GitHub has verified for you>"
    git config --global user.name  "JeedimetlaCharan704"
    git config --local --unset user.email; git config --local --unset user.name
    git log -1 --format="%an <%ae>"     # must print the corrected address
    ```

    Check the address with `gh api user | ConvertFrom-Json | Select email`. Note that
    `--no-git` is **not** a flag Vercel CLI 63 accepts — there is no way to bypass this
    from the CLI.
15. **The cold-start retry helps but is not a guarantee.** On 9 Oct the first audit after
    a deploy still returned `FAILED` once despite the retry. `FAILED` is retriable and
    the UI's re-run-audit button goes straight to `AUDITED`, so press it rather than
    reaching for a redeploy. Never demo the very first audit after a deploy without
    having one warm-up search in your pocket.
16. **A model-written demo page will skip any honesty line you only put in the prompt.**
    Both the "prepared for a seller of X" line and the opt-out are appended **after** the
    model returns. If you only ask, you will watch them get dropped in front of the jury.
17. **When classifying text, test the negatives first.** `"not interested"` contains
    `"interested"`, so a positive-first keyword order reads every refusal as enthusiasm —
    exactly what happened on 9 Oct. Same trap for "uninterested", "not a yes", and any
    `startswith`/`in` check on free text. Negatives before positives, always.
18. **A card with no evidenced gap must score 0, not a small number.** The first version
    weighted evidence strength even when there was no gap, so an empty card scored 20 and
    outranked nothing while looking meaningful. Score it 0 and say why in plain English.

---

## Deployment (Vercel)

Already proven end to end on 2026-10-09. Live URL from that run:
`https://prototype-ashy-chi-38.vercel.app`

```powershell
# from prototype/
vercel deploy --yes --prod --scope <your-team>
```

Requirements, all verified:

| Concern | Handling |
|---|---|
| Read-only FS | `settings.runtime_dir()` → `/tmp/marketpilot` when `VERCEL=1` |
| Ephemeral disk | leads/demo/outbox survive **while the instance stays warm**; a cold start wipes them. Warm it with one search before you demo. |
| Secrets | `vercel env add <NAME> production` — never a committed `.env` |
| `pyproject.toml` | **Do not add one.** Vercel runs `uv lock` on it and the build fails. Use `requirements.txt`; keep ruff in `.ruff.toml`, pytest in `pytest.ini`. |
| Function timeout | `"config": { "maxDuration": 60 }` in `vercel.json` |
| `uvicorn[standard]` | use plain `uvicorn` — `uvloop` is compiled and can fail |
| Public access | production alias serves 200 with no auth token |

**Biggest unproven risk:** OSM worked from Vercel on 9 Oct, but OSM services throttle
cloud IPs. If search fails live, `ALLOW_FALLBACK=1` is the escape hatch — it must be
visibly labelled as a cached snapshot in the UI.

---

## Judging checklist (14:45)

```powershell
.venv\Scripts\python.exe -m ruff check .    # All checks passed
.venv\Scripts\python.exe -m pytest          # 67 passed, 4 skipped
$env:RUN_LIVE='1'; .venv\Scripts\python.exe -m pytest tests\test_llm_live.py   # 4 passed
.venv\Scripts\python.exe verify_now.py      # RESULT: ALL PASS - three full chains
```

- [ ] `verify_now.py` ends `ALL PASS` (three full chains from an empty DB, refusals, persistence)
- [ ] Vercel URL opens in a private/incognito window (no auth wall)
- [ ] `/api/config` shows `llm_provider`, **`llm.transport`**, `daily_send_cap`, `opt_out`, `disclaimer`, `worker`, `discovery`
- [ ] `/api/search` returns **422** with the service missing or blank
- [ ] the lead, the demo page and the email all carry the service; the demo page also says **"not a build of that service"**
- [ ] **every evidence card shows a score and a "why it fits"**; the no-gap card scores **0**
- [ ] `/api/optout` accepts, lists and deletes an address; a send to a listed address returns **403**
- [ ] `/api/replies/poll` returns **503** (not 500) when IMAP is unset
- [ ] `/api/worker` returns **409** on Vercel and says why
- [ ] email demo link points at the URL judges can actually open
- [ ] one **cold** audit works: deploy, then immediately audit a lead
- [ ] `git log --oneline` shows only today's commits
- [ ] `git log -1 --format="%ae"` prints a recognised address, or deploys come back `Blocked`
- [ ] no secrets anywhere: `git grep -E "gsk_|AIza|AQ\.Ab8"`

## The three-minute pitch

1. **Architecture** (the jury's main question): three swappable discovery backends behind
   one interface, a deterministic four-check audit, evidence cards that cannot exist
   without an observation, one model adapter file, and a status graph that forbids
   re-contacting a business.
2. **Three inputs → real data:** what you sell (`AI chatbot development`), type `cafes`,
   area `Hyderabad` → OpenStreetMap rows → a fetched page → a named gap with its URL and
   timestamp → a working page → an email that quotes the observation back and names what
   you sell. `GET /api/config` shows the model in use, the opt-out wording and the live
   `sent_today / daily_send_cap` counter.
3. **The loop is closed, and the priority is argued, not guessed.** Every card carries a
   0–100 score from four weighted factors in `config.yaml`, plus a plain-English "why it
   fits" — so a jury can disagree with the weights by name. Inbound replies are polled,
   classified, and an opt-out becomes a permanent row that a send is **403**-refused
   against. There is no background process on Vercel: the button does one pass and
   returns, so nothing is running behind you on stage.
4. **What is deliberately *not* built:** mocks, mass mailing, follow-up sequences, any
   claim we did not observe. Opt-out is appended to every body after the model returns,
   a daily cap refuses the send with 429 rather than silently dropping it, and the
   refusal paths return 409/422 instead of guessing.
