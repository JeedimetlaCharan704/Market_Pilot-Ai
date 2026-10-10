# BUILD IN CAFÉ 2026: Full Rules and Details

Compiled from the participant WhatsApp group export (18 Sep to 9 Oct 2026), the pasted announcement and the official `Hackathon_Problem_Statements.docx`.

**Source key:** [DOC] = official problem statement document. [ORG] = stated by the organizer (Mahadev Dantu) in the group chat. [POSTER] = event announcement. [UNCONFIRMED] = mentioned or proposed in chat but not confirmed as final.

---

## 1. Event overview

| Item | Details |
|---|---|
| Name | BUILD IN CAFÉ 2026 [POSTER] |
| Tagline | Code / Collaborate / Create. "Come. Eat. Develop. Win." [POSTER] |
| Organizers | Student Tribe (ticketing and jury), Bytes and Bites / Build House community [POSTER, ORG] |
| Lead organizer | Mahadev Dantu (community admin, founder of Bytes and Bites; says he is not the "organiser" himself) [ORG] |
| Date | **10 October 2026** [POSTER] |
| Venue | Mana Katha Café, Vanasthalipuram. Seating for about 300 [POSTER, ORG] |
| Time | 10 AM to 6 PM on the poster. In chat the organizer said the event "ends at 4" and **final evaluation is at 3 PM** [ORG] |
| Build time | About 5 hours (10 AM to 3 PM) [ORG] |
| Registration | Closed 8 Oct, 11:25 PM [ORG] |
| Teams | 30+ teams [POSTER]. Organizer said most teams have 4 members; solo allowed. Team-up at the venue is possible [ORG] |
| Format | In person, one merged slot (slots 1 and 2 were merged) [ORG]. Attendance is not optional or flexible [ORG] |
| Registration page | https://app.studenttribe.in/events/build-in-cafe |

### What is on offer
- Coffee, 6 hours of building, real problem statements, music and games [POSTER]
- Prizes worth ₹1,00,000+; **3 winners** (amounts "not finalized") [POSTER, ORG]
- Internship and PPO opportunities. The organizers will pick a few participants and share their data with companies [ORG]
- **Microsoft referral goes to the winner with the best architecture.** Only 3rd or 4th year students are eligible [ORG]
- ElevenLabs gives a Pro tier (about ₹87,900) for the best project built with it [ORG]
- Every participant gets a score ("which no hackathon ever did") [ORG]
- A networking game is planned [ORG]
- The exclusive pass includes food. Evaluation is identical for all pass types [ORG]

---

## 2. Schedule (as far as known)

| Time | What |
|---|---|
| 10:00 AM | Event starts. Track and PS selection is done on the day [ORG] |
| 10:00 AM to about 3:00 PM | Build time (about 5 hours) [ORG] |
| 3:00 PM | **Final evaluation.** Each team gets "ample time" to explain its project [ORG] |
| About 4:00 PM | Event ends per the organizer (poster says 6 PM) [ORG, POSTER] |

The detailed run-of-show (lunch, mentoring, game, entertainment) was not shared. During the event the organizers say they will give tips and guidance on the day. "Detailed guiding is not practically possible at the event" [ORG].

---

## 3. Tracks and problem statements [DOC]

There are **three tracks**:
1. **Validater**, Agentic theme: ProofGraph, Claim vs Evidence Agent
2. **The Marketing Agent**, Automation theme: improvise on the Google Maps Scraper Kit
3. **Open Innovation**: restaurant or café challenge, or Agentic or Automation (your choice)

### Rule for ALL tracks
> **Strictly no mocks.** Every submission must be a real, working build: no mocked data, hard-coded responses, fake integrations or simulated demos. Live APIs, real data and a working end-to-end flow only.

Organizer clarification: "Mock means faking the process. If you perform real calls, that's valid." It should not "sound like a front end project." If the website is dynamic but the backend data is dummy, that is a mock. Calling any real endpoint that takes the real parameters is valid [ORG].

---

### 3.1 Track 1: Validater (ProofGraph, Claim vs Evidence Agent). Theme: Agentic

**Problem:** Students without networks can't prove what they know. A resume is self-reported, recruiters can't verify it, and students can't see their own gaps. Skilled students get ignored and unskilled ones bluff through.

**Solution:** A multi-agent system that turns a student's LinkedIn claims into a verified skill graph, using GitHub and LeetCode as evidence, and only raises their score when they prove new learning.

**Architecture in the document:**
- **Ingestion:** OAuth for GitHub and LeetCode, plus LinkedIn via profile export or PDF upload (the LinkedIn API is restricted)
- **Claim Extractor Agent:** LinkedIn to a normalized concept list (for example "REST APIs", "Distributed Systems")
- **Code Auditor Agent:** reads repo code (AST + LLM) and tags concepts with evidence strength: auth, async, caching, DB design, tests, CI/CD
- **LeetCode Analyzer Agent:** topics, difficulty, and pattern-vs-brute-force signals
- **Verifier:** maps claims to evidence and scores each concept as Verified / Weak / Unverified. Headline example: "62% verified"
- **Gap Planner Agent:** ranks missing concepts for the target role by score impact
- **Examiner Agent:** generates quizzes from the student's own code ("why a queue in worker.py?") and grades the answers
- **Orchestrator + Automations:** webhooks re-scan on every new commit or solved problem, and the score updates automatically

**Suggested stack:** LangGraph or similar, Claude for reasoning, GitHub/LeetCode APIs, Postgres + pgvector, Next.js dashboard.

**Demo flow (3 min):** connect the three accounts; show the claim list and live "62% verified" score; click an unverified claim to see the missing evidence; take a 3-question oral quiz on your own repo; push a commit and watch the score jump.

**Business model:** students (free score, paid roadmap and re-verification); recruiters (search by verified concepts); colleges (batch placement-readiness dashboard).

**Defensibility:** the verified skill graph is a dataset nobody else has, and it improves with every student.

**Risks to address in judging:** LinkedIn data access (mention the export fallback); quiz cheating (answers tied to repo-specific context and timed).

**Organizer note on testing:** the organizers will input a real LinkedIn ID, GitHub profile link and LeetCode link to test your system live [ORG].

---

### 3.2 Track 2: The Marketing Agent (improvise on the Google Maps Scraper Kit). Theme: Automation

**Base repo:** https://github.com/Mahanaicoach/google-maps-scraper-kit. It wraps the open-source gosom/google-maps-scraper (MIT licensed). Fork or clone it to start.

**The problem:** the kit finds local business leads, but it stops at a list and needs Claude to operate it. Freelancers and small agencies still have to figure out who to pitch, what to say, and how to handle replies.

**Your challenge:** build a standalone marketing agent. The user gives only **two inputs**:
1. What's your service?
2. Who are your clients?

The agent handles the rest:
- **Find** relevant leads using the scraper
- **Understand** each business and what it needs
- **Suggest ideas, not generic pitches.** A salon doesn't get "we'll build you a website." It gets "a virtual hairstyle try-on so customers preview a cut before booking."
- **Reach out** on the user's behalf
- **Reply** to responses and keep the conversation going
- **Notify** the user when a lead is interested or needs a human decision

**Must haves:**
- Works from just the two inputs
- Model-agnostic: runs without Claude; any LLM provider or local model allowed, switchable via config
- Works end to end: leads, ideas, outreach, reply, notification
- Built on or extending the repo above

**Bonus:** learning which ideas get replies, follow-up scheduling, a dashboard, opt-out handling and send limits.

**Judging criteria (this track):**

| Criterion | Weight |
|---|---|
| Idea quality and creativity | 35% |
| Works end to end from two inputs | 30% |
| Model independence | 15% |
| Real-world usefulness | 15% |
| Code and docs | 5% |

**Rules:** follow Google's terms and anti-spam laws, **include opt-out in every message, and cap daily sends.**

**Demo:** type two sentences. Watch leads appear, ideas generate, a message go out, a reply get answered, and the user get notified.

**Pre-submission check:** before publishing, open the repo link once to confirm it is still live and that the README setup (Docker) works.

---

### 3.3 Track 3: Open Innovation

**Theme:** restaurant or café challenge, or Agentic or Automation (your choice).

Build a project of your own choosing. Pick one of two paths:
1. **Restaurant or café challenge:** solve a real problem faced by a restaurant or café
2. **Agentic or Automation theme:** choose either theme and build something of your own around it

The no-mocks rule applies.

**Organizer notes on this track [ORG]:**
- "There is no PS in open innovation." You work within the given themes.
- You lose no points for choosing it. Points depend on the approach to the solution and innovation, never on the PS.
- "Your competition is with multi-agent systems, so plan accordingly."
- The organizer was thinking of limiting how many teams can pick it (not implemented as of 8 Oct).
- Participants hoped the PS would be café-themed; the open innovation track is the café-themed one.

---

## 4. Rules and expectations (collected)

### Build rules
1. **No mocks.** Real data, real APIs, real end-to-end flow [DOC].
2. **Do not bring a finished project.** Background work and sketching are accepted; "not a fully done project." Building every possible tech in 5 hours is not practical, so sketch ahead of time [ORG].
3. **Teams that finish the whole project early get a brand-new problem statement.** "Don't fully implement the thing and try to chill at café" [ORG].
4. **A first check-point identifies people who bring a completed project.** The organizers say they can see how much code you typed by hand, how much was made with AI, and how many hours you spent on the project [ORG].
5. Track and PS selection are done on the day of the event [ORG].
6. The abstract/round-1 submission was removed. Once you paid, you are in [ORG].
7. **No PPTs.** Only a demo and an explanation [ORG].
8. **AI use is allowed.** "Yeah, kill it." Marks are the same even if AI wrote the whole thing [ORG].
9. RAG is not compulsory. It may give a better impression [ORG].
10. Teams can form at the venue; the organizer also floated assigning mixed-skill teams (back end, front end, designer) [ORG].

### Behavior rules
- Attendance is mandatory for the whole slot [ORG].
- Dress code: a colour code by track was proposed as a joke or idea. See section 7 [UNCONFIRMED].

### Group rules
- No peer-to-peer selling, voucher trading or suspicious payment links. Official hackathon posts are welcome [ORG].

---

## 5. Judging and evaluation

- **Final evaluation at 3 PM.** Each team explains its project; it is not a slideshow [ORG].
- **Architecture is the focus.** "More than the problem statement, focus on architecture." Questions will be on architecture: how tools are connected and wired, and how the setup works if it is an automation [ORG].
- The problem statements "rely most on connections, integrations and wirings, not code" [ORG].
- **Scoring depends on the approach to the solution and innovation**, not on the problem statement chosen [ORG].
- Specific questions about "which part of your code does X" are not the focus; architecture is [ORG].
- A unique tool can be a reason to win [ORG].
- All participants get a score; evaluation is the same for every pass type [ORG].
- **Jury:** Student Tribe has its own fixed jury. It includes an HR professional from FreshGradPrep. The full jury panel was announced on Instagram on 7 Oct (#BuildInCafe2026) [ORG].
- The rubric with weights is published only for **Track 2** (section 3.2). Tracks 1 and 3 have no published weights.

---

## 6. Tools and resources mentioned [ORG]

- Antigravity, Google AI Studio, Jotform, Cursor, n8n, ElevenLabs
- Any AI coding tool is fine ("anything works"; Codex needs a subscription)
- Google Gemini student offer (shared by a participant): Gemini Plus and 400 GB cloud storage free for a year for students

---

## 7. Open or unconfirmed items

- Exact prize amounts for the 3 winners (organizer: "not finalized, will update")
- Exact times for lunch, game and entertainment
- Whether the event ends at 4 PM or 6 PM
- Colour code by track (proposed in chat, never confirmed): red for agents, white for automations, black for open innovation. The organizer then joked that people who don't follow get "complicated PS"
- Whether Open Innovation will be limited
- Whether pre-building a full prototype is allowed. **Not clarified by the organizers.** See section 4, rules 2 to 4

---

## 8. Quick strategy summary

- Choose one track on the day. Track 2 has the clearest rubric (35% ideas, 30% end to end, 15% model independence, 15% usefulness, 5% code and docs).
- Be ready to explain the architecture and how every tool is connected.
- Demonstrate **real** calls only. Have a controlled test recipient for emails.
- Include opt-out in every message and a daily send cap (Track 2 rules).
- Do not arrive with a finished project. Prepare integrations, prompts and sketches instead.

---

## 9. Contacts and links

- Registration: https://app.studenttribe.in/events/build-in-cafe
- Track 2 base repo: https://github.com/Mahanaicoach/google-maps-scraper-kit
- Community Instagram: Bytes and Bites (bytess_and_bites)
- Organizer: Mahadev Dantu, community admin (+91 98493 72484). Vamsha Vardhan can help with the 2nd problem statement; Vineeth is also available to guide participants
