# Shiftwise support ticket classifier

Sorts each support ticket into one of five categories (**Billing**, **Bug**, **Account Access**, **Feature Request**, **Cancellation**), with a confidence score, a `needs_human` flag and a one-line reason.

## Approach

| Piece | Choice |
|---|---|
| Classifier | Zero-shot LLM, **Claude Haiku 5.5** (`claude-haiku-5-5`), called through `claude -p` (Claude Code CLI on a subscription). Moving to the Anthropic API is a pre-live step. |
| Prompt | One definition per category, tie-break rules, a confidence rubric (≥0.9 clear, 0.7–0.9 indirect wording, <0.7 two categories fit). The ticket is framed as data to classify, never a question to answer. |
| Output | Structured via a JSON schema (`--json-schema`): `category` (enum), `confidence`, `needs_human`, `reason`. |
| `needs_human` | Model flag **OR** `confidence < 0.7`. The model flags only when unsure of the category or when the ticket reports a security/privacy problem; a routine support action (refund, closing an account, receipts) is not a reason. |
| PII | Regex masking of card numbers, emails and phone numbers *before* the call; outputs only contain the masked text. |
| Reliability | `tenacity` retries (4 attempts, exponential backoff with jitter) on transient errors only (timeouts, 429/5xx, unparseable output). A ticket that still fails is written with an empty category and `needs_human=True`; the run continues. |
| Throughput | 5 calls in parallel. |

Code: `classify.py` (classifier + `predict`), `evaluate.py` (metrics, by hand), `app/` (FastAPI). Spec: [docs/prd/2026-10-26-ticket-classifier.md](docs/prd/2026-10-26-ticket-classifier.md).

## How to run

Prerequisites: [uv](https://docs.astral.sh/uv/), Python ≥ 3.13, the `claude` CLI installed and logged in.

```bash
uv sync
uv run python classify.py evaluate                       # 40 labelled tickets -> results/eval_report.md, results/eval_predictions.csv
uv run python classify.py predict data/tickets_new.csv   # -> results/predictions_new.csv
```

Each command prints cost, tokens and wall time.

HTTP API (same classification path, PII masked, retries included):

```bash
uv run fastapi dev app/main.py   # interactive docs at http://127.0.0.1:8000/docs

curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/classify -H 'Content-Type: application/json' \
  -d '{"ticket_id": "X1", "text": "I was charged twice this month."}'
curl -X POST http://127.0.0.1:8000/classify/batch -H 'Content-Type: application/json' \
  -d '{"tickets": [{"text": "Rota page is blank"}, {"text": "Please add dark mode"}]}'
```

Tests (the `claude` call is faked, no CLI needed):

```bash
uv run pytest                                                             # unit tests
RUN_LIVE=1 uv run pytest tests/test_predict.py::test_smoke_real_claude    # one real, billed claude -p call
```

## Results

All numbers below come from the committed runs in `results/` (final prompt).

### Haiku 5.5 on the 40 labelled tickets — [results/eval_report.md](results/eval_report.md)

| Metric | Value |
|---|---|
| Accuracy vs team `label` | **90.0%** (36/40) |
| Accuracy vs `corrected_label` | **100.0%** (40/40) |
| Precision / recall, every category (vs `corrected_label`) | 1.00 / 1.00 |
| Flagged `needs_human` | 2/40 — T024 (privacy: staff see each other's pay), T025 (how-to question, conf 0.55); both correctly classified |
| Cost / tokens / time | $0.0110 / 79,165 / 26.2 s |

The 4 "errors" vs `label` are exactly the 4 tickets we relabelled (T006, T007, T019, T033; see [data notes](#what-we-noticed-in-the-data)): the model agreed with our correction every time.

Confusion matrix (rows: `corrected_label`, columns: predicted):

| | Billing | Bug | Account Access | Feature Request | Cancellation |
|---|---|---|---|---|---|
| Billing | 10 | 0 | 0 | 0 | 0 |
| Bug | 0 | 9 | 0 | 0 | 0 |
| Account Access | 0 | 0 | 8 | 0 | 0 |
| Feature Request | 0 | 0 | 0 | 8 | 0 |
| Cancellation | 0 | 0 | 0 | 0 | 5 |

### The 10 new tickets — [results/predictions_new.csv](results/predictions_new.csv)

No labels exist; the last column is my own read.

| Ticket | Text (masked) | Haiku | Conf | Flag | Sonnet | My read |
|---|---|---|---|---|---|---|
| N01 | We were billed for a location we closed in August. | Billing | 0.92 | | Billing | Billing |
| N02 | Password reset email goes to an old address I no longer have. | Account Access | 0.92 | | Account Access | Account Access |
| N03 | The rota doesn't load at all on Firefox. | Bug | 0.92 | | Bug | Bug |
| N04 | Please let staff swap shifts without manager approval. | Feature Request | 0.93 | | Feature Request | Feature Request |
| N05 | Cancel everything please, my business has been sold. | Cancellation | 0.93 | | Cancellation | Cancellation |
| N06 | Send receipts to [EMAIL], my number is [PHONE]. | Billing | 0.85 | | Billing | Billing |
| N07 | Shifts I delete keep coming back the next day. | Bug | 0.90 | | Bug | Bug |
| N08 | Can I pause our subscription over winter rather than cancel? | Billing | 0.75 | | Billing (0.62, flagged) | Billing, borders Cancellation (a retention case) |
| N09 | New starters can't create a login from the invite email. | **Bug** | 0.72 | | **Account Access** | Genuinely ambiguous: broken invite flow (Bug) vs can't get in (Account Access) |
| N10 | It'd be useful to see who's approaching overtime this week. | Feature Request | 0.40 | ✓ | Feature Request | Feature Request |

Haiku and Sonnet agree on 9/10; the only split is N09. Haiku's low confidence on N10 ("could be a how-to question") is over-cautious but harmless: it goes to a human.

The flag misses N09: the one ticket the two models disagree on, and Haiku's own reason says "it could also be read as Account Access", yet its confidence (0.72) sits just above the 0.7 threshold, so it is not flagged. N08 (0.75) is similar. A 0.8 threshold would catch both, but tuning it on these 10 tickets would leak the only unseen set; this is what the calibration step below is for.

### Haiku vs Sonnet (same final prompt)

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| 40 labelled: accuracy vs `label` / `corrected_label` | 90.0% / 100.0% | 90.0% / 100.0% |
| 40 labelled: flagged | 2 (T024, T025) | 1 (T024) |
| 10 new: agreement with the other model | 9/10 | 9/10 |
| 10 new: flagged | 1 (N10) | 1 (N08) |
| Cost, 40 + 10 tickets | $0.0139 | $0.2182 |
| Wall time, 40 tickets | 26.2 s | 36.5 s |
| **Cost per 400 tickets/day** | **≈ $0.11** (~$3/month) | ≈ $1.75 (~$52/month) |

Haiku matches Sonnet on every metric at ~1/16 of the cost, so it is the default. Costs are notional list prices reported by the CLI (the runs are on a subscription) and include ~1k tokens of `claude -p` overhead per call. Sonnet outputs: [results/sonnet/](results/sonnet/).

### Confidence threshold: kept at 0.7

Every one of the 40 labelled tickets was classified correctly by both models, so there is no confidence-vs-correctness signal to tune a threshold on: any value would separate zero errors. 0.7 stays, matching the rubric's "two categories fit" boundary. Sonnet put three correct tickets at exactly 0.70 (not flagged, since the rule is `< 0.7`); revisit once there are real errors to calibrate against.

## How it was measured

- `evaluate` runs the 40 labelled tickets through the same path as `predict` and scores against **both** the team's `label` and our `corrected_label` (precision/recall, confusion matrix and flag split use `corrected_label`). A failed ticket counts as wrong and flagged.
- **Leakage, honestly:** the prompt is zero-shot (no tickets in it), but its definitions and tie-break rules were written *after reading these 40 tickets*, and several rules target specific ones: "fewer locations → Billing" (T006), "Feature Request even when it mentions cancel" (T019), "Bug even when the symptom is a login failure caused by an outage" (T030). Near-100% on this set is therefore **optimistic**.
- The 10 new tickets were added after the prompt was written and are the unseen check. They have no labels, so the table above gives my own read and the disagreements.
- Earlier preliminary run (previous prompt): Sonnet and Haiku both scored 90% vs `label` / 100% vs `corrected_label`, with the same 4 tickets as the only "errors". But Sonnet flagged 16/40 and 7/10, all correctly classified: the flag meant "a support action is needed" (refund, closing an account), not "uncertain". The prompt now defines the flag narrowly (unsure, or security/privacy). Haiku also twice answered the ticket in its reason ("I have no access to timesheet data"), fixed by framing the ticket as data to classify.

### Alternative tried: embedding kNN (not merged)

`all-MiniLM-L6-v2` embeddings in Chroma, similarity-weighted vote of the k nearest other tickets (leave-one-out over the 40).

| k | 1 | 2 | 3 | 5 | 10 |
|---|---|---|---|---|---|
| accuracy vs `label` | 47.5% | 47.5% | 52.5% | 50.0% | 55.0% |
| accuracy vs `corrected_label` | 55.0% | 55.0% | 50.0% | 52.5% | 55.0% |

Neighbours follow topic words, not intent: "export the rota" (Feature Request) sits next to "rota page is blank" (Bug), so Bug and Feature Request swap constantly. It lives only on branch `feat/06-embedding-knn`, deliberately not merged: it pulls in torch, chromadb and sentence-transformers for a classifier we don't use. To look at it:

```bash
git switch feat/06-embedding-knn   # then read results/embedding_eval.md
```

## Before putting this live

| # | Step | Why |
|---|---|---|
| 1 | Anthropic API instead of `claude -p` | The CLI adds ~1k tokens of overhead per call, a subscription is not a production dependency, and the API gives prompt caching and proper rate limits. |
| 2 | Calibrate confidence | Self-reported confidence is uncalibrated. Calibrate on real outcomes (human overrides), or use self-consistency (agreement across several samples). |
| 3 | A proper anonymisation step | The regexes miss postal addresses and names (T027: "14 Mill Lane, Leeds LS1 4AB" reaches the model) and phones without a leading `0`/`+`. |
| 4 | A bigger, fresh test set | Hand-labelled by two people with agreement measured, never read while writing the prompt (removes the leakage above). |
| 5 | Route security/privacy reports to a human at high priority | Regardless of category (T024: staff seeing each other's pay is a Bug *and* a data breach). |
| 6 | Auth on the API | Required before it leaves localhost. |
| 7 | Monitoring | Category mix drift, flag rate, failure rate, cost; human overrides fed back as new labels. |
| 8 | Maybe multi-label | Mixed-intent tickets (T015: double charge + cancel) need two teams; one label hides one of them. |

## What we noticed in the data

**Mislabels.** Four team labels look wrong; `data/tickets_labelled.csv` keeps the original `label` and adds `corrected_label`:

| Ticket | Text | Team label | Corrected | Why |
|---|---|---|---|---|
| T007 | My login link keeps expiring before I can use it. | Billing | Account Access | A login problem, nothing about money. |
| T019 | Can you add a feature to cancel a shift swap request? | Cancellation | Feature Request | Asks for something new; "cancel" is not about leaving. |
| T033 | The schedule export to PDF cuts off the last column. | Feature Request | Bug | An existing feature is broken. |
| T006 | We're closing two sites so I need to cancel those locations. | Cancellation | Billing | Fewer locations = plan change, consistent with T040. |

**Inconsistency.** T006 ("cancel those locations") and T040 ("downgrade to fewer locations instead of cancelling") mean the same thing but were labelled Cancellation and Billing. The team needs a written rule; we chose Billing.

**Genuinely ambiguous tickets.**

| Ticket | Text | Tension |
|---|---|---|
| T015 | You charged us twice again this month, we want to cancel and get a refund. | Billing + Cancellation (mixed intent) |
| T030 | After your update yesterday nobody at our Bristol site can log in. | Account Access symptom, Bug cause |
| T039 | Our account was suspended but we paid on time. | Account Access vs Billing |
| T022 | Two-factor code never arrives by SMS. | Account Access vs Bug |
| T038 | SSO with Microsoft would help our head office team. | Feature Request vs Account Access |
| N08 | Can I pause our subscription over winter rather than cancel? | Billing vs Cancellation |
| N09 | New starters can't create a login from the invite email. | Account Access vs Bug (the models split) |

**PII in tickets.** Full card number (T005), email + phone (T012), home address (T027), email + phone (N06). The first, second and fourth are masked; the address is not.

**Size and balance.** 40 tickets, 5–10 per category: one ticket moves accuracy by 2.5 points, so the 100% above has wide error bars.

| | Billing | Bug | Account Access | Feature Request | Cancellation |
|---|---|---|---|---|---|
| team `label` | 10 | 8 | 7 | 8 | 7 |
| `corrected_label` | 10 | 9 | 8 | 8 | 5 |
