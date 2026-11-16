# The tools: the ONLY things the AI can do.
#
# Each tool is one narrow business action ("get my leave"), never "run any query".
# A tool calls the CRUD API as the logged-in user, so the CRUD API decides what is allowed.
#
# How the AI never sees the token:
#   make_tools(session) builds the tool functions INSIDE itself, so they can use `session`
#   without it being a parameter. The AI only sees the parameters (like `name`), so it can't
#   read the token, and it can't pass in someone else's.
#
# Notice: no "my" tool takes an employee id. Whose data it is always comes from the login,
# so the AI can't ask for someone else's salary by changing a number.
# The HR tools DO take a name or an id, but they still run as the logged-in user: if someone who
# isn't HR got the AI to call them, the CRUD API would refuse (404), exactly as for the app.

import json
from datetime import date, timedelta
from typing import Any, Optional

from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field

from app import crud_api
from app.sessions import ChatSession

# Field allowlists: ONLY these fields reach the AI. Everything else is dropped.
#   profile: home_address is left out, the AI doesn't need it to answer questions about your job
#   leave:   the reason is left out, it can be private (e.g. medical)
#   salary:  the amount is the whole point of the tool, and you may see your own
#   reviews: the comments are kept, they are what people ask about ("what did my manager say?")
#   team:    like the profile (a manager doesn't get their reports' home addresses anyway)
#   hr:      search results are short (just enough to tell people apart); a full employee record is
#            like the profile. HR may see home addresses, but the AI doesn't need them, so they stay out.
PROFILE_FIELDS = ["id", "name", "email", "job_title", "department_id", "manager_id",
                  "employment_status", "start_date", "phone"]
TEAM_FIELDS = ["id", "name", "email", "job_title", "department_id", "start_date", "phone"]
SEARCH_FIELDS = ["id", "name", "job_title", "department_id", "employment_status"]
EMPLOYEE_FIELDS = PROFILE_FIELDS + ["termination_date"]
SEARCH_LIMIT = 10  # at most this many matches go to the AI
LEAVE_FIELDS = ["id", "leave_type", "start_date", "end_date", "status"]
SALARY_FIELDS = ["amount", "effective_date"]
REVIEW_FIELDS = ["period", "rating", "comments", "reviewer_id"]


# Which permission (from GET /me/permissions) each tool needs. A tool missing here is never offered.
TOOL_PERMISSIONS = {
    "get_my_profile": "view_own_profile",
    "get_my_leave": "view_own_leave",
    "get_my_salary": "view_own_salary",
    "get_my_reviews": "view_own_reviews",
    # managers (anyone with direct reports)
    "get_my_team": "view_reports_profiles",
    "get_team_leave": "view_reports_leave",
    # hr and admin
    "search_employees": "view_all_employees",
    "get_employee": "view_all_employees",
}


def keep_only(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Copy just the allowed fields, e.g. keep_only({"a": 1, "b": 2}, ["a"]) -> {"a": 1}."""
    return {name: record[name] for name in fields if name in record}


def as_json(data: Any) -> str:
    """JSON text for the AI, without spaces: every character costs room in its small context."""
    return json.dumps(data, separators=(",", ":"))


def keep_only_in_list(records: list[dict[str, Any]], fields: list[str]) -> str:
    """keep_only for every record of a list, as JSON text for the AI."""
    return as_json([keep_only(record, fields) for record in records])


def leave_rows(leave: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leave requests with only the allowed fields, plus the number of days."""
    rows = [keep_only(request, LEAVE_FIELDS) for request in leave]
    for row in rows:
        # Small models are bad at date maths, so we count the days for them (both ends included).
        row["calendar_days"] = (date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])).days + 1
    return rows


def approved_days_per_year(leave: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Add up the days of APPROVED leave, per year and per type: {"2026": {"annual": 15, "sick": 2}}.

    Small models get these sums wrong (they mix up years, or add badly), so the code does them.
    We walk through every day of the leave, so leave from 30 December to 2 January counts
    2 days in each year.
    """
    totals: dict[str, dict[str, int]] = {}
    for request in leave:
        if request["status"] != "approved":
            continue  # pending, rejected or cancelled leave was not taken
        day = date.fromisoformat(request["start_date"])
        last_day = date.fromisoformat(request["end_date"])
        while day <= last_day:
            year = totals.setdefault(str(day.year), {})
            year[request["leave_type"]] = year.get(request["leave_type"], 0) + 1
            day += timedelta(days=1)
    return totals


# ---------- Argument schemas (the AI's arguments are checked against these before a tool runs) ----------

class NoArgs(BaseModel):
    """For tools that take no arguments. extra="forbid": any argument the AI makes up is rejected."""

    model_config = ConfigDict(extra="forbid")


class NameSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)  # at least 2 letters: "a" would match almost everyone


class EmployeeId(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0)  # a real id is always a positive number


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
        return as_json(keep_only(employee, PROFILE_FIELDS))

    def get_my_leave() -> str:
        """Get the logged-in user's own leave.
        "approved_days_per_year" is the number of days of approved leave, per year and per type
        (annual, sick, unpaid or parental), already added up: for "how many days" questions, read
        the number from there. Do not add up the requests yourself.
        "requests" are the newest leave requests (at most 20): type, start and end dates, number
        of calendar days, and status (pending, approved, rejected or cancelled).
        Use it for ANY question about the user's own leave, past or future: "when was my last
        holiday?", "how many sick days did I take?", "was my leave approved?"."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        # Ask for up to 100 requests (the most the CRUD API gives), so the totals are complete,
        # but show the model only the 20 newest ones: the model can only read a little text.
        leave = crud_api.call(session, "GET", "/leave", params={"employee_id": employee_id, "limit": 100})
        return as_json({
            "approved_days_per_year": approved_days_per_year(leave),
            "requests": leave_rows(leave[:20]),
        })

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

    def team_of(manager_id: int) -> list[dict[str, Any]]:
        """The people who CURRENTLY report to this manager (terminated people are left out)."""
        people = crud_api.call(session, "GET", "/employees", params={"manager_id": manager_id, "limit": 100})
        return [person for person in people if person["employment_status"] == "active"]

    def get_my_team() -> str:
        """Get the people who currently report directly to the logged-in user (their team): name,
        email, job title, department id, start date and phone.
        Use it for ANY question about the user's team: "who is in my team?", "what is Ali's job title?"."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        return keep_only_in_list(team_of(employee_id), TEAM_FIELDS)

    def get_team_leave() -> str:
        """Get the CURRENT and UPCOMING leave of the people who report directly to the logged-in
        user (leave that ends today or later): employee name, type, start and end dates, number of
        calendar days, and status. "pending" means it is waiting for the user to approve or reject it.
        Use it for ANY question about the team's leave: "who is off next week?", "who is off in
        December?", "are there leave requests waiting for me?"."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        names = {person["id"]: person["name"] for person in team_of(employee_id)}
        # Newest first, so the upcoming leave comes first. Past leave is dropped: every row costs
        # room in the model's small context window (see app/llm.py).
        leave = crud_api.call(session, "GET", "/leave", params={"manager_id": employee_id, "limit": 30})
        today = date.today().isoformat()
        leave = [request for request in leave if request["end_date"] >= today]
        rows = leave_rows(leave)
        for row, request in zip(rows, leave):
            # The leave request only has an employee id. We add the name, so the model doesn't
            # have to match ids to names itself (and can't get it wrong).
            row["employee_name"] = names.get(request["employee_id"], "unknown")
        return as_json(rows)

    def department_names() -> dict[int, str]:
        """{1: "Engineering", 2: "HR", ...}, so the AI gets names instead of numbers."""
        return {department["id"]: department["name"] for department in crud_api.call(session, "GET", "/departments")}

    def search_employees(name: str) -> str:
        """Find employees by (part of) their name, e.g. "yusuf" or "Yusuf Brown".
        Use it FIRST whenever the user names a person.
        One match: you get their full profile (job title, department, manager, start date, phone...).
        Several matches: you get a short list. Show it and ask the user which one they mean."""
        # We ask for one more than we show, so we know if there are more.
        people = crud_api.call(session, "GET", "/employees", params={"name": name, "limit": SEARCH_LIMIT + 1})

        if len(people) == 0:
            return f'Nobody is called "{name}".'

        if len(people) == 1:
            # Small models are bad at chaining two tools (search, then get_employee),
            # so for one match we give the full profile straight away.
            return as_json({"one_match": full_profile(people[0])})

        departments = department_names()
        matches: list[dict[str, Any]] = []
        for person in people[:SEARCH_LIMIT]:
            match = keep_only(person, SEARCH_FIELDS)
            match["department"] = departments.get(match.pop("department_id"), "unknown")
            matches.append(match)
        result: dict[str, Any] = {"several_matches": matches}
        if len(people) > SEARCH_LIMIT:
            # A sentence, not a true/false flag: small models read sentences better.
            result["note"] = f"Showing {SEARCH_LIMIT} of more than {SEARCH_LIMIT} matches. Ask the user for the full name."
        return as_json(result)

    def full_profile(employee: dict[str, Any]) -> dict[str, Any]:
        """An employee's allowed fields, with the department and manager as names instead of ids."""
        profile = keep_only(employee, EMPLOYEE_FIELDS)
        profile["department"] = department_names().get(profile.pop("department_id"), "unknown")
        if profile["manager_id"] is not None:
            # Add the manager's name, so the model doesn't have to look it up (or guess it).
            manager = crud_api.call(session, "GET", f"/employees/{profile['manager_id']}")
            profile["manager_name"] = manager["name"]
        return profile

    def get_employee(employee_id: int) -> str:
        """Get one employee's full profile by their employee id: name, email, job title, department,
        manager (with name), employment status, start and termination date, and phone.
        The employee_id must come from search_employees or another tool result. Never guess an id."""
        return as_json(full_profile(crud_api.call(session, "GET", f"/employees/{employee_id}")))

    all_tools = [
        FunctionTool.from_defaults(fn=get_my_profile, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_leave, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_salary, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_reviews, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_team, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_team_leave, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=search_employees, fn_schema=NameSearch),
        FunctionTool.from_defaults(fn=get_employee, fn_schema=EmployeeId),
    ]

    # Only offer the tools this user may use, so the AI doesn't offer things that would fail.
    # This is only for a better experience: the CRUD API still checks every single call itself.
    return [tool for tool in all_tools if TOOL_PERMISSIONS.get(tool.metadata.get_name()) in permissions]
