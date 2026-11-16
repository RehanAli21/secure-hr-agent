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
from typing import Any, Literal, Optional

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
    # changes
    "submit_leave_request": "submit_leave",
    "cancel_leave_request": "cancel_own_leave",
    "approve_leave_request": "decide_reports_leave",
    "reject_leave_request": "decide_reports_leave",
}

# The tools that CHANGE something. Every other tool only reads.
# The agent allows at most one change per message (see app/agent.py), and later the tools that
# need the user's confirmation will be marked here too. The model never decides any of this.
WRITE_TOOLS = {"submit_leave_request", "cancel_leave_request", "approve_leave_request", "reject_leave_request"}


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


def can_be_cancelled(request: dict[str, Any]) -> bool:
    """The CRUD API's rule: pending leave, or approved leave that has not started yet.
    We only use it to find the right request. The CRUD API still checks it when we cancel."""
    if request["status"] == "pending":
        return True
    return request["status"] == "approved" and date.fromisoformat(request["start_date"]) > date.today()


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


class NewLeaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A real list of choices: the model can't invent a leave type.
    leave_type: Literal["annual", "sick", "unpaid", "parental"]
    # Pydantic turns "2027-01-05" into a real date, and refuses anything that isn't a date.
    start_date: date
    end_date: date
    # Notice: no reason. It can be private (e.g. medical), so it stays out of the chat.
    # And no employee id: you can only ask leave for yourself, the CRUD API takes you from the token.


class LeaveStartDate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Which leave to cancel: the one that starts on this day. Not an id: the user says
    # "cancel my leave from 5 January", and the CRUD API never lets two pending or approved
    # leave requests overlap, so at most one of them starts on any day.
    # Left out (None): the tool only lists the leave that can be cancelled, and cancels nothing.
    start_date: Optional[date] = None


class WhichTeamLeave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Which team member's leave to approve or reject: (part of) their name, and/or the first day.
    # Not an id, for the same reason as LeaveStartDate. Both left out: the tool only lists the
    # requests that are waiting, and decides nothing.
    employee_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    start_date: Optional[date] = None


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

    # ---------- Changes ----------

    def submit_leave_request(leave_type: str, start_date: date, end_date: date) -> str:
        """Ask for leave for the logged-in user: annual, sick, unpaid or parental leave, from
        start_date to end_date (both days included).
        Only use it when the user asks to take leave ("I want annual leave from 5 to 9 January",
        "book me a sick day tomorrow"). If the type or a date is missing, ask the user first.
        Work out the dates yourself from today's date ("tomorrow", "next Friday"), and send them
        as YYYY-MM-DD. Never ask the user to write a date in a special format.
        Never use it to answer a question about leave."""
        new_request = {"leave_type": leave_type, "start_date": start_date.isoformat(),
                       "end_date": end_date.isoformat()}
        created = crud_api.call(session, "POST", "/leave", json=new_request)
        return as_json({
            "submitted": leave_rows([created])[0],
            "note": "The leave request was sent. It is pending until the manager approves it.",
        })

    def cancel_leave_request(start_date: Optional[date] = None) -> str:
        """Cancel the logged-in user's own leave that starts on start_date (YYYY-MM-DD).
        Leave can be cancelled while it is pending, or when it is approved but has not started yet.
        Only use it when the user asks to cancel leave ("cancel my leave from 5 January").
        If the user doesn't say the exact start date ("cancel my February leave", "cancel my
        leave"), call it WITHOUT start_date: it cancels nothing, and lists the leave that can be
        cancelled. If exactly one of them fits what the user said, call it again with that
        start_date. Otherwise list them and ask which one. Never ask the user for a date format.
        Never use it to answer a question about leave."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        leave = crud_api.call(session, "GET", "/leave", params={"employee_id": employee_id, "limit": 100})
        cancellable = [request for request in leave if can_be_cancelled(request)]
        if not cancellable:
            return "The user has no leave that can be cancelled."

        # No date: only show the choices (just the leave that CAN be cancelled, not the whole history).
        if start_date is None:
            return as_json({
                "note": "Nothing was cancelled yet. Which one does the user mean?",
                "leave_that_can_be_cancelled": leave_rows(cancellable),
            })

        matches = [request for request in cancellable if request["start_date"] == start_date.isoformat()]
        if not matches:
            # Don't guess: give the model the choices, so it can ask the user which one they mean.
            return as_json({
                "note": f"Nothing was cancelled: no leave that can be cancelled starts on "
                        f"{start_date.isoformat()}. Ask the user which of these they mean.",
                "leave_that_can_be_cancelled": leave_rows(cancellable),
            })

        cancelled = crud_api.call(session, "POST", f"/leave/{matches[0]['id']}/cancel")
        return as_json({"cancelled": leave_rows([cancelled])[0], "note": "The leave was cancelled."})

    def waiting_team_leave() -> list[dict[str, Any]]:
        """The pending leave requests of my direct reports, with the employee's name added.
        Each row keeps its "id", so we can approve or reject it (the model never has to)."""
        employee_id = my_employee_id()
        if employee_id is None:
            return []
        names = {person["id"]: person["name"] for person in team_of(employee_id)}
        leave = crud_api.call(session, "GET", "/leave",
                              params={"manager_id": employee_id, "status": "pending", "limit": 100})
        rows = leave_rows(leave)
        for row, request in zip(rows, leave):
            row["employee_name"] = names.get(request["employee_id"], "unknown")
        return rows

    def decide_team_leave(decision: str, employee_name: Optional[str], start_date: Optional[date]) -> str:
        """Shared by approve_leave_request and reject_leave_request. decision is "approve" or "reject"."""
        waiting = waiting_team_leave()
        if not waiting:
            return "No leave requests are waiting for the user to approve or reject."

        # Keep the requests that fit what the model gave: part of the name, and/or the first day.
        matches = waiting
        if employee_name is not None:
            matches = [row for row in matches if employee_name.lower() in row["employee_name"].lower()]
        if start_date is not None:
            matches = [row for row in matches if row["start_date"] == start_date.isoformat()]

        # Only act when the model said which one, and exactly one request fits. Otherwise: never
        # guess. Give the model the choices, so it can ask the user which one they mean.
        if (employee_name is None and start_date is None) or len(matches) != 1:
            return as_json({
                "note": f"Nothing was done yet. Which leave request does the user want to {decision}?",
                "waiting_for_a_decision": matches if matches else waiting,
            })

        chosen = matches[0]
        decided = crud_api.call(session, "POST", f"/leave/{chosen['id']}/{decision}")
        row = leave_rows([decided])[0]
        row["employee_name"] = chosen["employee_name"]
        return as_json({"decided": row, "note": f"The leave request was {row['status']}."})

    def approve_leave_request(employee_name: Optional[str] = None, start_date: Optional[date] = None) -> str:
        """Approve a pending leave request of someone who reports to the logged-in user.
        Say whose leave it is with employee_name (part of the name is fine, e.g. "Adam") and/or
        start_date (the first day of the leave, YYYY-MM-DD).
        Only use it when the user asks to approve leave ("approve Adam's leave").
        If the user doesn't say which request, call it with no arguments: it approves nothing, and
        lists the requests that are waiting, so you can ask which one.
        Never use it to answer a question about leave."""
        return decide_team_leave("approve", employee_name, start_date)

    def reject_leave_request(employee_name: Optional[str] = None, start_date: Optional[date] = None) -> str:
        """Reject a pending leave request of someone who reports to the logged-in user.
        Say whose leave it is with employee_name (part of the name is fine, e.g. "Adam") and/or
        start_date (the first day of the leave, YYYY-MM-DD).
        Only use it when the user asks to reject leave ("reject Chloe's leave request").
        If the user doesn't say which request, call it with no arguments: it rejects nothing, and
        lists the requests that are waiting, so you can ask which one.
        Never use it to answer a question about leave."""
        return decide_team_leave("reject", employee_name, start_date)

    all_tools = [
        FunctionTool.from_defaults(fn=get_my_profile, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_leave, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_salary, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_reviews, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_my_team, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=get_team_leave, fn_schema=NoArgs),
        FunctionTool.from_defaults(fn=search_employees, fn_schema=NameSearch),
        FunctionTool.from_defaults(fn=get_employee, fn_schema=EmployeeId),
        FunctionTool.from_defaults(fn=submit_leave_request, fn_schema=NewLeaveRequest),
        FunctionTool.from_defaults(fn=cancel_leave_request, fn_schema=LeaveStartDate),
        FunctionTool.from_defaults(fn=approve_leave_request, fn_schema=WhichTeamLeave),
        FunctionTool.from_defaults(fn=reject_leave_request, fn_schema=WhichTeamLeave),
    ]

    # Only offer the tools this user may use, so the AI doesn't offer things that would fail.
    # This is only for a better experience: the CRUD API still checks every single call itself.
    return [tool for tool in all_tools if TOOL_PERMISSIONS.get(tool.metadata.get_name()) in permissions]
