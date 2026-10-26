import csv
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

MODEL = "claude-sonnet-5-5"
CATEGORIES = ["Billing", "Bug", "Account Access", "Feature Request", "Cancellation"]
CONFIDENCE_THRESHOLD = 0.7
PARALLEL_CALLS = 5
CALL_TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 4
REQUIRED_COLUMNS = {"ticket_id", "text"}
TRANSIENT_MARKERS = (
    "429",
    "rate limit",
    "overloaded",
    "unavailable",
    "network",
    "connection",
    "timed out",
    "timeout",
    "econn",
)
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
BACKOFF = wait_exponential_jitter(initial=1, max=30, jitter=1)
DEFAULT_OUTPUT = Path("results/predictions_new.csv")
PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[CARD]"),
    (re.compile(r"(?<!\w)(?:\+|0)\d(?:[ -]?\d){8,13}(?!\w)"), "[PHONE]"),
]
OUTPUT_COLUMNS = [
    "ticket_id",
    "text_redacted",
    "predicted_category",
    "confidence",
    "needs_human",
    "reason",
]

SYSTEM_PROMPT = """You classify Shiftwise support tickets into exactly one category.

Categories:
- Billing: charges, refunds, invoices, payment methods, pricing, plan changes, downgrades, fewer locations.
- Bug: something that exists but is broken or behaves wrongly.
- Account Access: logging in, passwords, two-factor codes, locked or suspended accounts, permissions to get in.
- Feature Request: asking for something that does not exist yet.
- Cancellation: intent to leave the service entirely, or to stop renewal.

Tie-break rules:
- Intent to leave the service entirely, or stop renewal -> Cancellation.
- Fewer locations, downgrade or plan change -> Billing.
- Something that exists but is broken or wrong -> Bug, even when the symptom is a login failure caused by an outage.
- Asking for something that does not exist yet -> Feature Request, even when it mentions "cancel" or login.
- Mixed intent -> the single most appropriate category. Always return one label only.

Confidence rubric:
- 0.9 or higher: one clear intent that matches a definition.
- 0.7 to 0.9: the category is clear but the wording is indirect.
- Below 0.7: two categories fit, or there is not enough information.

Set needs_human to true when a person should look at the ticket. Give a one-line reason for your choice."""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_human": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["category", "confidence", "needs_human", "reason"],
    "additionalProperties": False,
}


class ClassifyError(Exception):
    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


@dataclass
class Outcome:
    cost: float = 0.0
    tokens: int = 0
    prediction: dict | None = None
    error: str | None = None
    text_redacted: str = ""


def looks_transient(message):
    lowered = message.lower()
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


def call_claude(text):
    command = [
        "claude",
        "-p",
        "--model",
        MODEL,
        "--output-format",
        "json",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--json-schema",
        json.dumps(SCHEMA),
    ]
    with tempfile.TemporaryDirectory() as scratch:
        try:
            completed = subprocess.run(
                command,
                cwd=scratch,
                input=text,
                capture_output=True,
                text=True,
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise ClassifyError(f"claude timed out after {CALL_TIMEOUT_SECONDS}s", transient=True) from error
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and (completed.returncode == 0 or payload.get("is_error")):
        return payload
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"claude exited {completed.returncode}"
        raise ClassifyError(message, transient=looks_transient(message))
    raise ClassifyError("unparseable claude output", transient=True)


def checked_response(response):
    if response.get("is_error"):
        message = str(response.get("result", "claude reported an error"))
        status = response.get("api_error_status")
        if isinstance(status, int):
            transient = status == 429 or status >= 500
        else:
            transient = looks_transient(message)
        raise ClassifyError(message, transient=transient)
    output = response.get("structured_output")
    if not isinstance(output, dict) or not set(SCHEMA["required"]) <= output.keys():
        raise ClassifyError("no complete structured_output in claude response", transient=True)
    return response


def usage_tokens(response):
    usage = response.get("usage") or {}
    return sum(usage.get(key, 0) for key in TOKEN_KEYS)


def mask_pii(text):
    for pattern, token in PII_PATTERNS:
        text = pattern.sub(token, text)
    return text


def run_classification(text, call=call_claude, wait=BACKOFF):
    text = mask_pii(text)
    outcome = Outcome(text_redacted=text)

    def attempt():
        response = call(text)
        outcome.cost += response.get("total_cost_usd") or 0.0
        outcome.tokens += usage_tokens(response)
        return checked_response(response)

    try:
        response = Retrying(
            retry=retry_if_exception(lambda error: isinstance(error, ClassifyError) and error.transient),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=wait,
            reraise=True,
        )(attempt)
    except ClassifyError as error:
        outcome.error = str(error)
        return outcome
    output = response["structured_output"]
    outcome.prediction = {
        "category": output["category"],
        "confidence": output["confidence"],
        "needs_human": output["needs_human"] or output["confidence"] < CONFIDENCE_THRESHOLD,
        "reason": output["reason"],
    }
    return outcome


def classify(text, call=call_claude, wait=BACKOFF):
    outcome = run_classification(text, call=call, wait=wait)
    if outcome.error is not None:
        raise ClassifyError(outcome.error)
    return {**outcome.prediction, "text_redacted": outcome.text_redacted, "cost": outcome.cost, "tokens": outcome.tokens}


def classify_row(ticket, call, wait):
    row = {"ticket_id": ticket["ticket_id"], "text_redacted": mask_pii(ticket["text"])}
    try:
        outcome = run_classification(ticket["text"], call=call, wait=wait)
    except Exception as error:
        print(f"unexpected error on {ticket['ticket_id']}: {error!r}", file=sys.stderr)
        outcome = Outcome(error="unexpected error")
    if outcome.error is not None:
        failure = {
            "predicted_category": "",
            "confidence": "",
            "needs_human": True,
            "reason": f"classification failed: {outcome.error}",
        }
        return {**row, **failure}, outcome
    prediction = {
        "predicted_category": outcome.prediction["category"],
        "confidence": outcome.prediction["confidence"],
        "needs_human": outcome.prediction["needs_human"],
        "reason": outcome.prediction["reason"],
    }
    return {**row, **prediction}, outcome


def read_tickets(input_path):
    with open(input_path, newline="") as source:
        reader = csv.DictReader(source)
        if not REQUIRED_COLUMNS <= set(reader.fieldnames or []):
            raise ValueError(f"{input_path} must have columns ticket_id,text")
        return list(reader)


def classify_all(tickets, call, wait):
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda ticket: classify_row(ticket, call, wait), tickets))
    return results, time.monotonic() - started


def predict(input_path, output_path=DEFAULT_OUTPUT, call=call_claude, wait=BACKOFF):
    tickets = read_tickets(input_path)
    results, elapsed = classify_all(tickets, call, wait)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(row for row, _ in results)

    for row, outcome in results:
        if outcome.error is not None:
            print(f"FAILED {row['ticket_id']}: {outcome.error}")
    print(
        f"{len(tickets)} tickets -> {output_path} | "
        f"cost ${sum(outcome.cost for _, outcome in results):.4f} | "
        f"tokens {sum(outcome.tokens for _, outcome in results)} | "
        f"time {elapsed:.1f}s"
    )


def main(argv):
    if len(argv) == 2 and argv[1] == "evaluate":
        from evaluate import evaluate

        evaluate()
    elif len(argv) == 3 and argv[1] == "predict":
        predict(argv[2])
    else:
        sys.exit("usage: classify.py predict TICKETS_CSV | classify.py evaluate")


if __name__ == "__main__":
    main(sys.argv)
