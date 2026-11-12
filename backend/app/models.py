from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey
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

    # When the row was created and last changed.
    # updated_at will later help us notice if a record changed before a user confirms an action.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
