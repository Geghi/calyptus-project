import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from tenacity import wait_none

import classify

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_classify_parses_recorded_real_response():
    recorded = json.loads((FIXTURES / "claude_response.json").read_text())
    result = classify.classify("t", call=lambda text: recorded)
    assert result == {
        "category": "Billing",
        "confidence": 0.97,
        "needs_human": True,
        "reason": recorded["structured_output"]["reason"],
        "cost": recorded["total_cost_usd"],
        "tokens": 2 + 168 + 1289 + 331,
    }


def test_classify_sums_cost_of_every_attempt():
    call = flaky_call(1, {**cli_result(), "is_error": True, "structured_output": None, "api_error_status": 429, "result": "slow down", "total_cost_usd": 0.01})
    result = classify.classify("t", call=call, wait=wait_none())
    assert result["cost"] == pytest.approx(0.015)
    assert result["tokens"] == 270


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
        {"is_error": True, "api_error_status": 429, "result": "rate limited"},
        {"is_error": True, "api_error_status": 529, "result": "x"},
        {"is_error": True, "api_error_status": 503, "result": "x"},
        {"is_error": True, "api_error_status": None, "result": "overloaded"},
        {"is_error": True, "api_error_status": None, "result": "network connection reset"},
        {"is_error": False},
        {"is_error": False, "structured_output": {"category": "Bug"}},
        lambda: (_ for _ in ()).throw(classify.ClassifyError("exit 1", transient=True)),
    ],
)
def test_classify_retries_transient_failures_then_succeeds(failure):
    call = flaky_call(2, failure)
    result = classify.classify("t", call=call, wait=wait_none())
    assert result["category"] == "Bug"
    assert len(call.attempts) == 3


@pytest.mark.parametrize(
    "failure",
    [
        {"is_error": True, "api_error_status": 401, "result": "invalid api key"},
        {"is_error": True, "api_error_status": 400, "result": "bad request"},
        {"is_error": True, "api_error_status": None, "result": "unknown model"},
        {"is_error": True, "api_error_status": 400, "result": "429 tokens over the limit"},
        {"is_error": True, "api_error_status": 400, "result": "connection field is invalid"},
        lambda: (_ for _ in ()).throw(classify.ClassifyError("auth failed")),
    ],
)
def test_classify_fails_fast_on_non_transient_failures(failure):
    call = flaky_call(99, failure)
    with pytest.raises(classify.ClassifyError):
        classify.classify("t", call=call, wait=wait_none())
    assert len(call.attempts) == 1


def test_backoff_grows_and_is_capped():
    waits = [classify.BACKOFF(SimpleNamespace(attempt_number=n)) for n in (1, 2, 3, 4)]
    assert waits[0] <= 2 <= waits[1] < 4 <= waits[2]
    assert 8 <= waits[3] <= 9
    assert classify.BACKOFF(SimpleNamespace(attempt_number=20)) == 30


def test_classify_gives_up_after_four_attempts_and_reraises_original():
    call = flaky_call(99, {"is_error": True, "api_error_status": 529, "result": "overloaded"})
    with pytest.raises(classify.ClassifyError, match="overloaded"):
        classify.classify("t", call=call, wait=wait_none())
    assert len(call.attempts) == 4


def test_classify_does_not_retry_programming_errors():
    attempts = []

    def call(text):
        attempts.append(text)
        raise KeyError("bug")

    with pytest.raises(KeyError):
        classify.classify("t", call=call, wait=wait_none())
    assert len(attempts) == 1


def test_predict_reports_failed_row_after_all_retries(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [["T1", "a"]])
    call = flaky_call(99, {"is_error": True, "api_error_status": 429, "result": "429 rate limit"})
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
    assert re.search(r"cost \$0\.0100 \| tokens 270 \| time \d+\.\ds", printed)


def test_predict_cost_includes_retried_and_failed_attempts(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [["T1", "a"]])
    call = flaky_call(99, {"is_error": True, "api_error_status": 429, "result": "x", "total_cost_usd": 0.01})
    classify.predict(src, out, call=call, wait=wait_none())
    assert "cost $0.0400" in capsys.readouterr().out


def test_predict_survives_unexpected_errors_per_ticket(tmp_path, capsys):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [["T1", "ok"], ["T2", "nulls"], ["T3", "explodes"]])
    attempts = []

    def call(text):
        attempts.append(text)
        if text == "nulls":
            return {**cli_result(), "usage": None, "total_cost_usd": None}
        if text == "explodes":
            raise KeyError("secret internals")
        return cli_result()

    classify.predict(src, out, call=call, wait=wait_none())
    rows = {r["ticket_id"]: r for r in read_rows(out)}
    assert rows["T1"]["predicted_category"] == "Bug"
    assert rows["T2"]["predicted_category"] == "Bug"
    assert rows["T3"]["predicted_category"] == ""
    assert rows["T3"]["needs_human"] == "True"
    assert "secret internals" not in rows["T3"]["reason"]
    assert attempts.count("explodes") == 1
    captured = capsys.readouterr()
    assert "secret internals" in captured.err
    assert "FAILED T3" in captured.out


def test_classify_outcome_carries_cost_of_failed_attempts():
    call = flaky_call(99, {"is_error": True, "api_error_status": 529, "result": "x", "total_cost_usd": 0.01})
    outcome = classify.run_classification("t", call=call, wait=wait_none())
    assert outcome.prediction is None
    assert outcome.error == "x"
    assert outcome.cost == pytest.approx(0.04)


def test_predict_rejects_csv_without_required_columns(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text("id,body\n1,hi\n")
    with pytest.raises(ValueError, match="ticket_id"):
        classify.predict(src, tmp_path / "out.csv", call=lambda text: cli_result())


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


def test_predict_runs_exactly_five_calls_concurrently(tmp_path):
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    write_tickets(src, [[f"T{i}", f"text{i}"] for i in range(10)])
    barrier = threading.Barrier(5, timeout=5)
    lock = threading.Lock()
    running = peak = 0

    def call(text):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        barrier.wait()
        with lock:
            running -= 1
        return cli_result()

    classify.predict(src, out, call=call)
    assert peak == 5


def test_command_line_has_isolation_flags_and_text_on_stdin(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        seen["cwd_existed"] = os.path.isdir(kwargs["cwd"])
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(cli_result()), stderr="")

    monkeypatch.setattr(classify.subprocess, "run", fake_run)
    assert classify.call_claude("--hello")["structured_output"]["category"] == "Bug"
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert "--hello" not in cmd
    assert seen["kwargs"]["input"] == "--hello"
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
    cwd = Path(seen["kwargs"]["cwd"]).resolve()
    assert seen["cwd_existed"]
    assert not cwd.is_relative_to(Path(classify.__file__).parent.resolve())
    assert cwd.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not cwd.exists()


def test_call_claude_raises_on_nonzero_exit_without_json(monkeypatch):
    monkeypatch.setattr(
        classify.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="nope"),
    )
    with pytest.raises(classify.ClassifyError, match="nope") as raised:
        classify.call_claude("x")
    assert not raised.value.transient


def test_call_claude_transient_nonzero_exit_is_retryable(monkeypatch):
    monkeypatch.setattr(
        classify.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="connection reset"),
    )
    with pytest.raises(classify.ClassifyError) as raised:
        classify.call_claude("x")
    assert raised.value.transient


def test_call_claude_reads_error_json_from_stdout_on_nonzero_exit(monkeypatch):
    error = {"is_error": True, "api_error_status": 401, "result": "invalid api key", "total_cost_usd": 0}
    monkeypatch.setattr(
        classify.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(error), stderr="noise"),
    )
    assert classify.call_claude("x") == error


@pytest.mark.parametrize("stdout", ["", "not json", "[1]"])
def test_call_claude_unparseable_stdout_is_retryable(monkeypatch, stdout):
    monkeypatch.setattr(
        classify.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=""),
    )
    with pytest.raises(classify.ClassifyError) as raised:
        classify.call_claude("x")
    assert raised.value.transient


def test_system_prompt_has_categories_rules_and_rubric():
    for needle in [*classify.CATEGORIES, "0.9", "0.7"]:
        assert needle in classify.SYSTEM_PROMPT


@pytest.mark.parametrize(
    "rule",
    [
        "login failure caused by an outage",
        "Fewer locations, downgrade or plan change -> Billing",
        'Feature Request, even when it mentions "cancel" or login',
        "stop renewal -> Cancellation",
    ],
)
def test_system_prompt_has_tie_break_rules(rule):
    assert rule in classify.SYSTEM_PROMPT


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="live billed call: set RUN_LIVE=1")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_smoke_real_claude():
    result = classify.classify("I was charged twice this month, please refund one payment.")
    assert result["category"] in classify.CATEGORIES
    assert 0 <= result["confidence"] <= 1
