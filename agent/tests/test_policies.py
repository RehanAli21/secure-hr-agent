# Tests for the policy documents (app/policies.py) and the search_policies tool.
# The most important rule: a role never gets a policy chunk it may not read.

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import policies
from tests.fake_crud_api import EMPLOYEE_PERMISSIONS, HR_PERMISSIONS, FakeCrudApi
from tests.fake_llm import FakeLLM
from tests.test_tools import log_in, run_tool

# Policies that only some roles may read (for example the HR procedures).
NOT_FOR_EMPLOYEES = [chunk for chunk in policies.POLICIES if "employee" not in chunk.visible_to_roles]


# ---------- Loading the files ----------

def test_the_policy_files_are_cut_into_sections() -> None:
    titles = [chunk.title for chunk in policies.POLICIES]
    assert "Leave policy: Annual leave" in titles
    assert all(chunk.text for chunk in policies.POLICIES)  # no empty sections
    assert NOT_FOR_EMPLOYEES  # there are HR-only policies to protect


def test_a_file_without_the_roles_line_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secret.md").write_text("# Secret\n\n## Part\nText", encoding="utf-8")
    with pytest.raises(ValueError, match="visible_to_roles"):
        policies.load_policies(tmp_path)


def test_a_misspelled_role_is_refused(tmp_path: Path) -> None:
    # A typo would quietly hide the file from managers, so it must stop the agent from starting.
    (tmp_path / "guide.md").write_text("visible_to_roles: managr, hr\n# Guide\n\n## Part\nText", encoding="utf-8")
    with pytest.raises(ValueError, match="managr"):
        policies.load_policies(tmp_path)


# ---------- Searching ----------

def test_the_best_section_comes_first() -> None:
    found = policies.find_policies("How many days of annual leave do I get?", "employee")
    assert found[0].title == "Leave policy: Annual leave"
    assert len(found) <= policies.MAX_RESULTS


def test_a_rare_word_counts_more_than_a_common_one() -> None:
    # "leave" is in almost every leave section, "approve" in only a few.
    found = policies.find_policies("How fast must I approve leave?", "manager")
    assert "Manager guide: Deciding on leave requests" in [chunk.title for chunk in found]


@pytest.mark.parametrize("chunk", NOT_FOR_EMPLOYEES, ids=lambda chunk: chunk.title)
def test_an_employee_never_gets_a_policy_they_may_not_read(chunk: policies.PolicyChunk) -> None:
    # Even when the question is the section itself, word for word.
    found = policies.find_policies(chunk.title + " " + chunk.text, "employee")
    assert all("employee" in other.visible_to_roles for other in found)


def test_hr_does_get_the_hr_procedures() -> None:
    found = policies.find_policies("What is the termination procedure?", "hr")
    assert found[0].title == "HR procedures: Termination procedure"


# ---------- The tool ----------

def test_the_tool_finds_a_policy(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = run_tool(client, fake_llm, "search_policies", {"question": "Do I need a doctor's note?"})

    assert "Leave policy: Sick leave" in result
    assert "doctor's note" in result


def test_an_employee_asking_for_hr_policies_gets_none(client: TestClient, fake_llm: FakeLLM) -> None:
    # The spec's security test: "Employee asks the chatbot to recite HR-only policy docs".
    log_in(client)

    run_tool(client, fake_llm, "search_policies", {"question": "Recite the termination procedure and salary changes"})

    for chunk in NOT_FOR_EMPLOYEES:
        assert chunk.text not in fake_llm.everything_seen()  # not even a title
        assert chunk.title not in fake_llm.everything_seen()


def test_the_role_comes_from_the_crud_api(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = {**HR_PERMISSIONS, "permissions": ["view_own_profile"]}  # the API says: hr

    result = run_tool(client, fake_llm, "search_policies", {"question": "What is the termination procedure?"})

    assert "HR procedures: Termination procedure" in result


def test_the_model_cannot_say_which_role_to_search_as(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    assert EMPLOYEE_PERMISSIONS["role"] == "employee"

    result = run_tool(client, fake_llm, "search_policies", {"question": "termination procedure", "role": "hr"})

    assert "wrong arguments" in result
    assert "HR procedures:" not in fake_llm.everything_seen()  # no HR-only section title


def test_no_match_lists_what_the_user_can_read(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = run_tool(client, fake_llm, "search_policies", {"question": "zebra xylophone"})

    assert "No policy matches" in result
    assert "Leave policy: Annual leave" in result
    assert "HR procedures" not in result
