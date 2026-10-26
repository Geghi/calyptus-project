import csv
from pathlib import Path

from classify import BACKOFF, CATEGORIES, call_claude, classify_all, read_tickets

FAILED = "failed"
LABELLED_PATH = Path("data/tickets_labelled.csv")
REPORT_PATH = Path("results/eval_report.md")
PREDICTIONS_PATH = Path("results/eval_predictions.csv")
PREDICTION_COLUMNS = [
    "ticket_id",
    "text_redacted",
    "label",
    "corrected_label",
    "predicted_category",
    "confidence",
    "needs_human",
    "reason",
]


def accuracy(expected, predicted):
    if not expected:
        return 0.0
    return sum(e == p for e, p in zip(expected, predicted)) / len(expected)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def precision_recall(expected, predicted):
    scores = {}
    for category in CATEGORIES:
        true_positive = sum(e == p == category for e, p in zip(expected, predicted))
        scores[category] = {
            "precision": ratio(true_positive, predicted.count(category)),
            "recall": ratio(true_positive, expected.count(category)),
        }
    return scores


def confusion_matrix(expected, predicted):
    matrix = {e: {p: 0 for p in [*CATEGORIES, FAILED]} for e in CATEGORIES}
    for e, p in zip(expected, predicted):
        matrix[e][p or FAILED] += 1
    return matrix


def percent(hits, total):
    return f"{100 * ratio(hits, total):.1f}% ({hits}/{total})" if total else "n/a"


def table(header, body):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in line) + " |" for line in body]
    return lines


def render_report(rows, cost, tokens, seconds):
    labels = [r["label"] for r in rows]
    corrected = [r["corrected_label"] for r in rows]
    predicted = [r["predicted_category"] for r in rows]
    hits = lambda truth: sum(t == p for t, p in zip(truth, predicted))
    flagged = [r for r in rows if r["needs_human"]]
    unflagged = [r for r in rows if not r["needs_human"]]
    correct = lambda subset: sum(r["predicted_category"] == r["corrected_label"] for r in subset)
    scores = precision_recall(corrected, predicted)
    matrix = confusion_matrix(corrected, predicted)
    wrong = [r for r in rows if r["predicted_category"] != r["label"] or r["predicted_category"] != r["corrected_label"]]
    lines = [
        "# Evaluation report",
        "",
        f"- Accuracy vs `label`: {percent(hits(labels), len(rows))}",
        f"- Accuracy vs `corrected_label`: {percent(hits(corrected), len(rows))}",
        f"- Flagged: {len(flagged)}/{len(rows)}",
        f"- Accuracy (vs `corrected_label`) of flagged tickets: {percent(correct(flagged), len(flagged))}",
        f"- Accuracy (vs `corrected_label`) of not-flagged tickets: {percent(correct(unflagged), len(unflagged))}",
        f"- Cost: ${cost:.4f}",
        f"- Tokens: {tokens}",
        f"- Wall time: {seconds:.1f}s",
        "",
        "## Precision and recall (vs `corrected_label`)",
        "",
        *table(
            ["Category", "Precision", "Recall"],
            [[c, f"{s['precision']:.2f}", f"{s['recall']:.2f}"] for c, s in scores.items()],
        ),
        "",
        "## Confusion matrix (rows: `corrected_label`, columns: predicted)",
        "",
        *table(["", *matrix[CATEGORIES[0]]], [[e, *counts.values()] for e, counts in matrix.items()]),
        "",
        "## Misclassified tickets",
        "",
        *table(
            ["Ticket", "Text", "Label", "Corrected", "Predicted", "Confidence", "Reason"],
            [
                [
                    r["ticket_id"],
                    r["text_redacted"],
                    r["label"],
                    r["corrected_label"],
                    r["predicted_category"] or FAILED,
                    r["confidence"],
                    r["reason"],
                ]
                for r in wrong
            ],
        ),
        "",
    ]
    return "\n".join(lines)


def evaluate(input_path=LABELLED_PATH, report_path=REPORT_PATH, predictions_path=PREDICTIONS_PATH, call=call_claude, wait=BACKOFF):
    tickets = read_tickets(input_path)
    results, seconds = classify_all(tickets, call, wait)
    rows = [
        {**row, "label": ticket["label"], "corrected_label": ticket["corrected_label"]}
        for ticket, (row, _) in zip(tickets, results)
    ]
    report_path, predictions_path = Path(report_path), Path(predictions_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with open(predictions_path, "w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=PREDICTION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    cost = sum(outcome.cost for _, outcome in results)
    tokens = sum(outcome.tokens for _, outcome in results)
    report_path.write_text(render_report(rows, cost, tokens, seconds))
    print(f"{len(rows)} tickets -> {report_path}, {predictions_path} | cost ${cost:.4f} | tokens {tokens} | time {seconds:.1f}s")
