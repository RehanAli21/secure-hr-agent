# All API endpoints for performance reviews live here.
# Like salaries, the URLs hang under an employee, e.g. /employees/7/reviews.
#
# Who may do what:
#   - read reviews:     the same people who may see that employee
#                       (the employee themselves, their manager, hr/admin)
#   - write a review:   only the employee's direct manager (so nobody reviews themselves)
#
# A review is never changed or deleted after it is written, so there is no PATCH or DELETE.

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PerformanceReview, SuperUser
from app.permissions import is_direct_manager
from app.routers.employees import find_visible_employee  # reuse: "load an employee I may see, or 404"
from app.schemas import ReviewCreate, ReviewOut
from app.security import Account, get_current_user

router = APIRouter(
    prefix="/employees",
    tags=["Reviews"],
    dependencies=[Depends(get_current_user)],
)


def period_start(period: str) -> date:
    """The first day of a period: "2025-H1" -> 2025-01-01, "2025-H2" -> 2025-07-01."""
    year = int(period[:4])
    month = 1 if period.endswith("H1") else 7
    return date(year, month, 1)


@router.get("/{employee_id}/reviews", response_model=list[ReviewOut])
def get_reviews(
    employee_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """All reviews of one employee. The newest period is first."""
    employee = find_visible_employee(db, account, employee_id)

    return (
        db.query(PerformanceReview)
        .filter(PerformanceReview.employee_id == employee.id)
        .order_by(PerformanceReview.period.desc())
        .all()
    )


@router.post("/{employee_id}/reviews", response_model=ReviewOut, status_code=201)
def write_review(
    employee_id: int,
    data: ReviewCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """Write a review for one of your direct reports."""
    employee = find_visible_employee(db, account, employee_id)

    # A review needs a reviewer who is an employee. Superusers are not employees.
    if isinstance(account, SuperUser) or account.employee_id is None:
        raise HTTPException(status_code=400, detail="Only accounts linked to an employee can write reviews")

    # Only the direct manager writes the review. Your manager is never you,
    # so this also means nobody can review themselves.
    if not is_direct_manager(account, employee):
        raise HTTPException(status_code=403, detail="Only the employee's manager can write their review")

    if employee.employment_status == "terminated":
        raise HTTPException(status_code=400, detail="Cannot review a terminated employee")

    # You can't review a period that has not started yet.
    if period_start(data.period) > date.today():
        raise HTTPException(status_code=400, detail="This period has not started yet")

    # One review per period. (The database has the same rule, in case two requests come at once.)
    existing = (
        db.query(PerformanceReview)
        .filter(PerformanceReview.employee_id == employee.id, PerformanceReview.period == data.period)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"This employee already has a review for {data.period}")

    review = PerformanceReview(
        employee_id=employee.id,
        reviewer_id=account.employee_id,  # always you, taken from the token
        period=data.period,
        rating=data.rating,
        comments=data.comments,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review
