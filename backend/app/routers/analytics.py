# GET /analytics: numbers about many people at once (headcount, hires, salaries, leave, ratings).
#
# Who may ask:
#   analyst, hr, admin (and superusers):  the whole company
#   anyone with direct reports:           only their own team, and no salary numbers
#   everyone else:                        403
#
# The answer is never about one person:
#   - it holds only totals, averages and counts, never names or ids
#   - the SMALL-GROUP RULE, for the private numbers (salaries, leave, ratings):
#       part 1: nobody from a department with fewer than 5 active employees is ever counted
#               (see app/analytics.py)
#       part 2: a group in an answer that is about fewer than 5 people is left out
#               (for example "parental leave in Legal: 2 people")
#     Otherwise "average salary in a department of 1 person" would be that person's salary.
#
# The question is a choice from fixed lists (schemas.AnalyticsQuery), never SQL: the queries
# themselves are in app/analytics.py.

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics import METRICS, MIN_GROUP_SIZE, Filters
from app.database import get_db
from app.models import Department, Employee, SuperUser
from app.permissions import ANALYTICS_ROLES
from app.schemas import AnalyticsOut, AnalyticsQuery, AnalyticsRow
from app.security import Account, get_current_user

router = APIRouter(tags=["Analytics"], dependencies=[Depends(get_current_user)])

# The metrics about something private: these follow the small-group rule.
SENSITIVE_METRICS = {"average_salary", "payroll_total", "leave_days", "average_rating"}

# Salary numbers: never for a manager's team view (managers can't see their reports' salaries).
SALARY_METRICS = {"average_salary", "payroll_total"}

# The metrics about something that happened in a date range. The others are "as of today".
DATED_METRICS = {"new_hires", "leavers", "leave_days"}

# Which groupings make sense for each metric.
ALLOWED_GROUPS = {
    "headcount": ["department"],
    "new_hires": ["department", "month", "year"],
    "leavers": ["department", "month", "year"],
    "average_salary": ["department"],
    "payroll_total": ["department"],
    "leave_days": ["department", "leave_type", "month", "year"],
    "average_rating": ["department", "period"],
}

# A date range may be at most this long, so a "by month" answer has at most 60 rows.
MAX_RANGE = timedelta(days=5 * 366)


def analytics_scope(db: Session, account: Account) -> str:
    """ "company" or "team" (a manager's direct reports). Anyone else gets 403."""
    if isinstance(account, SuperUser) or account.role in ANALYTICS_ROLES:
        return "company"
    if account.employee_id is not None:
        # Anyone with active people reporting to them, whatever their role (checked live).
        reports = (db.query(Employee)
                   .filter(Employee.manager_id == account.employee_id, Employee.employment_status == "active")
                   .count())
        if reports > 0:
            return "team"
    raise HTTPException(status_code=403, detail="You are not allowed to see analytics")


def check_question(query: AnalyticsQuery) -> None:
    """Refuse a question that doesn't make sense, with a message that says why (422)."""
    allowed = ALLOWED_GROUPS[query.metric]
    if query.group_by is not None and query.group_by not in allowed:
        raise HTTPException(status_code=422,
                            detail=f"{query.metric} can be grouped by: {', '.join(allowed)} (or nothing)")
    if query.metric not in DATED_METRICS and (query.from_date is not None or query.to_date is not None):
        raise HTTPException(status_code=422,
                            detail=f"{query.metric} is about today, so it takes no dates. "
                                   f"Dates work with: {', '.join(sorted(DATED_METRICS))}")
    if query.leave_type is not None and query.metric != "leave_days":
        raise HTTPException(status_code=422, detail="leave_type only works with leave_days")


@router.get("/analytics", response_model=AnalyticsOut)
def get_analytics(
    query: Annotated[AnalyticsQuery, Query()],
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """One number, or one number per group, about many people at once."""
    scope = analytics_scope(db, account)
    check_question(query)
    private = query.metric in SENSITIVE_METRICS
    if scope == "team" and query.metric in SALARY_METRICS:
        raise HTTPException(status_code=403, detail="Salary numbers are only for analysts, HR and admins")

    # The date range: the last 12 months unless the question says otherwise.
    to_date = query.to_date or date.today()
    from_date = query.from_date or to_date - timedelta(days=365)
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="from_date must be before to_date")
    if to_date - from_date > MAX_RANGE:
        raise HTTPException(status_code=422, detail="The date range can be at most 5 years")

    if query.department is not None:
        found = (db.query(Department)
                 .filter(func.lower(Department.name) == query.department.lower(), Department.deleted_at.is_(None))
                 .first())
        if found is None:
            raise HTTPException(status_code=404, detail=f"There is no department called {query.department}")

    team_of = None
    if scope == "team" and not isinstance(account, SuperUser):  # (a superuser is never "team")
        team_of = account.employee_id
    filters = Filters(
        team_of=team_of,
        only_big_departments=private,
        department=query.department,
        leave_type=query.leave_type,
        from_date=from_date,
        to_date=to_date,
    )
    metric_function, description = METRICS[query.metric]
    rows = metric_function(db, query.group_by, filters)

    # The small-group rule, part 2: leave out groups that are about too few people.
    # (This also covers a manager's team of fewer than 5: every group in it is that small.)
    hidden = 0
    if private:
        hidden = sum(1 for row in rows if row[2] < MIN_GROUP_SIZE)
        rows = [row for row in rows if row[2] >= MIN_GROUP_SIZE]

    notes: list[str] = []
    if private and scope == "team":
        notes.append(f"Private numbers are only shown for groups of at least {MIN_GROUP_SIZE} people.")
    elif private:
        notes.append(f"Private numbers never include departments with fewer than {MIN_GROUP_SIZE} employees.")
    if hidden > 0:
        notes.append(f"{hidden} group(s) about fewer than {MIN_GROUP_SIZE} people are not shown.")
    if not rows:
        notes.append("No data to show for this question.")
    note = " ".join(notes) or None

    dated = query.metric in DATED_METRICS
    return AnalyticsOut(
        metric=query.metric,
        description=description,
        group_by=query.group_by,
        scope=scope,
        from_date=from_date if dated else None,
        to_date=to_date if dated else None,
        rows=[AnalyticsRow(group=group, value=round(value, 2), people=people) for group, value, people in rows],
        hidden_groups=hidden,
        note=note,
    )
