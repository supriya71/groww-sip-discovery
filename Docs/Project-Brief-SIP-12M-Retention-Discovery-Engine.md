# Project Brief: AI-Powered Discovery Engine for First-Time SIP Retention

**Product area:** Mutual funds — first-time SIP investors (India)  
**North-star metric:** % of first-time MF investors with an **active SIP 12 months** after starting their first one  
**Active SIP definition:** ≥1 successful instalment in the last 30 days  
**Phase:** Discovery (problem identification) — **no solution design in this brief**  
**Solution constraints (for later phases):** No specific fund/stock recommendations; no fear, FOMO, or urgency-based nudges  
**Document purpose:** Single briefing to align PM, research, data, design, and engineering on **how** we discover the underlying user problem at scale  
**Status:** Draft for execution  
**Last updated:** 28 Oct 2026

---

## 1. Executive summary

Starting a SIP is friction-light; **staying invested for 12 months** is not. The business cares about active SIPs at month 12 because retained investors compound platform value and actually capture long-horizon benefits. Leadership has **not** handed us a validated user problem—only the metric and guardrails.

This project builds an **AI-powered discovery engine** that ingests **public, conversational evidence** (reviews, forums, Reddit, social, YouTube comments, etc.), structures it into testable insight themes, **quantifies** patterns where possible, and **ranks opportunity areas** that could plausibly move 12-month active-SIP rate—without jumping to product fixes or prohibited nudge tactics.

**Deliverable of discovery phase:** A ranked **Opportunity Map** (problems × segments × evidence strength × hypothesized metric lever) plus a **Research Backlog** for qual validation and internal data joins.

---

## 2. Problem framing (what we know vs. what we must discover)

### 2.1 Known (given)

| Item | Detail |
| --- | --- |
| Strategic goal | Increase % first-time MF investors still on an active SIP at month 12 |
| Active SIP | ≥1 successful instalment in last 30 days |
| Drop-off window | Months 1–12 after first SIP start; behaviour often **quiet** (pause, reduce, lapse) |
| Solution guardrails | No fund/stock picks; no fear / FOMO / urgency nudges |

### 2.2 Unknown (discovery targets)

We must infer **why people start**, **what they expect**, **how behaviour and attention change over months**, **what triggers pause/stop**, **what they check and how often**, **in-app confusion vs. out-of-app coping**, and **what differs between continuers and silent drop-offs**—including segment splits (income stability, age, salary vs. other money, influence source).

### 2.3 Hypothesis space (non-binding)

These are **starting lenses**, not conclusions:

- **Expectation gap:** Early statements feel “flat”; users interpret slow early growth as failure.
- **Attention decay:** SIP succeeds operationally but disappears from mental model until a negative trigger (market dip, cash crunch, life event).
- **Trust / comprehension:** Mandates, NAV, units, tax, and “did my money go?” anxiety drive checking spirals or avoidance.
- **Lifecycle interference:** Irregular income, EMIs, or family pressure makes fixed debits feel rigid.
- **Social comparison:** Peers or influencers set implicit return timelines incompatible with SIP reality.
- **Platform friction:** Pause, step-up, modify, or support paths are harder to find than start.

Discovery must **confirm, reject, or refine** these with evidence—not anecdotes.

---

## 3. Discovery questions (research backbone)

Each question maps to **collection queries**, **taxonomy codes**, and **downstream quant metrics**.

| # | Question | Primary signals | Quant where possible |
| --- | --- | --- | --- |
| Q1 | Why do people start a SIP, and what do they expect? | Start motivations, goal language, time horizons, “passive wealth” narratives | Theme frequency; co-mention with fund category; expectation keywords (1y vs 10y) |
| Q2 | What changes in behaviour in months after starting? | Check frequency, statement obsession, app open patterns described in text | Temporal phrases (month 2–6); behaviour verb clusters |
| Q3 | What leads to pause, reduce, or stop? | Explicit reasons: cash, fear, boredom, “not working”, support failures | Reason taxonomy counts; severity (pause vs cancel) |
| Q4 | What do investors check, and how often? | NAV, returns, statements, XIRR mentions, portfolio vs SIP amount | Check-type frequency; anxiety vs curiosity tone |
| Q5 | What confuses or worries them **inside the app**? | UI/flow complaints, missing info, notification issues | App-specific entity tagging; platform comparison |
| Q6 | What do they do **outside the app** when unsure? | YouTube, CA, friends, Twitter, Reddit “ask” threads | Out-of-app channel share; question types |
| Q7 | What separates continuers from quiet drop-offs? | Long-horizon framing, habit language, auto-debit comfort | Contrasting cohort language (self-identified) |
| Q8 | How do patterns differ by segment? | Income type, age, student/first job, influencer-led vs self-serve | Segment tags when stated; proxy tags (language, platform) |
| Q9 | What unmet needs repeat across sources? | “I wish…”, “there should be…”, workarounds | Cross-source theme stability score |

---

## 4. Evidence sources (public corpus)

### 4.1 Tier A — High volume, structured metadata

| Source | Access | Value | Caveats |
| --- | --- | --- | --- |
| Google Play Store reviews | API / scrape (ToS-compliant) | In-app pain, updates, SIP UX | Selection bias; rage reviews |
| Apple App Store reviews | API / scrape | Same | Lower volume in India vs Play |
| App review replies (if public) | Same | Official framing of issues | Marketing tone |

### 4.2 Tier B — Narrative-rich, thread structure

| Source | Examples | Value |
| --- | --- | --- |
| Reddit | r/IndiaInvestments, r/personalfinanceindia, platform-specific subs | Long-form journeys, pause/stop stories |
| Forums | TradingQ&A, ValueResearch forums, BharatInvestors (where public) | Detailed “why I stopped” |
| Twitter / X | #SIP, #mutualfunds, influencer threads | Real-time reactions to market moves |
| YouTube | Comments on SIP explainer / “my first SIP” vlogs | Beginner expectations, confusion |
| LinkedIn / Instagram | Public posts (hashtag-led samples) | Career-stage narratives |
| Quora | SIP / mutual fund topics | First-time investor FAQs |

### 4.3 Tier C — Context and benchmarks

| Source | Use |
| --- | --- |
| RBI / AMFI public education material | Baseline “official” narrative vs user mental models |
| Competitor app reviews (named platforms) | Category-level UX gaps, not fund advice |
| News comments (economic times, mint) | Macro fear triggers |

### 4.4 Out of scope for **this** corpus

- Authenticated in-app behavioural logs (separate **internal join** workstream)
- PII harvesting (names, phone, account IDs in scraped text must be redacted)
- Private groups / paid Telegram without legal clearance
- Content used to recommend specific securities (forbidden in later solution anyway)

---

## 5. AI-powered discovery engine — conceptual architecture

The engine is **not** a summary bot. It is a **repeatable pipeline** from raw text → structured claims → theme metrics → opportunity scoring → human review.

```
┌─────────────────────────────────────────────────────────────────┐
│  SOURCE CONNECTORS (scheduled ingest + manual backfill)         │
│  Play/App stores · Reddit · forums · YT comments · social       │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  NORMALIZE & DEDUPE                                             │
│  lang detect · dedupe near-duplicates · timestamp · source id   │
│  PII redaction · ToS / robots compliance flags                  │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENRICH (LLM + rules hybrid)                                    │
│  SIP journey stage · actor segment · sentiment (multi-axis)     │
│  taxonomy labels · expectation type · drop-off reason candidates│
│  in-app vs out-of-app · continuer vs drop-off (if stated)       │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STRUCTURED STORE + VECTOR INDEX                                │
│  rows: one utterance / review / comment + labels + embeddings   │
│  enables semantic search + aggregate SQL-style cuts               │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  ANALYSIS LAYER (beyond sentiment)                              │
│  theme prevalence · co-occurrence · source divergence           │
│  temporal drift · segment crosstabs · exemplar quotes (linked)  │
│  opportunity scoring · stability across refresh cycles          │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PM ARTIFACTS                                                   │
│  Opportunity Map · Problem Statements · Validation backlog      │
└─────────────────────────────────────────────────────────────────┘
```

### 5.1 Why hybrid LLM + rules

| Approach | Role |
| --- | --- |
| Rules / regex | Dates, “paused SIP”, “cancelled”, rupee amounts, month mentions |
| Classifier (fine-tuned or prompted with schema) | Multi-label taxonomy (see §6) |
| Embeddings | Cluster emergent themes; find gaps not in taxonomy |
| LLM synthesis | **Only on aggregated stats + sampled quotes**—never single-comment product decisions |

### 5.2 Multi-axis “sentiment” (not one score)

Replace naive positive/negative with axes relevant to retention research:

| Axis | Example poles |
| --- | --- |
| Expectation alignment | Realistic long-term ↔ disappointed early |
| Self-efficacy | “I understand” ↔ “I’m lost” |
| Trust in platform | Reliable ↔ suspicious |
| Financial stress | Stable ↔ cash-crunch |
| Engagement | Habit / autopilot ↔ forgotten |
| Intent | Continue ↔ pause ↔ exit |

### 5.3 Journey staging (for utterances that imply time)

| Stage | Description | Typical language |
| --- | --- | --- |
| S0 Pre-start | Research, comparison | “Which app”, “minimum SIP” |
| S1 Start | First mandate, excitement | “Started 500/month” |
| S2 Early (1–3 mo) | First statements | “Only gained 20 rupees” |
| S3 Mid (4–8 mo) | Routine or doubt | “Forgot”, “market down” |
| S4 Late (9–12 mo) | Continue vs lapse decision | “Stopping”, “step-up” |
| S5 Post-lapse | Regret or relief | “Should I restart” |

Utterances without explicit timing still get **S?** unknown; aggregate with care.

---

## 6. Taxonomy v0 (living schema)

All enriched rows should support **multi-label** assignment with confidence. Version taxonomy in git; re-run enrichment on schema bumps.

### 6.1 Start motivations (Q1)

`GOAL_RETIREMENT`, `GOAL_TAX_80C`, `GOAL_WEALTH`, `GOAL_LEARN`, `PEER_INFLUENCE`, `INFLUENCER`, `APP_EASE`, `FOMO_MARKET` (document as **observed user language**, not something we will exploit in product)

### 6.2 Expectation types

`EXPECT_PASSIVE`, `EXPECT_QUICK_GROWTH`, `EXPECT_BEAT_FD`, `EXPECT_SIP_GUARANTEE`, `EXPECT_MARKET_TIMING`, `EXPECT_UNKNOWN`

### 6.3 Drop-off / pause drivers (Q3)

`REASON_CASH_FLOW`, `REASON_MARKET_FEAR`, `REASON_UNDERWHELMING_RETURNS`, `REASON_COMPLEXITY`, `REASON_LIFE_EVENT`, `REASON_GOAL_ACHIEVED`, `REASON_SWITCHED_PLATFORM`, `REASON_BAD_SUPPORT`, `REASON_MANDATE_FAIL`, `REASON_UNKNOWN`

### 6.4 In-app friction (Q5)

`FRICTION_NAV_UNITS`, `FRICTION_STATEMENT`, `FRICTION_PAUSE_MODIFY`, `FRICTION_NOTIFICATIONS`, `FRICTION_KYC_PAYMENT`, `FRICTION_PERFORMANCE_DISPLAY`, `FRICTION_COMPARISON_UI`

### 6.5 Out-of-app behaviour (Q6)

`SEEK_YOUTUBE`, `SEEK_REDDIT`, `SEEK_CA`, `SEEK_FRIEND`, `SEEK_BANK`, `SEEK_NEWS`

### 6.6 Segment tags (when inferable from text—low confidence flag)

`SEG_SALARIED`, `SEG_GIG`, `SEG_STUDENT`, `SEG_YOUNG`, `SEG_FIRST_JOB`, `SEG_FAMILYPressure`, `SEG_SELF_DIRECTED`, `SEG_INFLUENCER_LED`

---

## 7. Workflow: from text to opportunity areas

### Step 1 — Corpus definition sprint (Week 1)

- Freeze **source list**, refresh cadence (e.g. weekly stores, daily Reddit sample).
- Legal/compliance check on scraping and storage.
- Define **platform scope** (single app vs category)—brief assumes **category + named apps in reviews** for competitive UX insight, not fund picks.

### Step 2 — Ingest & baseline (Weeks 1–2)

- Backfill **6–12 months** of reviews and forum posts where APIs allow.
- Publish **corpus stats**: volume by source, language mix, SIP-keyword precision/recall on manual sample.

### Step 3 — Enrichment at scale (Weeks 2–4)

- Run taxonomy labelling with **inter-rater sample**: PM + researcher manually label 300–500 rows; measure precision/recall; tune prompts/rules.
- Build embedding clusters; name clusters with human approval (avoid LLM-only cluster names).

### Step 4 — Quantified theme dashboard (Weeks 3–5)

For each theme **T**:

| Metric | Definition |
| --- | --- |
| Prevalence | % of SIP-relevant rows mentioning T |
| Growth | Prevalence delta last 90d vs prior 90d |
| Source mix | Which sources over-index on T |
| Stage skew | Over-representation in S2 vs S4 |
| Segment skew | Over-index vs baseline for SEG_* |
| Continuer contrast | Odds ratio vs self-identified continuer language (noisy but directional) |
| Co-occurrence | Top pairs (e.g. `EXPECT_QUICK_GROWTH` + `REASON_UNDERWHELMING_RETURNS`) |

### Step 5 — Opportunity scoring (Week 5)

Score each **problem hypothesis** (not solutions):

| Factor | Weight (tunable) | Notes |
| --- | --- | --- |
| Evidence volume | Medium | Enough quotes across sources |
| Cross-source stability | High | Same theme on Play + Reddit + YT |
| Stage alignment | High | Peaks in months 1–12 journey |
| Actionability under guardrails | High | Can address without fund rec or fear nudges |
| Metric plausibility | Medium | Narrative link to “still active at 12m” |
| Segment strategic value | Medium | First-time investors priority |

Output: **Opportunity Map** (2×2: evidence strength × hypothesized impact on 12m active SIP).

### Step 6 — Problem statements + validation backlog (Week 6)

For top 5–8 opportunities, write:

- **Problem statement** (user-centric, no solution verbs)
- **Evidence pack** (10–20 anchored quotes with source links)
- **Open questions** for interviews and internal analytics
- **Suggested metric proxy** for later A/B (e.g. pause rate within 90d—not committed here)

---

## 8. Human-in-the-loop governance

| Control | Requirement |
| --- | --- |
| Quote integrity | Every synthesized claim links to stored source id |
| No single-post decisions | Min cluster size or min independent authors for “theme confirmed” |
| Bias disclosure | Play reviews ≠ all users; Reddit skews male/urban/English |
| Language | Hindi/Hinglish pipelines; do not English-only discover |
| Prohibited outputs | Engine must not output fund recommendations or urgency copy suggestions |
| Refresh | Re-run enrichment monthly; track theme drift |

---

## 9. Integration with internal data (parallel track)

Public discovery **cannot** alone prove drop-off rates. Plan a **join path** (separate data request):

| Public insight | Internal validation |
| --- | --- |
| “Mandate failed silently” | Payment failure logs, retry success |
| “Stopped checking app” | Session frequency cohorts |
| “Paused after first red statement” | Cohort by first negative P&L display |
| Segment tags | CRM / KYC age band, income declaration (aggregated) |

Discovery engine exports **theme ids**; analytics exports **cohort metrics**—convergence meeting decides promoted problems.

---

## 10. Tooling options (implementation-agnostic)

Can reuse patterns from existing repo RAG prototype (`loading → chunking → embedding → vector store → retrieval`) **with a different corpus and enrichment schema**:

| Layer | Option A (lean) | Option B (scale) |
| --- | --- | --- |
| Ingest | Python scripts + store APIs | Airbyte / custom connectors |
| Store | Postgres + pgvector or Chroma | Warehouse (BigQuery) + object storage |
| Label | Batch LLM with JSON schema | Human labelling tool (Label Studio) |
| Dashboard | Streamlit / Metabase | Looker |
| Search | Semantic + filters | Same |

**Model policy:** Document model versions; reproducible enrichment runs; store raw text immutable, labels versioned.

---

## 11. Success criteria (discovery phase)

| Criterion | Target |
| --- | --- |
| Corpus coverage | ≥3 Tier-A/B source families live |
| SIP relevance | ≥5,000 SIP-relevant enriched utterances (adjust after keyword calibration) |
| Taxonomy quality | ≥0.75 precision on manual audit sample for top 10 labels |
| Opportunity Map | 5–8 ranked problems with cross-source evidence |
| Segment cuts | ≥3 segment hypotheses with directional data |
| Guardrails | Zero fund recommendations in engine outputs; documented nudge policy for phase 2 |
| Handoff | Validation backlog sized for 15–20 user interviews |

---

## 12. Phasing and roles

| Phase | Duration | Output | Owner |
| --- | --- | --- | --- |
| D0 Brief approval | 1 week | This document signed off | PM |
| D1 Corpus + ingest | 2 weeks | Raw corpus v1 | Eng / data |
| D2 Enrichment v1 | 2 weeks | Labelled store + audit report | Eng + PM |
| D3 Analysis | 2 weeks | Dashboard + theme metrics | PM + research |
| D4 Opportunity Map | 1 week | Ranked problems + quotes | PM |
| D5 Validation plan | 1 week | Interview guide + analytics asks | PM + UX research |

**RACI snapshot:** PM owns questions and Opportunity Map; Eng owns pipeline; Legal owns ToS; Research owns qual validation; Analytics owns internal joins.

---

## 13. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Scraping blocked / ToS | Prioritize official APIs; manual exports for forums |
| LLM label drift | Frozen prompts + periodic audit sample |
| False “insights” from bots/spam | Author dedupe, account age heuristics |
| Overfitting to vocal minority | Weight by source diversity; internal rate validation |
| Premature solutioning | Gate: no feature specs until Opportunity Map approved |
| Regulatory sensitivity | No personalised advice in any public demo of the engine |

---

## 14. Appendix A — Example analyst prompts (discovery-only)

Use against **aggregated** exports, not raw firehose:

1. “List themes where `REASON_UNDERWHELMING_RETURNS` co-occurs with journey stage S2; show prevalence by source.”
2. “Compare language axes for rows mentioning ‘pause’ vs ‘cancel’.”
3. “Which `FRICTION_*` labels grew fastest in the last 90 days on Play Store?”
4. “Find emergent embedding clusters not mapped to taxonomy v0; top 5 with 15 exemplar ids each.”
5. “Where do users describe checking behaviour weekly vs never—what else co-occurs?”

---

## 15. Appendix B — Opportunity Map template (fill after D4)

| Rank | Problem statement | Primary journey stage | Top taxonomy labels | Prevalence | Sources | Segment skew | Hypothesized link to 12m active SIP | Evidence strength (1–5) | Validation next step |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | TBD | | | | | | | | |

---

## 16. Appendix C — Glossary

| Term | Meaning |
| --- | --- |
| SIP | Systematic Investment Plan — recurring mutual fund investment |
| Active SIP (metric) | ≥1 successful instalment in last 30 days |
| First-time MF investor | User’s first mutual fund SIP on platform (define precisely in analytics) |
| Discovery engine | Ingest + enrich + quantify + rank **problems**, not product features |
| Opportunity area | A validated user problem cluster worth solving under guardrails |

---

## 17. Next steps after this brief

1. Stakeholder sign-off on sources, taxonomy v0, and scoring weights.  
2. Stand up ingest for Play Store + one forum + Reddit (pilot scope).  
3. Manual labelling sprint to calibrate enrichment.  
4. Run first full enrichment pass and publish **Corpus v1 Report**.  
5. Build Opportunity Map v1; schedule validation interviews before any solution PRD.

---

*This brief intentionally stops at problem discovery. Solution design must respect: no specific fund/stock recommendations; no fear, FOMO, or urgency-based nudges.*
