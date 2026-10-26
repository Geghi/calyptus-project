import csv

import pytest
from tenacity import wait_none

import classify
import evaluate
from test_predict import cli_result, read_rows

LABELLED = "data/tickets_labelled.csv"


def labelled_rows():
    with open(LABELLED, newline="") as f:
        return list(csv.DictReader(f))


def test_labelled_csv_keeps_team_labels_and_adds_corrections():
    rows = {r["ticket_id"]: r for r in labelled_rows()}
    assert len(rows) == 40
    assert list(rows["T001"]) == ["ticket_id", "text", "label", "corrected_label"]
    changed = {i: (r["label"], r["corrected_label"]) for i, r in rows.items() if r["label"] != r["corrected_label"]}
    assert changed == {
        "T007": ("Billing", "Account Access"),
        "T019": ("Cancellation", "Feature Request"),
        "T033": ("Feature Request", "Bug"),
        "T006": ("Cancellation", "Billing"),
    }


def test_accuracy():
    assert evaluate.accuracy(["a", "b", "c", "d"], ["a", "b", "x", ""]) == 0.5


def test_accuracy_of_nothing_is_zero():
    assert evaluate.accuracy([], []) == 0.0


def test_precision_recall_per_category():
    expected = ["Billing", "Billing", "Bug", "Bug"]
    predicted = ["Billing", "Bug", "Bug", "Bug"]
    scores = evaluate.precision_recall(expected, predicted)
    assert scores["Billing"] == {"precision": 1.0, "recall": 0.5}
    assert scores["Bug"] == {"precision": pytest.approx(2 / 3), "recall": 1.0}


def test_category_with_no_predictions_or_no_tickets_scores_zero_without_dividing_by_zero():
    scores = evaluate.precision_recall(["Billing"], ["Bug"])
    assert scores["Billing"] == {"precision": 0.0, "recall": 0.0}
    assert scores["Cancellation"] == {"precision": 0.0, "recall": 0.0}
    assert set(scores) == set(classify.CATEGORIES)


def test_confusion_matrix_counts_expected_by_predicted_with_failures_apart():
    matrix = evaluate.confusion_matrix(["Billing", "Billing", "Bug"], ["Bug", "Billing", ""])
    assert matrix["Billing"]["Bug"] == 1
    assert matrix["Billing"]["Billing"] == 1
    assert matrix["Bug"][evaluate.FAILED] == 1
    assert matrix["Cancellation"]["Bug"] == 0


def row(ticket_id, label, corrected, predicted, confidence=0.9, needs_human=False, reason="because"):
    return {
        "ticket_id": ticket_id,
        "text_redacted": f"text {ticket_id}",
        "label": label,
        "corrected_label": corrected,
        "predicted_category": predicted,
        "confidence": confidence,
        "needs_human": needs_human,
        "reason": reason,
    }


ROWS = [
    row("T1", "Billing", "Billing", "Billing"),
    row("T2", "Cancellation", "Billing", "Billing", 0.6, True),
    row("T3", "Bug", "Bug", "Billing", 0.55, True, "looked like money"),
    row("T4", "Bug", "Bug", "", "", True, "classification failed: boom"),
]
TOTALS = {"cost": 0.0123, "tokens": 456, "seconds": 7.5}


def test_report_has_both_accuracies_flag_split_and_totals():
    report = evaluate.render_report(ROWS, **TOTALS)
    assert "Accuracy vs `label`: 25.0% (1/4)" in report
    assert "Accuracy vs `corrected_label`: 50.0% (2/4)" in report
    assert "Flagged: 3/4" in report
    assert "$0.0123" in report
    assert "456" in report
    assert "7.5s" in report


def test_report_lists_every_misclassified_ticket_with_details():
    report = evaluate.render_report(ROWS, **TOTALS)
    assert "T3" in report and "text T3" in report and "looked like money" in report and "0.55" in report
    assert "T4" in report and "classification failed: boom" in report
    assert "T2" in report
    assert "| T1 |" not in report


def test_report_flagged_split_handles_no_flagged_tickets():
    report = evaluate.render_report([row("T1", "Bug", "Bug", "Bug")], **TOTALS)
    assert "Flagged: 0/1" in report
    assert "n/a" in report


def test_report_renders_confusion_matrix_and_per_category_scores():
    report = evaluate.render_report(ROWS, **TOTALS)
    for category in classify.CATEGORIES:
        assert category in report
    assert "Precision" in report and "Recall" in report


def fake_call(text):
    return cli_result(category="Billing" if "charged" in text else "Bug")


def write_labelled(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticket_id", "text", "label", "corrected_label"])
        writer.writerows(rows)


def test_evaluate_writes_report_and_predictions(tmp_path):
    src = tmp_path / "labelled.csv"
    write_labelled(src, [["T1", "charged twice", "Billing", "Billing"], ["T2", "blank page", "Billing", "Bug"]])
    report, predictions = tmp_path / "out" / "eval_report.md", tmp_path / "out" / "eval_predictions.csv"
    evaluate.evaluate(src, report, predictions, call=fake_call)
    rows = read_rows(predictions)
    assert list(rows[0]) == [
        "ticket_id",
        "text_redacted",
        "label",
        "corrected_label",
        "predicted_category",
        "confidence",
        "needs_human",
        "reason",
    ]
    assert [r["predicted_category"] for r in rows] == ["Billing", "Bug"]
    text = report.read_text()
    assert "Accuracy vs `label`: 50.0% (1/2)" in text
    assert "vs `corrected_label`: 100.0% (2/2)" in text
    assert "$0.0100" in text


def test_report_escapes_pipes_in_text_and_reason():
    piped = row("T9", "Bug", "Bug", "Billing", reason="a | b")
    piped["text_redacted"] = "x | y"
    report = evaluate.render_report([piped], **TOTALS)
    assert "x \\| y" in report
    assert "a \\| b" in report


def test_evaluate_counts_failed_ticket_as_wrong_and_flagged_and_sums_its_cost(tmp_path):
    src = tmp_path / "labelled.csv"
    write_labelled(src, [["T1", "charged twice", "Billing", "Billing"], ["T2", "boom", "Bug", "Bug"]])
    report, predictions = tmp_path / "eval_report.md", tmp_path / "eval_predictions.csv"

    def call(text):
        if text == "boom":
            return {"is_error": True, "result": "invalid request", "total_cost_usd": 0.001}
        return fake_call(text)

    evaluate.evaluate(src, report, predictions, call=call, wait=wait_none())
    text = report.read_text()
    assert "Accuracy vs `label`: 50.0% (1/2)" in text
    assert "Flagged: 1/2" in text
    assert "$0.0060" in text
    assert "classification failed: invalid request" in text


def test_evaluate_writes_masked_text(tmp_path):
    src = tmp_path / "labelled.csv"
    write_labelled(src, [["T1", "charged twice, mail me at a@b.com", "Billing", "Billing"]])
    report, predictions = tmp_path / "eval_report.md", tmp_path / "eval_predictions.csv"
    evaluate.evaluate(src, report, predictions, call=fake_call)
    assert "a@b.com" not in read_rows(predictions)[0]["text_redacted"]
