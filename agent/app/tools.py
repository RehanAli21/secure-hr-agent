# The tools: the ONLY things the AI can do.
#
# Each tool is one narrow business action ("get my profile"), never "run any query".
# A tool calls the CRUD API as the logged-in user, so the CRUD API decides what is allowed.
#
# How the AI never sees the token:
#   make_tools(session) builds the tool functions INSIDE itself, so they can use `session`
#   without it being a parameter. The AI only sees the parameters (none here), so it can't
#   read the token, and it can't pass in someone else's.

import json
from typing import Any

from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, ConfigDict

from app import crud_api
from app.sessions import ChatSession

# Field allowlist: ONLY these fields of an employee reach the AI.
# home_address is left out on purpose: the AI doesn't need it to answer questions about your job.
PROFILE_FIELDS = ["id", "name", "email", "job_title", "department_id", "manager_id",
                  "employment_status", "start_date", "phone"]


def keep_only(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Copy just the allowed fields, e.g. keep_only({"a": 1, "b": 2}, ["a"]) -> {"a": 1}."""
    return {name: record[name] for name in fields if name in record}


class NoArgs(BaseModel):
    """For tools that take no arguments. extra="forbid": any argument the AI makes up is rejected."""

    model_config = ConfigDict(extra="forbid")


def make_tools(session: ChatSession) -> list[FunctionTool]:
    """Build the tools for one logged-in user."""

    def get_my_profile() -> str:
        """Get the logged-in user's own employee profile: name, email, job title,
        department id, manager id, employment status, start date and phone.
        Use it for any question about the user's own job, department, manager or start date."""
        me = crud_api.call(session, "GET", "/me")
        if me.get("employee_id") is None:
            return "This account is not linked to an employee."
        employee = crud_api.call(session, "GET", f"/employees/{me['employee_id']}")
        return json.dumps(keep_only(employee, PROFILE_FIELDS))

    return [
        FunctionTool.from_defaults(fn=get_my_profile, fn_schema=NoArgs),
    ]
