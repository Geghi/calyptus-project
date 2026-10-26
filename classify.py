import csv
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = "claude-sonnet-5-5"
CATEGORIES = ["Billing", "Bug", "Account Access", "Feature Request", "Cancellation"]
CONFIDENCE_THRESHOLD = 0.7
PARALLEL_CALLS = 5
DEFAULT_OUTPUT = Path("results/predictions_new.csv")
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
    pass


def call_claude(text):
    command = [
        "claude",
        "-p",
        text,
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
        completed = subprocess.run(command, cwd=scratch, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ClassifyError(completed.stderr.strip() or f"claude exited {completed.returncode}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ClassifyError(f"unparseable claude output: {error}") from error


def classify(text, call=call_claude):
    response = call(text)
    if response.get("is_error"):
        raise ClassifyError(str(response.get("result", "claude reported an error")))
    output = response.get("structured_output")
    if not output:
        raise ClassifyError("no structured_output in claude response")
    usage = response.get("usage", {})
    return {
        "category": output["category"],
        "confidence": output["confidence"],
        "needs_human": output["needs_human"] or output["confidence"] < CONFIDENCE_THRESHOLD,
        "reason": output["reason"],
        "cost": response.get("total_cost_usd", 0.0),
        "tokens": sum(
            usage.get(key, 0)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        ),
    }


def classify_row(ticket, call):
    try:
        return classify(ticket["text"], call=call)
    except (ClassifyError, KeyError) as error:
        return {"error": str(error) or type(error).__name__, "cost": 0.0, "tokens": 0}


def output_row(ticket, result):
    if "error" in result:
        return {
            "ticket_id": ticket["ticket_id"],
            "text_redacted": ticket["text"],
            "predicted_category": "",
            "confidence": "",
            "needs_human": True,
            "reason": f"classification failed: {result['error']}",
        }
    return {
        "ticket_id": ticket["ticket_id"],
        "text_redacted": ticket["text"],
        "predicted_category": result["category"],
        "confidence": result["confidence"],
        "needs_human": result["needs_human"],
        "reason": result["reason"],
    }


def predict(input_path, output_path=DEFAULT_OUTPUT, call=call_claude):
    with open(input_path, newline="") as source:
        tickets = list(csv.DictReader(source))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda ticket: classify_row(ticket, call), tickets))
    elapsed = time.monotonic() - started

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_row(t, r) for t, r in zip(tickets, results))

    for ticket, result in zip(tickets, results):
        if "error" in result:
            print(f"FAILED {ticket['ticket_id']}: {result['error']}")
    print(
        f"{len(tickets)} tickets -> {output_path} | "
        f"cost ${sum(r['cost'] for r in results):.4f} | "
        f"tokens {sum(r['tokens'] for r in results)} | "
        f"time {elapsed:.1f}s"
    )


def main(argv):
    if len(argv) != 3 or argv[1] != "predict":
        sys.exit("usage: classify.py predict TICKETS_CSV")
    predict(argv[2])


if __name__ == "__main__":
    main(sys.argv)
