# All API endpoints for leave requests (time off) live here.
#
# Who may do what:
#   - submit leave:          anyone linked to an employee, and only for THEMSELVES
#   - see leave:             the same people who may see that employee (see app/permissions.py)
#   - see the reason:        only the employee themselves and hr/admin
#   - cancel leave:          only the employee who asked for it
#   - approve / reject:      only the employee's direct manager (so nobody approves their own leave)
#
# Superusers may do everything.
# Every change is written to the audit log (see app/audit.py).

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.audit import Origin, get_origin, record_audit, snapshot
from app.database import get_db
from app.models import Employee, LeaveRequest, SuperUser, utc_now
from app.permissions import can_see_employee, can_see_leave_reason, is_direct_manager, only_visible_employees
from app.schemas import LeaveCreate, LeaveOut, LeaveStatus
from app.security import Account, get_current_user

router = APIRouter(
    prefix="/leave",
    tags=["Leave"],
    dependencies=[Depends(get_current_user)],
)


# ---------- Helpers ----------

def find_visible_leave(db: Session, account: Account, leave_id: int) -> tuple[LeaveRequest, Employee]:
    """Load one leave request (and its employee) that this account may see. Otherwise 404."""
    leave = db.get(LeaveRequest, leave_id)
    if leave is None:
        raise HTTPException(status_code=404, detail="Leave request not found")

    employee = db.get(Employee, leave.employee_id)
    if employee is None or not can_see_employee(account, employee):
        # 404, not 403: don't tell people that this leave request exists.
        raise HTTPException(status_code=404, detail="Leave request not found")

    return leave, employee


def to_leave_out(account: Account, leave: LeaveRequest, employee: Employee) -> LeaveOut:
    """Turn a leave request into API output, hiding the reason from people who may not read it."""
    out = LeaveOut.model_validate(leave)
    if not can_see_leave_reason(account, employee):
        out.reason = None
    return out


def check_no_overlap(db: Session, employee_id: int, start_date: date, end_date: date):
    """Stop with 409 if the employee already has pending or approved leave on any of these days."""
    # Two date ranges overlap when each one starts before the other one ends.
    overlapping = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status.in_(["pending", "approved"]),
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
        .first()
    )
    if overlapping is not None:
        raise HTTPException(status_code=409, detail="You already have leave on some of these days")


def decide_leave(db: Session, account: Account, origin: Origin, leave_id: int, new_status: str) -> LeaveOut:
    """Approve or reject a leave request. Shared by the /approve and /reject endpoints."""
    leave, employee = find_visible_leave(db, account, leave_id)

    # Only the direct manager decides. Your own manager is never you,
    # so this also means nobody can approve their own leave.
    if not isinstance(account, SuperUser) and not is_direct_manager(account, employee):
        raise HTTPException(status_code=403, detail="Only the employee's manager can decide on this leave")

    if leave.status != "pending":
        raise HTTPException(status_code=400, detail=f"This leave request is already {leave.status}")

    before = snapshot(leave)
    leave.status = new_status
    # Superusers are not employees, so for them the approver stays empty.
    leave.approver_id = None if isinstance(account, SuperUser) else account.employee_id
    leave.decided_at = utc_now()

    db.flush()
    # The action name is "approve_leave" or "reject_leave".
    record_audit(db, account, origin, "approve_leave" if new_status == "approved" else "reject_leave", leave, before)
    db.commit()
    db.refresh(leave)
    return to_leave_out(account, leave, employee)


# ---------- Endpoints ----------

@router.post("", response_model=LeaveOut, status_code=201)
def submit_leave(
    data: LeaveCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Ask for time off for yourself."""
    if isinstance(account, SuperUser) or account.employee_id is None:
        raise HTTPException(status_code=400, detail="Only accounts linked to an employee can ask for leave")

    if data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="The end date cannot be before the start date")

    check_no_overlap(db, account.employee_id, data.start_date, data.end_date)

    leave = LeaveRequest(
        employee_id=account.employee_id,  # always yourself, taken from the token
        leave_type=data.leave_type,
        start_date=data.start_date,
        end_date=data.end_date,
        reason=data.reason,
        status="pending",
    )
    db.add(leave)
    db.flush()
    record_audit(db, account, origin, "submit_leave", leave, before=None)
    db.commit()
    db.refresh(leave)

    employee = db.get(Employee, leave.employee_id)
    assert employee is not None  # it must exist: the foreign key guarantees it
    return to_leave_out(account, leave, employee)


@router.get("", response_model=list[LeaveOut])
def list_leave(
    employee_id: Optional[int] = None,     # only one employee's leave
    status: Optional[LeaveStatus] = None,  # e.g. only "pending"
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    # The ids of the employees this account may see. We reuse the SAME rule as the employees endpoints.
    visible_employee_ids = only_visible_employees(db.query(Employee), account).with_entities(Employee.id)

    # Each row = (leave request, its employee). We need the employee to decide if the reason is hidden.
    query = db.query(LeaveRequest, Employee).join(Employee, LeaveRequest.employee_id == Employee.id)
    query = query.filter(LeaveRequest.employee_id.in_(visible_employee_ids))

    if employee_id is not None:
        query = query.filter(LeaveRequest.employee_id == employee_id)
    if status is not None:
        query = query.filter(LeaveRequest.status == status)

    rows = query.order_by(LeaveRequest.start_date.desc()).limit(limit).all()
    return [to_leave_out(account, leave, employee) for leave, employee in rows]


@router.get("/{leave_id}", response_model=LeaveOut)
def get_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    leave, employee = find_visible_leave(db, account, leave_id)
    return to_leave_out(account, leave, employee)


@router.post("/{leave_id}/cancel", response_model=LeaveOut)
def cancel_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Cancel your own leave: while it is pending, or when it is approved but has not started yet."""
    leave, employee = find_visible_leave(db, account, leave_id)

    if not isinstance(account, SuperUser) and leave.employee_id != account.employee_id:
        raise HTTPException(status_code=403, detail="You can only cancel your own leave")

    can_cancel = leave.status == "pending" or (leave.status == "approved" and leave.start_date > date.today())
    if not can_cancel:
        raise HTTPException(status_code=400, detail="This leave can no longer be cancelled")

    before = snapshot(leave)
    leave.status = "cancelled"
    db.flush()
    record_audit(db, account, origin, "cancel_leave", leave, before)
    db.commit()
    db.refresh(leave)
    return to_leave_out(account, leave, employee)


@router.post("/{leave_id}/approve", response_model=LeaveOut)
def approve_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    return decide_leave(db, account, origin, leave_id, "approved")


@router.post("/{leave_id}/reject", response_model=LeaveOut)
def reject_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    return decide_leave(db, account, origin, leave_id, "rejected")
