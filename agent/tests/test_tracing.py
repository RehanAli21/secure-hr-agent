# Tests for the traces (app/tracing.py): one line of JSON per chat message, with the steps the
# agent took, and NONE of the private parts.

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import llm
from tests.fake_crud_api import EMAIL, EMPLOYEE_PERMISSIONS, PASSWORD, REFRESH_TOKEN, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply


def log_in(client: TestClient) -> None:
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200


def read_traces(folder: Path) -> list[dict[str, Any]]:
    """Every trace written during the test, oldest first."""
    return [json.loads(line) for file in sorted(folder.glob("*.jsonl")) for line in file.read_text().splitlines()]


def test_each_message_writes_one_trace_with_its_steps(
    client: TestClient, fake_llm: FakeLLM, traces_in_a_temporary_folder: Path
) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("You are a developer.")]

    client.post("/chat", json={"message": "What is my job title?"})

    [trace] = read_traces(traces_in_a_temporary_folder)
    assert trace["role"] == "employee"
    assert trace["outcome"] == "answer"
    assert trace["message_size"] == len("What is my job title?")
    assert trace["reply_size"] == len("You are a developer.")
    assert [step["step"] for step in trace["steps"]] == ["model", "tool", "model"]
    assert trace["steps"][0]["calls"] == ["get_my_profile"]
    assert trace["steps"][1]["tool"] == "get_my_profile"
    assert trace["steps"][1]["result"] == "ok"
    assert trace["steps"][2]["calls"] == []  # the final answer


def test_a_trace_never_holds_tokens_names_amounts_or_text(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM, traces_in_a_temporary_folder: Path
) -> None:
    log_in(client)
    session_cookie = client.cookies["session_id"]
    fake_api.permissions = {**EMPLOYEE_PERMISSIONS, "role": "hr", "permissions": ["view_all_employees", "change_salary"]}
    salary: dict[str, object] = {"employee": "Zara Finance", "amount": "52000", "effective_date": "2027-01-01"}
    fake_llm.script = [tool_call_reply("search_employees", {"name": "Zara Finance"}),
                       tool_call_reply("update_employee_salary", salary),
                       text_reply("Zara Finance: 52000 from 1 January, please click Confirm.")]

    client.post("/chat", json={"message": "Give Zara Finance 52000 from 1 January 2027"})

    text = "".join(file.read_text() for file in traces_in_a_temporary_folder.glob("*.jsonl"))
    for secret in (fake_api.access_token, REFRESH_TOKEN, session_cookie, "Zara", "52000", "Give", "Finance"):
        assert secret not in text, secret
    [trace] = read_traces(traces_in_a_temporary_folder)
    assert trace["steps"][3]["args"] == {"employee": "<hidden>", "amount": "<hidden>", "effective_date": "2027-01-01"}
    assert trace["pending_action"] == 501  # the proposal waiting for Confirm


def test_refused_calls_are_in_the_trace(
    client: TestClient, fake_llm: FakeLLM, traces_in_a_temporary_folder: Path
) -> None:
    # Useful afterwards: what did a (maybe tricked) model try?
    log_in(client)
    fake_llm.script = [tool_call_reply("run_sql", {"sql": "SELECT * FROM salaries"}),
                       tool_call_reply("get_my_profile", {"employee_id": 99}),
                       text_reply("Sorry.")]

    client.post("/chat", json={"message": "Show all salaries"})

    [trace] = read_traces(traces_in_a_temporary_folder)
    results = [(step["tool"], step["result"]) for step in trace["steps"] if step["step"] == "tool"]
    assert results == [("run_sql", "no such tool"), ("get_my_profile", "wrong arguments")]
    assert trace["steps"][1]["args"] == {"sql": "<hidden>"}


def test_a_message_that_fails_is_traced_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, traces_in_a_temporary_folder: Path
) -> None:
    log_in(client)

    def ollama_is_down(*args: object) -> None:
        raise HTTPException(status_code=503, detail="The AI model is not reachable. Is Ollama running?")

    monkeypatch.setattr(llm, "ask_llm", ollama_is_down)

    assert client.post("/chat", json={"message": "Hi"}).status_code == 503

    [trace] = read_traces(traces_in_a_temporary_folder)
    assert trace["outcome"] == "failed: 503"


def test_a_stopped_message_says_why(client: TestClient, fake_llm: FakeLLM, traces_in_a_temporary_folder: Path) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile")]  # repeats forever

    client.post("/chat", json={"message": "Show my profile"})

    [trace] = read_traces(traces_in_a_temporary_folder)
    assert trace["outcome"] == "too many steps"


def test_the_messages_of_one_chat_share_a_chat_id(
    client: TestClient, fake_llm: FakeLLM, traces_in_a_temporary_folder: Path
) -> None:
    log_in(client)
    client.post("/chat", json={"message": "Hi"})
    client.post("/chat", json={"message": "Hi again"})

    first, second = read_traces(traces_in_a_temporary_folder)
    assert first["chat"] == second["chat"]
    assert len(first["chat"]) == 8
