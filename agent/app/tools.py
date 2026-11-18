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

import inspect
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Literal, Optional

from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field

from app import crud_api, policies
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
    # everyone: every role has view_own_profile. The tool itself only searches the policies
    # that the user's role may read (see app/policies.py).
    "search_policies": "view_own_profile",
    "get_roles": "view_own_profile",  # everyone: GET /roles is open to every logged-in user
    # analysts, hr and admin (the whole company); managers have their own permission, see below
    "get_analytics": "view_analytics",
    # changes
    "submit_leave_request": "submit_leave",
    "cancel_leave_request": "cancel_own_leave",
    "approve_leave_request": "decide_reports_leave",
    "reject_leave_request": "decide_reports_leave",
    # hr and admin, big changes: they only PROPOSE, the user confirms in the app
    "update_employee_salary": "change_salary",
    "terminate_employee": "terminate_employee",
    "change_employee_role": "change_role",
    "change_employee_manager": "update_employee",
}

# A few tools are also offered with a SECOND permission. Managers see numbers about their own
# team (view_team_analytics): the CRUD API decides whether it is the company or the team.
ALSO_ALLOWED_WITH = {
    "get_analytics": "view_team_analytics",
}

# The tools that CHANGE something. Every other tool only reads.
# The agent allows at most one change per message (see app/agent.py), and later the tools that
# need the user's confirmation will be marked here too. The model never decides any of this.
WRITE_TOOLS = {"submit_leave_request", "cancel_leave_request", "approve_leave_request", "reject_leave_request",
               "update_employee_salary", "terminate_employee", "change_employee_role", "change_employee_manager"}


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


def chart_for(answer: dict[str, Any], department: Optional[str], leave_type: Optional[str]) -> Optional[dict[str, Any]]:
    """The chart for an answer from GET /analytics, or None when there is nothing to draw.

    Built by CODE from the CRUD API's answer, never from the model's words, so the chart always
    shows the real numbers, even if the model gets them wrong in its text.
    """
    if answer["group_by"] is None or len(answer["rows"]) < 2:
        return None  # one number needs no chart: the answer's text says it
    # What the chart is about, in a few words under its title, e.g. "by month · sick leave".
    details = [f"by {answer['group_by'].replace('_', ' ')}"]
    if leave_type is not None:
        details.append(f"{leave_type} leave")
    if department is not None:
        details.append(department)
    if answer["from_date"] is not None:
        details.append(f"{answer['from_date']} to {answer['to_date']}")
    if answer["scope"] == "team":
        details.append("your team")
    return {
        "title": answer["description"],
        "details": " · ".join(details),
        # Over time (months, years): a line. Groups side by side (departments...): bars.
        "kind": "line" if answer["group_by"] in ("month", "year") else "bar",
        "points": [{"label": row["group"], "value": row["value"]} for row in answer["rows"]],
    }


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


class PolicyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=200)


class WhichRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Left out (None): a one-line summary of every role. Given: that role's permissions.
    # One role at a time, because all four with all their permissions is a lot of text.
    role: Optional[Literal["employee", "manager", "analyst", "hr", "admin"]] = None


class AnalyticsQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The same fixed lists as the CRUD API (GET /analytics): the model picks from them, it never
    # writes a query. The CRUD API checks again, and says which combinations make no sense.
    metric: Literal["headcount", "new_hires", "leavers", "average_salary", "payroll_total",
                    "leave_days", "average_rating"]
    # Required, with "none" for one total: Ollama hides Optional arguments' choices from the
    # model, and with an optional group_by it left it out of "sick days per month".
    group_by: Literal["none", "department", "month", "year", "leave_type", "period"]
    department: Optional[str] = Field(default=None, min_length=1, max_length=100)
    leave_type: Optional[Literal["annual", "sick", "unpaid", "parental"]] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None


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


# The big changes say WHO with one text: a name ("Sam Lee") or an employee id ("87").
# The tool finds the person itself: small models are bad at chaining "search, then use the id".
WHO = Field(min_length=1, max_length=100)


class NewSalary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: str = WHO
    # Decimal (not float) keeps money exact. The same limits as the CRUD API.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    effective_date: date  # the day the new salary starts (it can be in the future)


class Termination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: str = WHO
    termination_date: date  # their last working day (it can be in the past, today or in the future)


class NewRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: str = WHO
    role: Literal["employee", "manager", "analyst", "hr", "admin"]  # a real list of choices, like the CRUD API


class NewManager(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: str = WHO
    # The new manager: a name, an id, or "nobody". Required on purpose: if the model forgets it,
    # the call is refused, instead of quietly proposing "no manager".
    new_manager: str = WHO


# ---------- The tools ----------

def make_tool(fn: Callable[..., str], fn_schema: type[BaseModel]) -> FunctionTool:
    """Turn one of the functions below into a tool for the AI. Its docstring is what the AI reads.

    We give the description ourselves, because LlamaIndex would otherwise start it with the Python
    signature ("get_my_profile() -> str"), and keep the 8 spaces in front of every docstring line.
    The AI doesn't need either, and every tool description is sent with EVERY message: HR has 16
    tools, and the model can only read 4096 tokens at once (see app/llm.py).
    """
    return FunctionTool.from_defaults(fn=fn, fn_schema=fn_schema, description=inspect.cleandoc(fn.__doc__ or ""))


def make_tools(session: ChatSession, permissions: list[str]) -> list[FunctionTool]:
    """Build the tools for one logged-in user: only the ones their permissions allow."""

    def my_employee_id() -> Optional[int]:
        """Which employee is logged in? Asked from the CRUD API, never from the AI."""
        return crud_api.call(session, "GET", "/me").get("employee_id")

    def get_my_profile() -> str:
        """The user's own profile: name, email, job title, department id, manager id, employment
        status, start date and phone.
        Use it for ANY question about the user's own job, department, manager or start date."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        employee = crud_api.call(session, "GET", f"/employees/{employee_id}")
        return as_json(keep_only(employee, PROFILE_FIELDS))

    def get_my_leave() -> str:
        """The user's own leave. "approved_days_per_year": the days of approved leave per year and
        type, already added up. Read "how many days" answers from there; never add up requests yourself.
        "requests": the 20 newest requests (type, dates, calendar_days, status).
        Use it for ANY question about the user's own leave: "when was my last holiday?", "how many sick
        days did I take?", "was my leave approved?"."""
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
        """The user's own salary history, newest first. The current salary is the newest one whose
        effective_date is not after today; a later one is a raise that has not started yet.
        The amounts have no currency: never add a currency sign."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        salaries = crud_api.call(session, "GET", f"/employees/{employee_id}/salaries")
        return keep_only_in_list(salaries, SALARY_FIELDS)

    def get_my_reviews() -> str:
        """The user's own performance reviews, newest first: period (like "2025-H2"), rating
        from 1 to 5, comments, and the reviewer's employee id."""
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
        """The people who report directly to the user (their team): name, email, job title,
        department id, start date and phone.
        Use it for ANY question about the user's team: "who is in my team?", "what is Ali's job title?"."""
        employee_id = my_employee_id()
        if employee_id is None:
            return "This account is not linked to an employee."
        return keep_only_in_list(team_of(employee_id), TEAM_FIELDS)

    def get_team_leave() -> str:
        """Current and upcoming leave of the user's team: employee name, type, dates,
        calendar_days and status ("pending" = waiting for the user to approve or reject it).
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
        One match: you get their full profile (job title, department, manager, start date...).
        Several matches: a short list. Show it and ask the user which one they mean."""
        # We ask for one more than we show, so we know if there are more.
        people = crud_api.call(session, "GET", "/employees", params={"name": name, "limit": SEARCH_LIMIT + 1})

        if len(people) == 0:
            return f'Nobody is called "{name}".'

        if len(people) == 1:
            # Small models are bad at chaining two tools (search, then get_employee),
            # so for one match we give the full profile straight away.
            return as_json({"one_match": full_profile(people[0])})

        result: dict[str, Any] = {"several_matches": short_list(people[:SEARCH_LIMIT])}
        if len(people) > SEARCH_LIMIT:
            # A sentence, not a true/false flag: small models read sentences better.
            result["note"] = f"Showing {SEARCH_LIMIT} of more than {SEARCH_LIMIT} matches. Ask the user for the full name."
        return as_json(result)

    def short_list(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Just enough to tell people apart (name, job title, department, id), to ask "which one?"."""
        departments = department_names()
        matches: list[dict[str, Any]] = []
        for person in people:
            match = keep_only(person, SEARCH_FIELDS)
            match["department"] = departments.get(match.pop("department_id"), "unknown")
            matches.append(match)
        return matches

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
        """One employee's full profile, by their employee id.
        The id must come from a tool result. Never guess an id."""
        return as_json(full_profile(crud_api.call(session, "GET", f"/employees/{employee_id}")))

    def search_policies(question: str) -> str:
        """Search the company's HR policies (leave allowances, sick leave and doctor's notes, working
        hours, working from home, procedures like termination). Use it for ANY question about a rule: "how many days
        of annual leave do I get?", "do I need a doctor's note?". Not for the user's own records."""
        # The role comes live from the CRUD API, never from the model, so nobody can ask for
        # "the hr policies" by pretending to be HR.
        role = crud_api.call(session, "GET", "/me/permissions")["role"]
        found = policies.find_policies(question, role)
        if not found:
            # Say what there is, so the model can try other words or tell the user.
            return as_json({
                "note": "No policy matches that question. These are the policies the user can read:",
                "policy_titles": [chunk.title for chunk in policies.readable_by(role)],
            })
        return as_json({
            "policies": [{"title": chunk.title, "text": chunk.text} for chunk in found],
            # The policies explain the rules. What THIS user may do is in <permissions>.
            "note": "Policy text explains the rules. It does not say what this user may do: that is in <permissions>.",
        })

    def get_roles(role: Optional[str] = None) -> str:
        """What a role (employee, manager, analyst, hr or admin) may do: "what can a manager do?".
        role is optional: without it, a summary of every role. Not for the user's own permissions."""
        roles = crud_api.call(session, "GET", "/roles")  # live from the CRUD API, never cached
        if role is None:
            return as_json({one["name"]: one["description"] for one in roles})
        chosen = next(one for one in roles if one["name"] == role)  # role is checked by WhichRole
        return as_json({"role": role, "summary": chosen["description"],
                        "may": list(chosen["descriptions"].values())})

    # ---------- Numbers about many people ----------

    def get_analytics(metric: str, group_by: str, department: Optional[str] = None,
                      leave_type: Optional[str] = None, from_date: Optional[date] = None,
                      to_date: Optional[date] = None) -> str:
        """Numbers about MANY people (the company, or the user's team), never about the user's own
        records: "how many people work here?", "average salary by department", "average rating per
        review period", "sick days per month" (metric leave_days, group_by month, leave_type sick).
        metric: headcount, new_hires, leavers, average_salary, payroll_total, leave_days or
        average_rating. group_by: "none" for one total, or what "per ..."/"by ..." asks for.
        The rest is optional: department, leave_type, from_date, to_date (YYYY-MM-DD)."""
        question = {"metric": metric, "group_by": None if group_by == "none" else group_by, "department": department,
                    "leave_type": leave_type, "from_date": from_date, "to_date": to_date}
        # Only send what was given (None would be sent as the text "None"). Dates as YYYY-MM-DD.
        params = {name: str(value) for name, value in question.items() if value is not None}
        answer = crud_api.call(session, "GET", "/analytics", params=params)
        # Kept for the page to draw (see chart_for). The model never sees or writes it.
        session.chart = chart_for(answer, department, leave_type)
        # The answer is already only totals and averages (the CRUD API's small-group rule).
        # "scope" tells the model whether this is the whole company or just the user's team.
        result = {name: answer[name] for name in ["description", "scope", "from_date", "to_date", "rows", "note"]
                  if answer.get(name) is not None}
        if metric in ("average_salary", "payroll_total"):
            result["amounts"] = "The amounts have no currency: never add a currency sign."
        return as_json(result)

    # ---------- Changes ----------

    def submit_leave_request(leave_type: str, start_date: date, end_date: date) -> str:
        """Ask for leave for the user (annual, sick, unpaid or parental), from start_date
        to end_date, both days included. Only when the user asks to take leave ("book me a sick day
        tomorrow"). If the type or a date is missing, ask the user.
        Work out the dates yourself from today's date ("next Friday") and send them as YYYY-MM-DD.
        Never ask the user for a date format. Never use it to answer a question."""
        new_request = {"leave_type": leave_type, "start_date": start_date.isoformat(),
                       "end_date": end_date.isoformat()}
        created = crud_api.call(session, "POST", "/leave", json=new_request)
        return as_json({
            "submitted": leave_rows([created])[0],
            "note": "The leave request was sent. It is pending until the manager approves it.",
        })

    def cancel_leave_request(start_date: Optional[date] = None) -> str:
        """Cancel the user's own leave that starts on start_date (YYYY-MM-DD). start_date is optional.
        Only when the user asks to cancel leave.
        If the user doesn't say the exact start date ("cancel my leave", "cancel my February leave"),
        call it WITHOUT start_date: it cancels nothing and lists the leave that can be cancelled.
        If exactly one fits what the user said, call it again with that start_date; otherwise ask
        which one. Never ask the user for a date."""
        # "start_date is optional" must be written here: the model can't see it anywhere else
        # (Ollama drops the "or null" part of the schema). Without it, it asked the user for a date.
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
        """Approve a pending leave request of the user's team, chosen by employee_name (part of the
        name is fine) and/or start_date (the first day, YYYY-MM-DD). Both are optional.
        Only when the user asks to approve leave ("approve Adam's leave").
        If the user doesn't say whose leave ("approve leave", "approve the waiting request"), call it
        with NO arguments: it approves nothing and lists the waiting requests, so you can ask which one."""
        return decide_team_leave("approve", employee_name, start_date)

    def reject_leave_request(employee_name: Optional[str] = None, start_date: Optional[date] = None) -> str:
        """Reject a pending leave request of the user's team ("reject Chloe's leave").
        The arguments work exactly like approve_leave_request: both are optional."""
        return decide_team_leave("reject", employee_name, start_date)

    # ---------- Big changes: the user must confirm them ----------
    #
    # These tools change NOTHING. They ask the CRUD API to store a "pending action" (POST /actions).
    # The CRUD API builds a preview from the real data, and the chat page shows it with a Confirm
    # button. Only that button (a person clicking) runs the change. The CRUD API refuses to run it
    # for the agent, so even a tricked model can't skip this step.

    def find_one_employee(who: str, argument: str) -> tuple[Optional[dict[str, Any]], str]:
        """Find exactly ONE employee from a name or an id. Never guesses.
        argument = the tool argument `who` came from ("employee" or "new_manager"), for the message.
        Returns (the employee, "") or (None, a message for the model, e.g. the choices to ask about)."""
        if who.strip().isdigit():
            try:
                return crud_api.call(session, "GET", f"/employees/{who.strip()}"), ""
            except crud_api.CrudApiError as error:
                if error.status_code == 404:
                    return None, f"Nothing was proposed: nobody has the employee id {who}."
                raise
        people = crud_api.call(session, "GET", "/employees", params={"name": who, "limit": SEARCH_LIMIT + 1})
        if len(people) == 1:
            return people[0], ""
        if len(people) == 0:
            return None, f'Nothing was proposed: nobody is called "{who}".'
        # Say exactly which argument gets the id, with a real example: the model repeated the
        # name (and got the same list again) when the note only said "use the id".
        return None, as_json({
            "note": f'Nothing was proposed: several people are called "{who}". Ask the user which one. '
                    f'When they answer, call this tool again with {argument} set to that person\'s id '
                    f'from this list, for example {argument}="{people[0]["id"]}".',
            "several_matches": short_list(people[:SEARCH_LIMIT]),
        })

    def propose(action: str, target_id: int, args: dict[str, Any]) -> str:
        """Store a pending action, and keep it on the session so the page can show the Confirm button."""
        pending = crud_api.call(session, "POST", "/actions",
                                json={"action": action, "target_id": target_id, "args": args})
        session.proposed_action = {"id": pending["id"], "summary": pending["preview"]["summary"],
                                   "expires_at": pending["expires_at"]}
        return as_json({
            # Status first: the model wrote "Terminated Sam Lee" (as if done) when the result began
            # with the summary itself.
            "status": "PROPOSED, NOT DONE YET",
            "proposed_change": pending["preview"]["summary"],
            # Written so the model can repeat it to the user word for word (small models often do).
            "note": "Nothing has changed yet. Please check the details on the card below and click "
                    "Confirm within 5 minutes. Typing yes in the chat does not confirm it.",
        })

    def update_employee_salary(employee: str, amount: Decimal, effective_date: date) -> str:
        """Propose a new salary: the amount (a number, no currency) from effective_date
        (YYYY-MM-DD). Only when the user asks ("give Sam Lee 90000 from 1 January").
        employee: a name as the user wrote it, or an employee id. No need to search first."""
        person, problem = find_one_employee(employee, "employee")
        if person is None:
            return problem
        return propose("change_salary", person["id"],
                       {"amount": str(amount), "effective_date": effective_date.isoformat()})

    def terminate_employee(employee: str, termination_date: date) -> str:
        """Propose to terminate an employee. termination_date is their last working day
        (YYYY-MM-DD). Only when the user asks ("terminate Sam Lee, last day 31 December").
        No last working day given: ask the user for it, never pick one.
        employee: a name as the user wrote it, or an employee id. No need to search first."""
        person, problem = find_one_employee(employee, "employee")
        if person is None:
            return problem
        return propose("terminate_employee", person["id"], {"termination_date": termination_date.isoformat()})

    def change_employee_role(employee: str, role: str) -> str:
        """Propose a new role: employee, manager, analyst, hr or admin (what they may see and do in
        this system). Use it for "make Sam Lee a manager" or "give Sam Lee the hr role".
        employee: a name as the user wrote it, or an employee id. No need to search first."""
        person, problem = find_one_employee(employee, "employee")
        if person is None:
            return problem
        return propose("change_role", person["id"], {"role": role})

    def change_employee_manager(employee: str, new_manager: str) -> str:
        """Propose who an employee reports to: "Sam Lee now reports to Kim Park", or
        "Sam Lee no longer has a manager" (new_manager "nobody").
        employee and new_manager: a name as the user wrote it, or an employee id."""
        person, problem = find_one_employee(employee, "employee")
        if person is None:
            return problem
        if new_manager.strip().lower() == "nobody":
            return propose("change_manager", person["id"], {"manager_id": None})
        manager, problem = find_one_employee(new_manager, "new_manager")
        if manager is None:
            return problem
        return propose("change_manager", person["id"], {"manager_id": manager["id"]})

    all_tools = [
        make_tool(get_my_profile, NoArgs),
        make_tool(get_my_leave, NoArgs),
        make_tool(get_my_salary, NoArgs),
        make_tool(get_my_reviews, NoArgs),
        make_tool(get_my_team, NoArgs),
        make_tool(get_team_leave, NoArgs),
        make_tool(search_employees, NameSearch),
        make_tool(get_employee, EmployeeId),
        make_tool(search_policies, PolicyQuestion),
        make_tool(get_roles, WhichRole),
        make_tool(get_analytics, AnalyticsQuestion),
        make_tool(submit_leave_request, NewLeaveRequest),
        make_tool(cancel_leave_request, LeaveStartDate),
        make_tool(approve_leave_request, WhichTeamLeave),
        make_tool(reject_leave_request, WhichTeamLeave),
        make_tool(update_employee_salary, NewSalary),
        make_tool(terminate_employee, Termination),
        make_tool(change_employee_role, NewRole),
        make_tool(change_employee_manager, NewManager),
    ]

    # Only offer the tools this user may use, so the AI doesn't offer things that would fail.
    # This is only for a better experience: the CRUD API still checks every single call itself.
    def may_use(name: str) -> bool:
        return TOOL_PERMISSIONS.get(name) in permissions or ALSO_ALLOWED_WITH.get(name) in permissions

    return [tool for tool in all_tools if may_use(tool.metadata.get_name())]
