# Schemas describe the shape of the data that goes IN and OUT of our API.
# FastAPI uses them to check every request automatically.
#
# "Create" = data the client sends to make a new record.
# "Update" = data the client sends to change a record (every field is optional).
# "Out"    = data we send back to the client.

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- Departments ----------

class DepartmentCreate(BaseModel):
    # extra="forbid" means: if the client sends a field we don't know, reject the request.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)


class DepartmentOut(BaseModel):
    # from_attributes=True lets Pydantic read data straight from our database objects.
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    deleted_at: Optional[datetime]  # empty = not deleted


# ---------- Employees ----------

class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    job_title: str = Field(min_length=1, max_length=100)
    department_id: int
    manager_id: Optional[int] = None
    start_date: date
    # Notice: there is no employment_status here.
    # New employees are always "active". Only the terminate endpoint can change it.


class EmployeeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[str] = Field(default=None, min_length=3, max_length=200)
    job_title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    department_id: Optional[int] = None
    manager_id: Optional[int] = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    job_title: str
    department_id: int
    manager_id: Optional[int]
    employment_status: str
    start_date: date
    termination_date: Optional[date]
    phone: Optional[str]
    home_address: Optional[str]  # hidden (None) for people who may not see it, see app/permissions.py
    created_at: datetime
    updated_at: datetime


class ContactUpdate(BaseModel):
    """The contact details an employee may change themselves."""

    model_config = ConfigDict(extra="forbid")

    # pattern = only digits, spaces, and the characters + - ( )
    phone: Optional[str] = Field(default=None, max_length=30, pattern=r"^[0-9+\-() ]+$")
    home_address: Optional[str] = Field(default=None, min_length=1, max_length=300)


class TerminateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The last day the employee works here. It can be today, in the past, or in the future.
    termination_date: date


# ---------- Leave ----------

# Literal = only these exact words are accepted. Anything else is rejected with 422.
LeaveType = Literal["annual", "sick", "unpaid", "parental"]
LeaveStatus = Literal["pending", "approved", "rejected", "cancelled"]


class LeaveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Notice: there is no employee_id here. You can only ask leave for YOURSELF,
    # and who "yourself" is comes from your login token.
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: Optional[str] = Field(default=None, max_length=500)


class LeaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type: str
    start_date: date
    end_date: date
    reason: Optional[str]  # hidden (None) for people who may not see it, see app/permissions.py
    status: str
    approver_id: Optional[int]
    decided_at: Optional[datetime]
    created_at: datetime


# ---------- Salaries ----------

class SalaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Yearly salary in US dollars. gt=0 = must be more than 0.
    # Decimal (not float) keeps money exact. max_digits/decimal_places match the database column.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    # The day the new salary starts. It can be in the future (a planned raise).
    effective_date: date


class SalaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    amount: Decimal  # sent as a string like "85000.00", so no cents get lost
    effective_date: date
    created_at: datetime


# ---------- Performance reviews ----------

class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Notice: there is no reviewer_id here. The reviewer is always YOU, taken from your login token.
    # pattern = the text must look like "2025-H1" or "2025-H2" (4 digits, "-H", then 1 or 2).
    period: str = Field(pattern=r"^\d{4}-H[12]$")
    rating: int = Field(ge=1, le=5)  # 1 = poor, 3 = meets expectations, 5 = outstanding
    comments: Optional[str] = Field(default=None, max_length=2000)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    reviewer_id: int
    period: str
    rating: int
    comments: Optional[str]
    deleted_at: Optional[datetime]  # empty = not deleted
    created_at: datetime


# ---------- Roles ----------

Role = Literal["employee", "manager", "hr", "admin"]


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class RoleOut(BaseModel):
    employee_id: int
    user_id: int
    role: str


class RoleInfo(BaseModel):
    name: str
    description: str
    permissions: list[str]
    descriptions: dict[str, str]  # each permission in plain words, e.g. {"submit_leave": "Ask for leave ..."}


class PermissionsOut(BaseModel):
    kind: str             # "user" or "superuser"
    role: Optional[str]   # superusers have no role
    is_manager: bool      # True if someone reports directly to you
    permissions: list[str]
    descriptions: dict[str, str]  # each permission in plain words


# ---------- Audit log ----------

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_kind: str
    account_id: int
    action: str
    table_name: str
    record_id: int
    before: Optional[dict[str, Any]]
    after: Optional[dict[str, Any]]
    source: str
    pending_action_id: Optional[int]
    created_at: datetime


# ---------- Pending actions (big changes that the user must confirm) ----------

# The only actions that can wait for confirmation. Each one runs an existing endpoint.
ActionName = Literal[
    "terminate_employee",  # target_id = employee id, args = {"termination_date": "2026-12-31"}
    "change_salary",       # target_id = employee id, args = {"amount": "80000", "effective_date": "2027-01-01"}
    "change_role",         # target_id = employee id, args = {"role": "manager"}
    "change_manager",      # target_id = employee id, args = {"manager_id": 12}
    "delete_department",   # target_id = department id, args = {}
    "delete_review",       # target_id = review id, args = {}
]


class ManagerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_id: Optional[int]  # None = no manager


class NoArgs(BaseModel):
    """For actions that need no arguments. extra="forbid" still rejects anything sent."""

    model_config = ConfigDict(extra="forbid")


class PendingActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionName
    target_id: int
    args: dict[str, Any] = Field(default_factory=dict)  # checked against the action's own schema


class PendingActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    target_id: int
    args: dict[str, Any]
    preview: dict[str, Any]
    status: str
    created_at: datetime
    expires_at: datetime
    decided_at: Optional[datetime]


class ConfirmOut(BaseModel):
    action: PendingActionOut
    result: dict[str, Any]  # what the real endpoint returned, e.g. the terminated employee


# ---------- Login ----------

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class AccessTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    # Who is logged in. Notice: no password_hash here. It never leaves the server.
    id: int
    email: str
    kind: str  # "user" or "superuser"
    employee_id: Optional[int] = None  # superusers are not linked to an employee
    role: Optional[str] = None  # "employee", "manager", "hr" or "admin". Superusers have no role: they skip all checks.
