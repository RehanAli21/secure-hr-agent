from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# How to read a line like:   name: Mapped[str] = mapped_column(String, unique=True)
#   name                -> the column name
#   Mapped[str]         -> in Python this value is a str
#   Mapped[Optional[x]] -> the value can also be empty (None, NULL in the database)
#   mapped_column(...)  -> extra database settings, like unique or a foreign key


def utc_now() -> datetime:
    """The current time in UTC. We store all times in UTC to avoid time zone mix-ups."""
    return datetime.now(timezone.utc)


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


class Employee(Base):
    __tablename__ = "employees"

    # A rule the database enforces: nobody can leave before they started.
    __table_args__ = (
        CheckConstraint("termination_date >= start_date", name="employee_terminated_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    job_title: Mapped[str] = mapped_column(String)

    # Which department this employee belongs to.
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))

    # Who this employee reports to. It points to another row in this same table.
    # It is empty (None) for the person at the top.
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"))

    # "active" or "terminated". We never delete employees, we only terminate them.
    employment_status: Mapped[str] = mapped_column(String, default="active")

    start_date: Mapped[date]

    # The last day they worked here. Empty (None) while they still work here.
    termination_date: Mapped[Optional[date]]

    # When the row was created and last changed.
    # updated_at will later help us notice if a record changed before a user confirms an action.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SuperUser(Base):
    """A special account that can access everything. It is NOT linked to any employee."""

    __tablename__ = "super_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)


class User(Base):
    """A login account. One person = one user, linked to their employee record."""

    __tablename__ = "users"

    # The database only accepts these four roles.
    __table_args__ = (
        CheckConstraint("role IN ('employee', 'manager', 'hr', 'admin')", name="user_valid_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)

    # We NEVER store the real password, only a scrambled version of it (a "hash").
    password_hash: Mapped[str] = mapped_column(String)

    # Which employee this account belongs to.
    # It can be empty, for example for a system admin account.
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"), unique=True)

    # What this user is allowed to do: "employee", "manager", "hr" or "admin".
    # The rules for each role live in app/permissions.py.
    # default = used by Python for new rows. server_default = used by the database,
    # which also fills in "employee" for users that existed before this column was added.
    role: Mapped[str] = mapped_column(String, default="employee", server_default="employee")

    # When False, this user cannot log in and their tokens stop working.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LeaveRequest(Base):
    """An employee asks for time off. Their manager approves or rejects it."""

    __tablename__ = "leave_requests"

    # CheckConstraint = a rule the DATABASE itself enforces, even if our code has a bug.
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="leave_end_after_start"),
        CheckConstraint("approver_id <> employee_id", name="leave_no_self_approval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))

    # "annual", "sick", "unpaid" or "parental"
    leave_type: Mapped[str] = mapped_column(String)

    start_date: Mapped[date]
    end_date: Mapped[date]

    # Sensitive: the reason can be private (for example a health issue).
    reason: Mapped[Optional[str]] = mapped_column(Text)

    # "pending", "approved", "rejected" or "cancelled"
    status: Mapped[str] = mapped_column(String, default="pending")

    # The employee who approved or rejected it (usually their manager). Empty while pending.
    approver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Salary(Base):
    """One salary for one employee, from a certain date.

    We NEVER change an old salary row. A raise = a NEW row with a new effective_date.
    That way we keep the full salary history. The current salary is the newest row.
    """

    __tablename__ = "salaries"

    __table_args__ = (
        CheckConstraint("amount > 0", name="salary_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))

    # Yearly salary in US dollars. Numeric(12, 2) = exact money, up to 2 decimals.
    # (We never use float for money: float can turn 0.1 + 0.2 into 0.30000000000000004.)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    effective_date: Mapped[date]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PerformanceReview(Base):
    """A manager rates an employee's work for one period, for example "2025-H1"."""

    __tablename__ = "performance_reviews"

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="review_rating_1_to_5"),
        CheckConstraint("reviewer_id <> employee_id", name="review_no_self_review"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))

    # "H1" = January to June, "H2" = July to December. Example: "2025-H2"
    period: Mapped[str] = mapped_column(String)

    # 1 = poor, 3 = meets expectations, 5 = outstanding
    rating: Mapped[int]

    # Sensitive: written feedback about the person.
    comments: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
