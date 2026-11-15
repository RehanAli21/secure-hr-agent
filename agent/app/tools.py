# The tools: the ONLY things the AI can do.
#
# Each tool is one narrow business action ("get my leave"), never "run any query".
# A tool calls the CRUD API as the logged-in user, so the CRUD API decides what is allowed.
#
# How the AI never sees the token:
#   make_tools(session) builds the tool functions INSIDE itself, so they can use `session`
#   without it being a parameter. The AI only sees the parameters (none so far), so it can't
#   read the token, and it can't pass in someone else's.
#
# Notice: no "my" tool takes an employee id. Whose data it is always comes from the login,
# so the AI can't ask for someone else's salary by changing a number.

import json
from datetime import date
from typing import Any, Optional

from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, ConfigDict

from app import crud_api
from app.sessions import ChatSession

# Field allowlists: ONLY these fields reach the AI. Everything else is dropped.
#   profile: home_address is left out, the AI doesn't need it to answer questions about your job
#   leave:   the reason is left out, it can be private (e.g. medical)
#   salary:  the amount is the whole point of the tool, and you may see your own
#   reviews: the comments are kept, they are what people ask about ("what did my manager say?")
PROFILE_FIELDS = ["id", "name", "email", "job_title", "department_id", "manager_id",
                  "employment_status", "start_date", "phone"]
LEAVE_FIELDS = ["id", "leave_type", "start_date", "end_date", "status", "decided_at"]
SALARY_FIELDS = ["amount", "effective_date"]
REVIEW_FIELDS = ["period", "rating", "comments", "reviewer_id"]


# Which permission (from GET /me/permissions) each tool needs. A tool missing here is never offered.
TOOL_PERMISSIONS = {
    "get_my_profile": "view_own_profile",
    "get_my_leave": "view_own_leave",
    "get_my_salary": "view_own_salary",
    "get_my_reviews": "view_own_reviews",
}


def keep_only(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Copy just the allowed fields, e.g. keep_only({"a": 1, "b": 2}, ["a"]) -> {"a": 1}."""
    return {name: record[name] for name in fields if name in record}


def keep_only_in_list(records: list[dict[str, Any]], fields: list[str]) -> str:
    """keep_only for every record of a list, as JSON text for the AI."""
    return json.dumps([keep_only(record, fields) for record in records])


# ---------- Argument schemas (the AI's arguments are checked against these before a tool runs) ----------

class NoArgs(BaseModel):
    """For tools that take no arguments. extra="forbid": any argument the AI makes up is rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------- The tools ----------

def make_tools(session: ChatSession, permissions: list[str]) -> list[FunctionTool]:
    """Build the tools for one logged-in user: only the ones their permissions allow."""

    def my_employee_id() -> Optional[int]:
        """Which employee is logged in? Asked from the CRUD API, never from the AI."""
        return crud_api.call(session, "GET", "/me").get("employee_id")

    def get_my_profile() -> str:
        """Get the logged-in user's own employee profile: name, email, job title,
        department id, manager id, employment status, start date and phone.
        Use it for any question about the user's own job, department, manager or start date."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        employee = crud_api.call(session, "GET", f"/employees/{employee_id}")
        return json.dumps(keep_only(employee, PROFILE_FIELDS))

    def get_my_leave() -> str:
        """Get the logged-in user's own leave requests (newest first, at most 20): type (annual, sick,
        unpaid or parental), start and end dates, number of calendar days, and status
        (pending, approved, rejected or cancelled).
        Use it for ANY question about the user's own leave, past or future: "when was my last
        holiday?", "how many sick days did I take?", "was my leave approved?"."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        leave = crud_api.call(session, "GET", "/leave", params={"employee_id": employee_id})
        rows = [keep_only(request, LEAVE_FIELDS) for request in leave]
        for row in rows:
            # Small models are bad at date maths, so we count the days for them (both ends included).
            row["calendar_days"] = (date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])).days + 1
        return json.dumps(rows)

    def get_my_salary() -> str:
        """Get the logged-in user's own salary history (newest first): amount and effective date.
        The current salary is the newest one whose effective date is not after today.
        A row with a later effective date is a raise that has not started yet.
        The amounts have no currency: say the number only, never add a currency sign."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        salaries = crud_api.call(session, "GET", f"/employees/{employee_id}/salaries")
        return keep_only_in_list(salaries, SALARY_FIELDS)

    def get_my_reviews() -> str:
        """Get the logged-in user's own performance reviews (newest first): period (like "2025-H2"),
        rating from 1 to 5, comments, and the employee id of the reviewer."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        reviews = crud_api.call(session, "GET", f"/employees/{employee_id}/reviews")
        return keep_only_in_list(reviews, REVIEW_FIELDS)

    all_tools = [
        FunctionTool.from_defaults(fn=get_my_profile, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_leave, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_salary, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_reviews, fn_schema=NoArgs),
    ]

    # Only offer the tools this user may use, so the AI doesn't offer things that would fail.
    # This is only for a better experience: the CRUD API still checks every single call itself.
    return [tool for tool in all_tools if TOOL_PERMISSIONS.get(tool.metadata.get_name()) in permissions]
