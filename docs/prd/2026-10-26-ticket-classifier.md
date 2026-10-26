# Shiftwise support ticket classifier — v1

## Problem

Shiftwise support gets ~400 tickets a day and sorts them by hand.
v1 sorts each ticket automatically into one of five categories: **Billing**, **Bug**, **Account Access**, **Feature Request**, **Cancellation**.

## Inputs

- `data/tickets_labelled.csv` — 40 tickets hand-sorted by the support team (`ticket_id,text,label`).
- `data/tickets_new.csv` — 10 unsorted tickets (`ticket_id,text`), added by the user at the end so the prompt is not tuned on them.

## Deliverables

1. A prediction per ticket: one category, a confidence score, a `needs_human` flag and a one-line reason.
2. An evaluation on the labelled tickets: accuracy plus where it goes wrong, and how it was measured.
3. A CSV of predictions for the 10 new tickets.
4. A README: how to run, results, what to do before going live, what we noticed in the data.

## Decisions

### Model and transport

- Python, `uv` project.
- Calls Claude **Sonnet 5.5** through the `claude -p` CLI (current subscription), not the Anthropic API.
  Switching to the API is a later step if time allows.
- Structured output via `--json-schema`; the result is read from `structured_output` in `--output-format json`.
- Each call is isolated: `--tools ""`, `--setting-sources ""`, `--no-session-persistence`, our own `--system-prompt`, run from a scratch working directory, so no repo instructions, hooks or plugins leak into the classifier.
- The ticket text is sent on stdin, not argv, so text starting with `-` is never read as a flag.
- Calls run 5 in parallel.
- Each call has a 120 s timeout and is retried with `tenacity`: up to 4 attempts, exponential backoff with jitter (1 s initial, 30 s max).
  Retried: timeout, empty or unparseable stdout, missing or incomplete `structured_output`, and `is_error` responses with `api_error_status` 429 or 5xx (including 529 overloaded) or a clearly transient message (rate limit, overloaded, network, connection).
  Not retried (fail on the first attempt): other `is_error` responses (auth, invalid model, 4xx) and a non-zero exit with a non-transient error; programming errors are never retried.
  On a non-zero exit the JSON on stdout is read first, stderr is the fallback.
  A ticket that fails its last attempt gets an empty category, `needs_human=True` and the error in `reason`; the run continues.
  An unexpected error on one ticket (e.g. a malformed response) is not retried: that ticket gets a failed row with a generic reason, the raw error goes to stderr, and the run continues.
  Transience is decided by `api_error_status` when present; text markers apply only when there is no status or the error comes from stderr.
  The printed cost and tokens include every attempt that returned JSON, retries and failures too.
- Measured on one call: ~3.7 s, ~$0.0054 list price; ~$0.27 for all 50 tickets.

### Prompt

- **Zero-shot**: the labelled tickets are never put in the prompt, so evaluating on them is not leaked.
  Caveat for the README: the definitions were written after reading the 40 tickets, a mild leak a fresh labelled set would remove.
- One written definition per category, plus tie-break rules:
  - intent to leave the service entirely, or stop renewal → **Cancellation**;
  - fewer locations / downgrade / plan change → **Billing**;
  - something that exists but is broken or wrong → **Bug**, even when the symptom is a login failure caused by an outage;
  - asking for something that does not exist yet → **Feature Request**, even when it mentions "cancel" or login;
  - mixed intent → the single most appropriate category (one label only; multi-label is a possible later step).

### Output schema (model)

| field | type | meaning |
|---|---|---|
| `category` | enum of the five categories | the chosen category |
| `confidence` | number 0–1 | anchored by the rubric below |
| `needs_human` | boolean | model's own judgement that a person should look |
| `reason` | string | one line explaining the choice |

Confidence rubric given in the prompt:

- **≥ 0.9** — one clear intent that matches a definition;
- **0.7–0.9** — the category is clear but the wording is indirect;
- **< 0.7** — two categories fit, or there is not enough information.

Final flag: `needs_human = model needs_human OR confidence < 0.7`.
The 0.7 threshold is tuned after the first evaluation run, from how confidence lines up with correctness.

### PII

- `mask_pii` replaces card numbers (13–19 digits, space or dash separators) with `[CARD]`, email addresses with `[EMAIL]`, and phone numbers starting with `0` or `+` (9–14 digits, e.g. `07700 900123`, `+44 …`) with `[PHONE]`.
- It runs inside `run_classification`, so every caller sends only masked text to the model; `classify()` returns it as `text_redacted`.
- The masked text is what is written to `text_redacted` in output CSVs, FAILED rows included.
- Plain small numbers ("14 locations", "3 attempts") are left alone; a phone number with neither a leading `0` nor `+` is missed.
- Known gap, stated in the README: postal addresses and names are not caught; a proper anonymization step is required before going live.

### Labels

- `label` keeps the support team's original labels (T007 restored to Billing).
- A new `corrected_label` column holds our corrections:

| ticket | text | original | corrected | why |
|---|---|---|---|---|
| T007 | login link keeps expiring | Billing | Account Access | a login problem, nothing about money |
| T019 | add a feature to cancel a shift swap request | Cancellation | Feature Request | asks for something new; "cancel" is not about leaving |
| T033 | PDF export cuts off the last column | Feature Request | Bug | an existing feature is broken |
| T006 | closing two sites, cancel those locations | Cancellation | Billing | fewer locations = plan change, consistent with T040 |

### Evaluation

`evaluate` runs the classifier on the 40 labelled tickets and reports:

- accuracy against `label` and against `corrected_label`;
- precision and recall per category;
- confusion matrix;
- every misclassified ticket (wrong against `label` or `corrected_label`) with masked text, both labels, predicted, confidence, reason;
- how confidence lines up with correctness (accuracy of flagged vs not-flagged tickets, how many flagged);
- total cost (sum of `total_cost_usd` over every attempt, retries and failed tickets included; notional list price on a subscription), tokens and wall time.

Precision/recall, the confusion matrix and the flagged split are measured against `corrected_label`.
A ticket whose classification fails counts as wrong and flagged, and shows as `failed` in the confusion matrix.
It reuses `predict`'s classification path and parallel pool.

Written to `results/eval_report.md` and `results/eval_predictions.csv`.
Metrics are computed by hand, no sklearn.

### Prediction

`predict` reads `data/tickets_new.csv` and writes `results/predictions_new.csv`:

`ticket_id, text_redacted, predicted_category, confidence, needs_human, reason`

### Code and tests

- Single module `classify.py` with two commands, `evaluate` and `predict`, plus the `app/` package for the HTTP API.
- TDD with pytest; tests replace the `claude -p` call with a fake, so they run without the CLI.
  Covered: PII masking, parsing the CLI JSON, the `needs_human` rule, the metrics, CSV output.
- One real smoke test against `claude -p`; it is billed, so it runs only with `RUN_LIVE=1` and `claude` on PATH.
- Dependencies: `tenacity`, `fastapi[standard]`; `pytest` (dev).

## HTTP API

A FastAPI service exposes the same classification path as `predict` (`app/`): every ticket goes through `run_classification`, so PII is masked before the model sees it and transient failures are retried.
Run with `uv run fastapi dev app/main.py`; interactive docs at `/docs`.

- `GET /health` → `{"status": "ok"}`.
- `POST /classify` `{ticket_id?, text}` → `{ticket_id, category, confidence, needs_human, reason}`; the ticket text, masked or not, is never returned.
  Text is 1–5000 characters, otherwise 422.
  A classification failure returns 502 with a generic message; the raw error is only logged.
- `POST /classify/batch` `{tickets: [...]}` (1–100 tickets) → `{predictions: [...]}`, in input order, classified 5 in parallel by `classify_all`, the same pool as `predict`.
  A failed ticket comes back with `category` and `confidence` null, `needs_human=true` and reason "classification failed"; the batch still succeeds.
- Endpoints are plain `def`, so the blocking `claude -p` call runs in FastAPI's threadpool.
- The `claude -p` call is injected with `Depends(get_call)`; tests override it with a fake, so masking, retries and the `needs_human` rule still run.
- Out of scope: auth (required before exposing beyond localhost), a settings module, Docker.

## README content

- How to run (`uv run …` for `evaluate`, `predict`, tests).
- Results: both accuracy numbers, confusion matrix, errors, flagged rate, cost.
- Before going live:
  - move from `claude -p` to the Anthropic API;
  - calibrate confidence against real outcomes, or use agreement across several samples (self-consistency);
  - add a proper anonymization step (addresses, names) before any text leaves our systems;
  - a larger, freshly hand-labelled test set, with labels agreed between two people;
  - route security/privacy reports (e.g. T024, staff seeing each other's pay) to a human with high priority, whatever the category;
  - monitoring: category mix drift, flagged rate, human overrides fed back as labels.
- What we noticed in the data:
  - mislabelled tickets (table above) and the T006/T040 inconsistency;
  - genuinely ambiguous tickets: T015 (double charge + cancel), T030 (login failure caused by an outage), T039 (suspended but paid), T022 (2FA SMS), T038 (SSO request);
  - PII in tickets: full card number (T005), email and phone (T012), home address (T027);
  - tiny dataset: 7–10 tickets per category, one error moves accuracy by 2.5 points.

## Out of scope for v1

- Multi-label output.
- Few-shot prompting, local models, classic ML.
- Anthropic API transport.
