# Tests for the get_analytics tool: numbers about many people, from GET /analytics.
# The CRUD API decides everything (who may ask, the small-group rule); these tests check that the
# tool only passes the question on, and only offers itself to the right people.

from fastapi.testclient import TestClient

from tests.fake_crud_api import ANALYTICS, EMPLOYEE_PERMISSIONS, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply
from tests.test_tools import log_in, run_tool

ANALYST = {**EMPLOYEE_PERMISSIONS, "role": "analyst", "permissions": ["view_own_profile", "view_analytics"]}
MANAGER = {**EMPLOYEE_PERMISSIONS, "role": "manager", "permissions": ["view_own_profile", "view_team_analytics"]}


def analytics_requests(fake_api: FakeCrudApi) -> list[dict[str, str]]:
    """The query string of every GET /analytics the agent sent."""
    return [dict(request.url.params) for request in fake_api.requests if request.url.path == "/analytics"]


def test_the_question_is_passed_on_as_it_was_given(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = ANALYST

    run_tool(client, fake_llm, "get_analytics",
             {"metric": "leave_days", "group_by": "month", "leave_type": "sick", "from_date": "2026-01-01"})

    # Only what the model gave (nothing sent as "None"), with the date as YYYY-MM-DD.
    assert analytics_requests(fake_api) == [
        {"metric": "leave_days", "group_by": "month", "leave_type": "sick", "from_date": "2026-01-01"}]


def test_the_model_gets_the_numbers_and_the_note(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = ANALYST

    result = run_tool(client, fake_llm, "get_analytics", {"metric": "average_salary", "group_by": "department"})

    assert '{"group":"Engineering","value":104325.0,"people":148}' in result
    assert '"scope":"company"' in result
    assert "fewer than 5 people are not shown" in result  # so it can tell the user why a group is missing
    assert "hidden_groups" not in result  # the note already says it: fewer words for the small model
    assert "no currency" in result  # salary numbers: the data doesn't say which currency


def test_group_by_none_asks_for_one_total(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # group_by is required, so the model must choose; "none" means one number, so it isn't sent.
    log_in(client)
    fake_api.permissions = ANALYST

    run_tool(client, fake_llm, "get_analytics", {"metric": "headcount", "group_by": "none"})
    result = run_tool(client, fake_llm, "get_analytics", {"metric": "headcount"})  # group_by forgotten

    assert analytics_requests(fake_api) == [{"metric": "headcount"}]  # only the first one was asked
    assert "wrong arguments" in result


def test_it_is_offered_to_analysts_and_managers_but_not_employees(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    for permissions, offered in [(ANALYST, True), (MANAGER, True), (EMPLOYEE_PERMISSIONS, False)]:
        fake_api.permissions = permissions
        assert client.post("/chat", json={"message": "Hi"}).status_code == 200
        assert ("get_analytics" in [tool.metadata.get_name() for tool in fake_llm.seen_tools]) == offered


def test_an_employee_s_model_cannot_call_it_anyway(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)  # an employee: no analytics permission

    result = run_tool(client, fake_llm, "get_analytics", {"metric": "payroll_total", "group_by": "none"})

    assert "no tool with that name" in result
    assert analytics_requests(fake_api) == []


def test_a_refusal_from_the_crud_api_reaches_the_model(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # A manager asking for salary numbers: the CRUD API says no, and the model can explain why.
    log_in(client)
    fake_api.permissions = MANAGER
    fake_api.analytics_status = 403

    result = run_tool(client, fake_llm, "get_analytics", {"metric": "average_salary", "group_by": "none"})

    assert "Error 403: Salary numbers are only for analysts, HR and admins" in result


def test_only_the_fixed_choices_are_accepted(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # No made-up metric, and no way to slip in anything like SQL.
    log_in(client)
    fake_api.permissions = ANALYST

    wrong: list[dict[str, object]] = [{"metric": "salaries", "group_by": "none"},
                                      {"metric": "headcount", "group_by": "name"},
                                      {"metric": "headcount", "group_by": "none", "sql": "SELECT * FROM salaries"}]
    for args in wrong:
        assert "wrong arguments" in run_tool(client, fake_llm, "get_analytics", args)

    assert analytics_requests(fake_api) == []  # nothing was asked


# ---------- The chart under the answer ----------

def ask_for_numbers(client: TestClient, fake_llm: FakeLLM, model_says: str = "Here are the numbers.") -> dict[str, object]:
    """One chat message where the model calls get_analytics. Returns the whole reply."""
    fake_llm.script = [tool_call_reply("get_analytics", {"metric": "average_salary", "group_by": "department"}),
                       text_reply(model_says)]
    response = client.post("/chat", json={"message": "Average salary by department?"})
    assert response.status_code == 200
    return response.json()


def test_the_chart_shows_the_crud_api_s_numbers_not_the_model_s(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    fake_api.permissions = ANALYST

    reply = ask_for_numbers(client, fake_llm, model_says="Engineering earns 1000000 on average.")  # wrong!

    assert reply["chart"] == {
        "title": "Average current salary of active employees",
        "details": "by department",
        "kind": "bar",
        "points": [{"label": "Engineering", "value": 104325.0}, {"label": "Sales", "value": 77737.5}],
    }


def test_numbers_over_time_are_a_line_with_their_dates(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = MANAGER
    fake_api.analytics = {**ANALYTICS, "group_by": "month", "scope": "team",
                          "from_date": "2025-11-18", "to_date": "2026-11-18",
                          "rows": [{"group": "2026-01", "value": 3, "people": 5},
                                   {"group": "2026-02", "value": 4, "people": 6}]}
    fake_llm.script = [tool_call_reply("get_analytics", {"metric": "leave_days", "group_by": "month", "leave_type": "sick"}),
                       text_reply("Here they are.")]

    chart = client.post("/chat", json={"message": "Sick days per month in my team?"}).json()["chart"]

    assert chart["kind"] == "line"
    assert chart["details"] == "by month · sick leave · 2025-11-18 to 2026-11-18 · your team"


def test_one_number_needs_no_chart(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = ANALYST
    fake_api.analytics = {**ANALYTICS, "group_by": None, "rows": [{"group": "All", "value": 90000, "people": 500}]}

    assert ask_for_numbers(client, fake_llm)["chart"] is None


def test_a_chart_belongs_to_the_message_that_made_it(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = ANALYST
    assert ask_for_numbers(client, fake_llm)["chart"] is not None

    fake_llm.script = [text_reply("You're welcome.")]
    assert client.post("/chat", json={"message": "Thanks"}).json()["chart"] is None
