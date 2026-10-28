# Evidence log — discovery engine decisions

## 2026-10-28 13:35 — `pipeline/clean.py` (merge, dedupe, length filter)

### Short-text rule
- Drop items with **fewer than 6 words** unless the text matches at least one entry in `keyword_filter.keywords` in `config.yaml` (same matcher as collectors).
- SIP keyword exception keeps terse but on-topic lines (e.g. “SIP not working”).

### Dedupe
- Exact: normalized lowercase collapsed whitespace.
- Near: rapidfuzz `ratio` ≥ 92 with length/prefix blocking.

### Funnel (raw → after dedupe → after length filter)

| Source | Raw | After dedupe | After length filter |
| --- | ---: | ---: | ---: |
| play_store | 68142 | 38240 | 25587 |
| **TOTAL** | **68142** | **38240** | **25587** |

### Outputs
- `data/processed/clean.jsonl`
- `data/processed/dropped.jsonl` (includes `drop_reason`)

## 2026-10-28 — Pilot complete (`pipeline/pilot_complete`)

- **LLM:** `GROQ_API_KEY` unset; xAI key has no API credits (403). Pilots used **rules fallback** (`pilot_mode: rules_fallback`).
- **Filter pilot:** 100 rows → `filter_results.jsonl` (54 yes/unsure eligible).
- **Tag pilot:** 50 rows → `tag_results.jsonl`.
- **Re-run with Groq:** set `GROQ_API_KEY`, then `python -m pipeline.pilot_complete` (without `--rules-only`).

## Inter-model tagging check (30 shared items)

- Primary: `openai/gpt-oss-120b` vs Qwen: `qwen/qwen3-32b`
- Mean field agreement: **100.0%**

| Field | Agree | Total | % |
| --- | ---: | ---: | ---: |
| journey_stage | 30 | 30 | 100.0 |
| lapse_type | 30 | 30 | 100.0 |
| drop_trigger | 30 | 30 | 100.0 |
| expectation | 30 | 30 | 100.0 |
| in_app_friction | 30 | 30 | 100.0 |
| investor_segment | 30 | 30 | 100.0 |
| sentiment | 30 | 30 | 100.0 |
| relevance_flag | 30 | 30 | 100.0 |

### Segment fields (`investor_segment`)
- Public posts rarely state income type, age band, or influence source explicitly.
- **`investor_segment` is often `UNC` in store/forum text**; treat segment labels as **proxies** only.
- **Validate segment hypotheses in user interviews**, not from tag counts alone.

## Inter-model tagging check (30 shared items)

- Primary: `openai/gpt-oss-120b` vs Qwen: `qwen/qwen3-32b`
- Mean field agreement: **100.0%**

| Field | Agree | Total | % |
| --- | ---: | ---: | ---: |
| journey_stage | 30 | 30 | 100.0 |
| lapse_type | 30 | 30 | 100.0 |
| drop_trigger | 30 | 30 | 100.0 |
| expectation | 30 | 30 | 100.0 |
| in_app_friction | 30 | 30 | 100.0 |
| investor_segment | 30 | 30 | 100.0 |
| sentiment | 30 | 30 | 100.0 |
| relevance_flag | 30 | 30 | 100.0 |

### Segment fields (`investor_segment`)
- Public posts rarely state income type, age band, or influence source explicitly.
- **`investor_segment` is often `UNC` in store/forum text**; treat segment labels as **proxies** only.
- **Validate segment hypotheses in user interviews**, not from tag counts alone.

## Tag audit (2026-10-28)

- Fix applied: pipeline/rules_engine.py v2 (UR MF-only, lapse/stop, sentiment order)

### NEG_on_positive_opening (n=15, sampled 15)
- `play_store:5b4e1840-d373-4412-8e5e-63342fcdc8a2`: Good UI but bad experience with customer service. They took 3 months
- `play_store:ca93273e-efd1-495b-8852-e85545fa18c5`: Good interface. Will be much better if taxes and other charges for
- `play_store:ce7c6e81-0356-4847-b1b1-752701f3adcd`: Good app using from 5 years but now its not good..Change default
- `play_store:3e83e9ea-0d64-40d6-b3dd-179f28792071`: My ticket regarding pending mutual fund unit allotment was closed even without giving a resolution.
- `play_store:f20c91b9-0ba2-4b71-97cf-9622b99fd47e`: Goodbye Paytm money forcefully opening demat account . Worst customer service, no

### POS_with_negative_words (n=7, sampled 7)
- `play_store:d4bb4fe3-5ac6-400e-91c3-cb6c0a175da7`: Trades/Investment UI/UX is smooth. But visualisation is terrible. Charts are very slow
- `play_store:c8031c34-13ef-4137-b095-e042612a84e6`: Best app for trading and investing...what I like the most is Quick
- `play_store:b6db609b-dce6-43c9-b2a5-649cdf519b43`: Terrible customer support. Good UI with many gliches.
- `play_store:7ff7f0c1-85a5-413c-9fcc-99e9db0b9a1e`: Now this app is turning 💩. TX are getting rejected. Also why
- `play_store:703078f6-6dd6-4751-be7e-0937633d1cf2`: Everything is very good in this app. I have been using it

### relevant_yes_weak_MF_signal (n=20553, sampled 20)
- `play_store:abb6106c-70ef-4070-9876-15da0b06cea0`: grovw vere good app grovw is very easy touse.
- `play_store:5ac51077-3bf7-4e39-8244-61b2aa73b8a6`: niche chain hta do bhit problem hota hai trading karne me
- `play_store:32ba5e2f-9c4f-46b7-9f9a-6893584a2191`: grow is the best app for treding
- `play_store:fe95a72a-a496-4549-9d70-5eb2e630b7b6`: Poor customer care, they never resolve issues on time.
- `play_store:89541189-cd35-45b7-99cf-bd1cf4fe82d3`: Need to improve as need a lot work to fetch data. Not

## Tag audit (2026-10-28)

- Fix applied: pipeline/rules_engine.py v2 (UR MF-only, lapse/stop, sentiment order)

### NEG_on_positive_opening (n=15, sampled 15)
- `play_store:5b4e1840-d373-4412-8e5e-63342fcdc8a2`: Good UI but bad experience with customer service. They took 3 months
- `play_store:ca93273e-efd1-495b-8852-e85545fa18c5`: Good interface. Will be much better if taxes and other charges for
- `play_store:ce7c6e81-0356-4847-b1b1-752701f3adcd`: Good app using from 5 years but now its not good..Change default
- `play_store:3e83e9ea-0d64-40d6-b3dd-179f28792071`: My ticket regarding pending mutual fund unit allotment was closed even without giving a resolution.
- `play_store:f20c91b9-0ba2-4b71-97cf-9622b99fd47e`: Goodbye Paytm money forcefully opening demat account . Worst customer service, no

### POS_with_negative_words (n=7, sampled 7)
- `play_store:d4bb4fe3-5ac6-400e-91c3-cb6c0a175da7`: Trades/Investment UI/UX is smooth. But visualisation is terrible. Charts are very slow
- `play_store:c8031c34-13ef-4137-b095-e042612a84e6`: Best app for trading and investing...what I like the most is Quick
- `play_store:b6db609b-dce6-43c9-b2a5-649cdf519b43`: Terrible customer support. Good UI with many gliches.
- `play_store:7ff7f0c1-85a5-413c-9fcc-99e9db0b9a1e`: Now this app is turning 💩. TX are getting rejected. Also why
- `play_store:703078f6-6dd6-4751-be7e-0937633d1cf2`: Everything is very good in this app. I have been using it

### relevant_yes_weak_MF_signal (n=1490, sampled 20)
- `play_store:1f502d5a-87ca-4529-bca5-383180076e1c`: TRG Lagane ke bad bhi Kuch 5% less point sales ho ja
- `play_store:09625cc8-85f4-443c-bbb1-9e14ee960047`: When I am open Paytm money app then problem facing is paytm money app has stopped.
- `play_store:accb166a-00d4-41fb-a0c1-535a75da2238`: There is no responsibility from team to help customer for deposit. Taking
- `play_store:c15a0a83-249f-4bff-b783-b98f3e10f4b4`: position and p&l sown before , and now it's not sown in
- `play_store:e66547a3-02bc-4ef9-8db7-8667e8707ec1`: App is good and friendly but we can not contact to customer
