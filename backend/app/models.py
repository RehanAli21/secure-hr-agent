from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
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

    # Soft delete: instead of really deleting the row, we fill in WHEN it was deleted.
    # Empty (None) = not deleted. This way a deleted department can be restored.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


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

    # Contact details. The employee can change these themselves.
    phone: Mapped[Optional[str]] = mapped_column(String)
    # Sensitive: only the employee themselves and hr/admin may see the home address.
    home_address: Mapped[Optional[str]] = mapped_column(Text)

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
        # Only one review per employee for each period, e.g. one "2025-H1" review for Sara.
        # postgresql_where = the rule only counts reviews that are NOT deleted.
        # So after HR deletes a wrong review, the manager can write a new one for that period.
        Index("review_one_per_period", "employee_id", "period", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
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

    # Soft delete (see Department). Empty = not deleted.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    """One row for every change anybody makes. We only ever ADD rows here, never change them.

    It answers: who changed what, when, from where, and what did it look like before and after?
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who made the change. "user" or "superuser" + the id in that table.
    account_kind: Mapped[str] = mapped_column(String)
    account_id: Mapped[int]

    # What they did, e.g. "terminate_employee", and on which row, e.g. table "employees", id 7.
    action: Mapped[str] = mapped_column(String)
    table_name: Mapped[str] = mapped_column(String)
    record_id: Mapped[int]

    # A copy of the row before and after the change. "before" is empty for new rows.
    # JSON = the database stores a Python dict as text it can still search in.
    before: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    after: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # "ui" = the person clicked in the app. "agent" = the AI assistant made the request for them.
    source: Mapped[str] = mapped_column(String)

    # If the change ran because the user confirmed a pending action, which one.
    pending_action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pending_actions.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PendingAction(Base):
    """A big change that waits until the user confirms it in the app.

    Example: the AI agent wants to terminate someone. Instead of doing it, it creates a pending action.
    The app shows the user a preview, and ONLY when the user clicks "Confirm" does the change run.
    """

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who asked for it. Only this same account can confirm it.
    account_kind: Mapped[str] = mapped_column(String)
    account_id: Mapped[int]

    # What to do, e.g. "terminate_employee", on which record, with which exact arguments.
    action: Mapped[str] = mapped_column(String)
    target_id: Mapped[int]
    args: Mapped[dict[str, Any]] = mapped_column(JSON)

    # A copy of the record when the preview was made. If it is different at confirm time,
    # someone changed the record in the meantime, and we refuse (409).
    target_version: Mapped[dict[str, Any]] = mapped_column(JSON)

    # What we show the user, built by the API from the real data (never written by the AI).
    preview: Mapped[dict[str, Any]] = mapped_column(JSON)

    # "pending", "done", "cancelled" or "expired"
    status: Mapped[str] = mapped_column(String, default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    """A write request that came with an Idempotency-Key header, and the answer it got.

    If the same request comes again with the same key (a retry after a timeout, a double click),
    the API sends back this saved answer instead of making the change a second time.
    See app/idempotency.py.
    """

    __tablename__ = "idempotency_keys"
    # A key belongs to one account: two users may (by chance) pick the same key.
    __table_args__ = (Index("uq_idempotency_keys_account_key", "account_kind", "account_id", "key", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_kind: Mapped[str] = mapped_column(String)  # "user" or "superuser"
    account_id: Mapped[int]
    key: Mapped[str] = mapped_column(String(100))

    # A fingerprint of the request (method, path and body), so the same key can't be reused for
    # a DIFFERENT request.
    request_hash: Mapped[str] = mapped_column(String(64))

    # The saved answer. Both are empty while the first request is still running.
    status_code: Mapped[Optional[int]]
    response_body: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
