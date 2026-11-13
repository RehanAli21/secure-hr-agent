# Schemas describe the shape of the data that goes IN and OUT of our API.
# FastAPI uses them to check every request automatically.
#
# "Create" = data the client sends to make a new record.
# "Update" = data the client sends to change a record (every field is optional).
# "Out"    = data we send back to the client.

from datetime import date, datetime
from typing import Optional

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
