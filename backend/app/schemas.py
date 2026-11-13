# Schemas describe the shape of the data that goes IN and OUT of our API.
# FastAPI uses them to check every request automatically.
#
# "Create" = data the client sends to make a new record.
# "Update" = data the client sends to change a record (every field is optional).
# "Out"    = data we send back to the client.

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

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
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime


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
