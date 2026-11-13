# All API endpoints for performance reviews live here.
# Like salaries, the URLs hang under an employee, e.g. /employees/7/reviews.
#
# Who may do what:
#   - read reviews:       the same people who may see that employee
#                         (the employee themselves, their manager, hr/admin)
#   - write a review:     only the employee's direct manager (so nobody reviews themselves)
#   - delete / restore:   only hr/admin (for example to remove a review written by mistake)
#
# A review is never edited, so there is no PATCH. "Delete" is a SOFT delete (deleted_at is filled in).
# Every change is written to the audit log (see app/audit.py).

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import Origin, get_origin, record_audit, snapshot
from app.database import get_db
from app.models import Employee, PerformanceReview, SuperUser, utc_now
from app.permissions import HR_ROLES, is_direct_manager, require_confirmation, require_role
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


def find_review(db: Session, employee: Employee, review_id: int) -> PerformanceReview:
    """Load one review of this employee (deleted or not), or stop with a 404 error."""
    review = db.get(PerformanceReview, review_id)
    if review is None or review.employee_id != employee.id:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


def check_period_is_free(db: Session, employee: Employee, period: str):
    """Stop with 409 if this employee already has a (not deleted) review for this period."""
    existing = (
        db.query(PerformanceReview)
        .filter(
            PerformanceReview.employee_id == employee.id,
            PerformanceReview.period == period,
            PerformanceReview.deleted_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"This employee already has a review for {period}")


@router.get("/{employee_id}/reviews", response_model=list[ReviewOut])
def get_reviews(
    employee_id: int,
    include_deleted: bool = False,  # hr/admin can ask to see deleted reviews too
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """All reviews of one employee. The newest period is first."""
    employee = find_visible_employee(db, account, employee_id)

    query = db.query(PerformanceReview).filter(PerformanceReview.employee_id == employee.id)
    if include_deleted:
        require_role(account, HR_ROLES)
    else:
        query = query.filter(PerformanceReview.deleted_at.is_(None))

    return query.order_by(PerformanceReview.period.desc()).all()


@router.post("/{employee_id}/reviews", response_model=ReviewOut, status_code=201)
def write_review(
    employee_id: int,
    data: ReviewCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
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
    check_period_is_free(db, employee, data.period)

    review = PerformanceReview(
        employee_id=employee.id,
        reviewer_id=account.employee_id,  # always you, taken from the token
        period=data.period,
        rating=data.rating,
        comments=data.comments,
    )
    db.add(review)
    db.flush()
    record_audit(db, account, origin, "write_review", review, before=None)
    db.commit()
    db.refresh(review)
    return review


@router.delete("/{employee_id}/reviews/{review_id}", response_model=ReviewOut)
def delete_review(
    employee_id: int,
    review_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Soft delete: hide the review, but keep the row so it can be restored."""
    require_role(account, HR_ROLES)
    require_confirmation(origin)
    employee = find_visible_employee(db, account, employee_id)
    review = find_review(db, employee, review_id)

    if review.deleted_at is not None:
        raise HTTPException(status_code=400, detail="This review is already deleted")

    before = snapshot(review)
    review.deleted_at = utc_now()

    db.flush()
    record_audit(db, account, origin, "delete_review", review, before)
    db.commit()
    db.refresh(review)
    return review


@router.post("/{employee_id}/reviews/{review_id}/restore", response_model=ReviewOut)
def restore_review(
    employee_id: int,
    review_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Undo a delete. Not possible if a new review for the same period was written meanwhile."""
    require_role(account, HR_ROLES)
    employee = find_visible_employee(db, account, employee_id)
    review = find_review(db, employee, review_id)

    if review.deleted_at is None:
        raise HTTPException(status_code=400, detail="This review is not deleted")

    check_period_is_free(db, employee, review.period)

    before = snapshot(review)
    review.deleted_at = None

    db.flush()
    record_audit(db, account, origin, "restore_review", review, before)
    db.commit()
    db.refresh(review)
    return review
