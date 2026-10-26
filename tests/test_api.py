import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

import classify
from app.deps import get_call
from app.main import app


def cli_result(category="Bug", confidence=0.95, needs_human=False, reason="broken"):
    return {
        "is_error": False,
        "structured_output": {
            "category": category,
            "confidence": confidence,
            "needs_human": needs_human,
            "reason": reason,
        },
        "total_cost_usd": 0.005,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def use_call(fake):
    app.dependency_overrides[get_call] = lambda: fake


def failing_call(text):
    raise classify.ClassifyError("secret raw stderr")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_call_is_the_claude_cli():
    assert get_call() is classify.call_claude


def test_classify_returns_prediction_without_cost_or_text_fields(client):
    use_call(lambda text: cli_result(category="Billing", reason="double charge"))

    response = client.post("/classify", json={"ticket_id": "T1", "text": "charged twice"})

    assert response.status_code == 200
    assert response.json() == {
        "ticket_id": "T1",
        "category": "Billing",
        "confidence": 0.95,
        "needs_human": False,
        "reason": "double charge",
    }


def test_classify_applies_low_confidence_rule(client):
    use_call(lambda text: cli_result(confidence=0.5))

    response = client.post("/classify", json={"text": "export broken"})

    assert response.json()["needs_human"] is True


def test_classify_ticket_id_is_optional(client):
    use_call(lambda text: cli_result())

    response = client.post("/classify", json={"text": "export broken"})

    assert response.status_code == 200
    assert response.json()["ticket_id"] is None


def test_classify_sends_only_masked_text_to_the_model(client):
    seen = []
    use_call(lambda text: seen.append(text) or cli_result())

    response = client.post("/classify", json={"text": "write to ann@example.com please"})

    assert seen == ["write to [EMAIL] please"]
    assert "ann@example.com" not in response.text


@pytest.mark.parametrize(
    "payload",
    [{"text": ""}, {"text": "x" * 5001}, {}, {"ticket_id": "T1"}],
)
def test_classify_rejects_invalid_input(client, payload):
    use_call(lambda text: cli_result())

    assert client.post("/classify", json=payload).status_code == 422


def test_classify_failure_is_502_with_generic_message_and_logged(client, caplog):
    use_call(failing_call)

    with caplog.at_level(logging.ERROR):
        response = client.post("/classify", json={"text": "hello"})

    assert response.status_code == 502
    assert "secret raw stderr" not in response.text
    assert "secret raw stderr" in caplog.text


def test_batch_returns_predictions_in_order(client):
    use_call(lambda text: cli_result(category="Bug" if "bug" in text else "Billing"))

    response = client.post(
        "/classify/batch",
        json={"tickets": [{"ticket_id": "A", "text": "a bug"}, {"ticket_id": "B", "text": "an invoice"}]},
    )

    assert response.status_code == 200
    assert [(p["ticket_id"], p["category"]) for p in response.json()["predictions"]] == [
        ("A", "Bug"),
        ("B", "Billing"),
    ]


def test_batch_sends_only_masked_text_to_the_model(client):
    seen = []
    use_call(lambda text: seen.append(text) or cli_result())

    response = client.post("/classify/batch", json={"tickets": [{"text": "call +39 333 1234567"}]})

    assert seen == ["call [PHONE]"]
    assert "1234567" not in response.text


def test_batch_failed_ticket_is_flagged_not_a_failed_batch(client, caplog):
    def flaky(text):
        if "bad" in text:
            return failing_call(text)
        return cli_result()

    use_call(flaky)

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/classify/batch",
            json={"tickets": [{"ticket_id": "A", "text": "ok"}, {"ticket_id": "B", "text": "bad"}]},
        )

    assert response.status_code == 200
    ok, failed = response.json()["predictions"]
    assert ok["category"] == "Bug"
    assert failed == {
        "ticket_id": "B",
        "category": None,
        "confidence": None,
        "needs_human": True,
        "reason": "classification failed",
    }
    assert "secret raw stderr" not in response.text
    assert "secret raw stderr" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"tickets": []},
        {"tickets": [{"text": "x"}] * 101},
        {"tickets": [{"text": ""}]},
    ],
)
def test_batch_rejects_invalid_input(client, payload):
    use_call(lambda text: cli_result())

    assert client.post("/classify/batch", json=payload).status_code == 422


def test_batch_runs_at_most_five_in_parallel(client):
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def slow(text):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return cli_result()

    use_call(slow)

    client.post("/classify/batch", json={"tickets": [{"text": f"t{i}"} for i in range(12)]})

    assert 1 < state["peak"] <= classify.PARALLEL_CALLS
