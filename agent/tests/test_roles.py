# Tests for the get_roles tool: what OTHER roles may do, live from GET /roles.

from fastapi.testclient import TestClient

from tests.fake_crud_api import FakeCrudApi
from tests.fake_llm import FakeLLM
from tests.test_tools import log_in, run_tool


def test_without_a_role_it_gives_a_summary_of_every_role(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = run_tool(client, fake_llm, "get_roles")

    assert '"employee":"Sees their own records.' in result
    assert '"hr":"Sees all employees, including salaries.' in result
    assert "Terminate employees" not in result  # the details only come for one role


def test_with_a_role_it_gives_what_that_role_may_do(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = run_tool(client, fake_llm, "get_roles", {"role": "hr"})

    assert '"role":"hr"' in result
    assert "Terminate employees (this also disables their login)" in result
    assert "Ask for leave" not in result  # that is the employee role


def test_it_asks_the_crud_api_every_time(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # Never cached: a change to the roles shows up on the next question.
    log_in(client)

    run_tool(client, fake_llm, "get_roles")
    run_tool(client, fake_llm, "get_roles")

    assert [r.url.path for r in fake_api.requests].count("/roles") == 2


def test_an_unknown_role_is_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = run_tool(client, fake_llm, "get_roles", {"role": "superuser"})

    assert "wrong arguments" in result
    assert "/roles" not in [r.url.path for r in fake_api.requests]  # nothing was asked
