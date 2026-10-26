import csv
import json
import shutil
import subprocess

import pytest
from tenacity import wait_none

import classify


def cli_result(category="Bug", confidence=0.95, needs_human=False, reason="broken", cost=0.005):
    return {
        "is_error": False,
        "structured_output": {
            "category": category,
            "confidence": confidence,
            "needs_human": needs_human,
            "reason": reason,
        },
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 20,
        },
    }


def write_tickets(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticket_id", "text"])
        writer.writerows(rows)


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_classify_parses_structured_output_cost_and_tokens():
    result = classify.classify("t", call=lambda text: cli_result())
    assert result == {
        "category": "Bug",
        "confidence": 0.95,
        "needs_human": False,
        "reason": "broken",
        "cost": 0.005,
        "tokens": 135,
    }


def test_low_confidence_forces_needs_human():
    result = classify.classify("t", call=lambda text: cli_result(confidence=0.69))
    assert result["needs_human"] is True


def test_model_needs_human_is_kept_when_confident():
    result = classify.classify("t", call=lambda text: cli_result(confidence=0.99, needs_human=True))
    assert result["needs_human"] is True


def test_confident_and_model_ok_means_no_human():
    result = classify.classify("t", call=lambda text: cli_result(confidence=0.7))
    assert result["needs_human"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"is_error": True, "result": "boom"},
        {"is_error": False},
        {"is_error": False, "structured_output": None},
    ],
)
def test_classify_raises_on_bad_cli_result(bad):
    with pytest.raises(classify.ClassifyError):
        classify.classify("t", call=lambda text: bad, wait=wait_none())


def flaky_call(failures, failure):
    attempts = []

    def call(text):
        attempts.append(text)
        if len(attempts) <= failures:
            return failure() if callable(failure) else failure
        return cli_result()

    call.attempts = attempts
    return call


@pytest.mark.parametrize(
    "failure",
    [
        {"is_error": True, "result": "overloaded"},
        {"is_error": False},
        lambda: (_ for _ in ()).throw(classify.ClassifyError("exit 1")),
    ],
)
def test_classify_retries_transient_failures_then_succeeds(failure):
    call = flaky_call(2, failure)
    result = classify.classify("t", call=call, wait=wait_none())
    assert result["category"] == "Bug"
    assert len(call.attempts) == 3


def test_classify_gives_up_after_four_attempts_and_reraises_original():
    call = flaky_call(99, {"is_error": True, "result": "overloaded"})
    with pytest.raises(classify.ClassifyError, match="overloaded"):
        classify.classify("t", call=call, wait=wait_none())
    assert len(call.attempts) == 4


def test_classify_does_not_retry_programming_errors():
    attempts = []

    def call(text):
        attempts.append(text)
        return {"is_error": False, "structured_output": {"category": "Bug"}}

    with pytest.raises(KeyError):
        classify.classify("t", call=call, wait=wait_none())
    assert len(attempts) == 1


def test_predict_reports_failed_row_after_all_retries(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [["T1", "a"]])
    call = flaky_call(99, {"is_error": True, "result": "429 rate limit"})
    classify.predict(src, out, call=call, wait=wait_none())
    row = read_rows(out)[0]
    assert len(call.attempts) == 4
    assert row["needs_human"] == "True"
    assert "429" in row["reason"]
    assert "FAILED T1" in capsys.readouterr().out


def test_call_claude_turns_timeout_into_classify_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(classify.subprocess, "run", fake_run)
    with pytest.raises(classify.ClassifyError, match="timed out"):
        classify.call_claude("x")


def test_call_claude_sets_a_timeout(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(cli_result()), stderr="")

    monkeypatch.setattr(classify.subprocess, "run", fake_run)
    classify.call_claude("x")
    assert seen["timeout"] == classify.CALL_TIMEOUT_SECONDS


def test_predict_writes_csv_and_reports_totals(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "results" / "out.csv"
    write_tickets(src, [["T1", "a"], ["T2", "b"]])
    classify.predict(src, out, call=lambda text: cli_result(category="Billing", confidence=0.5))
    rows = read_rows(out)
    assert list(rows[0]) == [
        "ticket_id",
        "text_redacted",
        "predicted_category",
        "confidence",
        "needs_human",
        "reason",
    ]
    assert [r["ticket_id"] for r in rows] == ["T1", "T2"]
    assert rows[0]["text_redacted"] == "a"
    assert rows[0]["predicted_category"] == "Billing"
    assert rows[0]["needs_human"] == "True"
    printed = capsys.readouterr().out
    assert "0.0100" in printed
    assert "270" in printed
    assert "s" in printed


def test_predict_reports_failed_ticket_without_crashing(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [["T1", "ok"], ["T2", "bad"]])

    def call(text):
        return cli_result() if text == "ok" else {"is_error": True, "result": "boom"}

    classify.predict(src, out, call=call, wait=wait_none())
    rows = {r["ticket_id"]: r for r in read_rows(out)}
    assert rows["T1"]["predicted_category"] == "Bug"
    assert rows["T2"]["predicted_category"] == ""
    assert rows["T2"]["needs_human"] == "True"
    assert "boom" in rows["T2"]["reason"]
    assert "T2" in capsys.readouterr().out


def test_predict_keeps_input_order_with_parallel_calls(tmp_path):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [[f"T{i}", f"text{i}"] for i in range(12)])
    classify.predict(src, out, call=lambda text: cli_result(reason=text))
    rows = read_rows(out)
    assert [r["reason"] for r in rows] == [f"text{i}" for i in range(12)]


def test_command_line_has_isolation_flags(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(cli_result()), stderr="")

    monkeypatch.setattr(classify.subprocess, "run", fake_run)
    assert classify.call_claude("hello")["structured_output"]["category"] == "Bug"
    cmd = seen["cmd"]
    assert cmd[:3] == ["claude", "-p", "hello"]
    for flag, value in [
        ("--model", "claude-sonnet-5-5"),
        ("--output-format", "json"),
        ("--tools", ""),
        ("--setting-sources", ""),
    ]:
        assert cmd[cmd.index(flag) + 1] == value
    assert "--no-session-persistence" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["properties"]["category"]["enum"] == classify.CATEGORIES
    assert cmd[cmd.index("--system-prompt") + 1] == classify.SYSTEM_PROMPT
    assert seen["kwargs"]["cwd"] != "."


def test_call_claude_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        classify.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="nope"),
    )
    with pytest.raises(classify.ClassifyError, match="nope"):
        classify.call_claude("x")


def test_system_prompt_has_categories_rules_and_rubric():
    for needle in [*classify.CATEGORIES, "0.9", "0.7", "Cancellation"]:
        assert needle in classify.SYSTEM_PROMPT


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_smoke_real_claude():
    result = classify.classify("I was charged twice this month, please refund one payment.")
    assert result["category"] in classify.CATEGORIES
    assert 0 <= result["confidence"] <= 1
