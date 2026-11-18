# The analytics queries: one small function per metric.
#
# This is a "semantic layer": the caller picks a metric and a grouping from fixed lists
# (schemas.AnalyticsQuery), and THIS file decides how each one is worked out, with SQL we wrote
# ourselves. Nobody (not the user, not the AI) ever writes SQL, and nobody can ask for a column
# or table that isn't listed here.
#
# Every function returns rows of (group, value, people):
#   group   "Engineering", "2026-03", "sick"... ("All" when there is no grouping)
#   value   the number itself
#   people  how many different people the number is about (used by the small-group rule)
#
# Who may ask what is in app/routers/analytics.py.
#
# THE SMALL-GROUP RULE, part 1 (here): private numbers (salaries, leave, ratings) never include
# anyone from a department with fewer than MIN_GROUP_SIZE active employees: not in a total, not
# with a filter, not in a group. Only hiding such a department from one answer would not be enough:
# "everyone's payroll" minus "every other department's payroll" would give it back. People who are
# never counted can't be worked out by any subtraction. (Part 2 is in the router.)

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import Integer, Select, cast, func, literal, select
from sqlalchemy.orm import Session, aliased

from app.models import Department, Employee, LeaveRequest, PerformanceReview, Salary

MIN_GROUP_SIZE = 5


@dataclass
class Filters:
    """Everything that narrows the question down (already checked by the router)."""
    team_of: Optional[int]       # a manager's employee id: only their direct reports. None = everyone
    only_big_departments: bool   # the small-group rule, part 1: True for every private number
    department: Optional[str]    # a department name
    leave_type: Optional[str]
    from_date: date
    to_date: date


def group_expression(group_by: Optional[str], date_column: Any) -> Any:
    """The SQL that puts each row in its group (a column, or a small SQL expression)."""
    if group_by == "department":
        return Department.name
    if group_by == "month":
        return func.to_char(date_column, "YYYY-MM")   # 2026-03
    if group_by == "year":
        return func.to_char(date_column, "YYYY")      # 2026
    if group_by == "leave_type":
        return LeaveRequest.leave_type
    if group_by == "period":
        return PerformanceReview.period               # 2025-H2
    return literal("All")                             # no grouping: one row for everyone


def narrow(query: Select[Any], filters: Filters) -> Select[Any]:
    """The filters every metric shares: whose numbers (scope) and which department."""
    if filters.team_of is not None:
        query = query.where(Employee.manager_id == filters.team_of)
    if filters.department is not None:
        query = query.where(func.lower(Department.name) == filters.department.lower())
    if filters.only_big_departments:
        # The departments with at least MIN_GROUP_SIZE active employees. ("aliased" = a second,
        # separate copy of the employees table, so this list doesn't get mixed up with the
        # employees of the main question.)
        everyone = aliased(Employee)
        big_departments = (select(everyone.department_id)
                           .where(everyone.employment_status == "active")
                           .group_by(everyone.department_id)
                           .having(func.count(everyone.id) >= MIN_GROUP_SIZE))
        query = query.where(Employee.department_id.in_(big_departments))
    return query


def run(db: Session, query: Select[Any], group: Any, group_by: Optional[str]) -> list[tuple[str, float, int]]:
    """Group (unless there is no grouping), run, and turn the result into (group, value, people) rows."""
    if group_by is not None:
        query = query.group_by(group).order_by(group)
    rows = db.execute(query).all()
    # Without a grouping there is always one row, even when nothing matched (a count of 0, an
    # average of nothing): leave such a row out, like an empty group.
    return [(str(row[0]), float(row[1]), int(row[2])) for row in rows if row[2] > 0 and row[1] is not None]


# ---------- People ----------

def headcount(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    group = group_expression(group_by, None)
    query = (select(group, func.count(Employee.id), func.count(Employee.id))
             .join(Department, Employee.department_id == Department.id)
             .where(Employee.employment_status == "active"))
    return run(db, narrow(query, filters), group, group_by)


def new_hires(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    group = group_expression(group_by, Employee.start_date)
    query = (select(group, func.count(Employee.id), func.count(Employee.id))
             .join(Department, Employee.department_id == Department.id)
             .where(Employee.start_date.between(filters.from_date, filters.to_date)))
    return run(db, narrow(query, filters), group, group_by)


def leavers(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    group = group_expression(group_by, Employee.termination_date)
    query = (select(group, func.count(Employee.id), func.count(Employee.id))
             .join(Department, Employee.department_id == Department.id)
             .where(Employee.termination_date.between(filters.from_date, filters.to_date)))
    return run(db, narrow(query, filters), group, group_by)


# ---------- Salaries ----------

def current_salaries() -> Any:
    """Each employee's CURRENT salary: their newest one that has already started (a raise
    starting next month doesn't count yet). DISTINCT ON keeps the first row per employee."""
    return (select(Salary.employee_id, Salary.amount)
            .where(Salary.effective_date <= date.today())
            .distinct(Salary.employee_id)
            .order_by(Salary.employee_id, Salary.effective_date.desc(), Salary.id.desc())
            .subquery())


def average_salary(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    salary = current_salaries()
    group = group_expression(group_by, None)
    query = (select(group, func.avg(salary.c.amount), func.count(Employee.id))
             .join(salary, salary.c.employee_id == Employee.id)
             .join(Department, Employee.department_id == Department.id)
             .where(Employee.employment_status == "active"))
    return run(db, narrow(query, filters), group, group_by)


def payroll_total(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    salary = current_salaries()
    group = group_expression(group_by, None)
    query = (select(group, func.sum(salary.c.amount), func.count(Employee.id))
             .join(salary, salary.c.employee_id == Employee.id)
             .join(Department, Employee.department_id == Department.id)
             .where(Employee.employment_status == "active"))
    return run(db, narrow(query, filters), group, group_by)


# ---------- Leave ----------

def leave_days(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    """Calendar days of APPROVED leave, counted in the month/year the leave started."""
    group = group_expression(group_by, LeaveRequest.start_date)
    days = cast(LeaveRequest.end_date - LeaveRequest.start_date, Integer) + 1   # 5 to 9 Jan = 5 days
    query = (select(group, func.sum(days), func.count(func.distinct(LeaveRequest.employee_id)))
             .select_from(LeaveRequest)
             .join(Employee, LeaveRequest.employee_id == Employee.id)
             .join(Department, Employee.department_id == Department.id)
             .where(LeaveRequest.status == "approved",
                    LeaveRequest.start_date.between(filters.from_date, filters.to_date)))
    if filters.leave_type is not None:
        query = query.where(LeaveRequest.leave_type == filters.leave_type)
    return run(db, narrow(query, filters), group, group_by)


# ---------- Reviews ----------

def average_rating(db: Session, group_by: Optional[str], filters: Filters) -> list[tuple[str, float, int]]:
    """The average rating (1 to 5) of the reviews that are not deleted."""
    group = group_expression(group_by, None)
    query = (select(group, func.avg(PerformanceReview.rating), func.count(func.distinct(PerformanceReview.employee_id)))
             .select_from(PerformanceReview)
             .join(Employee, PerformanceReview.employee_id == Employee.id)
             .join(Department, Employee.department_id == Department.id)
             .where(PerformanceReview.deleted_at.is_(None)))
    return run(db, narrow(query, filters), group, group_by)


# Every metric: its function and what it means, in plain words (shown to the user and the AI).
METRICS = {
    "headcount": (headcount, "Number of active employees today"),
    "new_hires": (new_hires, "Number of people who started in the date range"),
    "leavers": (leavers, "Number of people whose employment ended in the date range"),
    "average_salary": (average_salary, "Average current salary of active employees (no currency)"),
    "payroll_total": (payroll_total, "All current salaries of active employees added up (no currency)"),
    "leave_days": (leave_days, "Calendar days of approved leave that started in the date range"),
    "average_rating": (average_rating, "Average performance review rating, from 1 to 5"),
}
