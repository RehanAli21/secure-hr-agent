# All API endpoints for salaries live here.
# The URLs hang under an employee, e.g. /employees/7/salaries, so it is always clear whose salary it is.
#
# Who may do what:
#   - see a salary history:   the employee themselves and hr/admin (managers can NOT)
#   - give a new salary:      only hr/admin, and never to themselves
#
# Important: we NEVER change or delete an old salary row.
# A raise is a NEW row with a new effective_date, so the full history is kept forever.
# There is no PATCH and no DELETE endpoint here on purpose.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Salary, SuperUser
from app.permissions import HR_ROLES, can_see_salary, require_role
from app.routers.employees import find_visible_employee  # reuse: "load an employee I may see, or 404"
from app.schemas import SalaryCreate, SalaryOut
from app.security import Account, get_current_user

router = APIRouter(
    prefix="/employees",
    tags=["Salaries"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/{employee_id}/salaries", response_model=list[SalaryOut])
def get_salary_history(
    employee_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """The full salary history of one employee. The newest one is first."""
    employee = find_visible_employee(db, account, employee_id)

    # A manager may see their report exists (above), but not what they earn.
    if not can_see_salary(account, employee):
        raise HTTPException(status_code=403, detail="You are not allowed to see this salary")

    return (
        db.query(Salary)
        .filter(Salary.employee_id == employee.id)
        .order_by(Salary.effective_date.desc())
        .all()
    )


@router.post("/{employee_id}/salaries", response_model=SalaryOut, status_code=201)
def add_salary(
    employee_id: int,
    data: SalaryCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """Give an employee a new salary (a raise, or a correction from a date). Adds a new row."""
    require_role(account, HR_ROLES)
    employee = find_visible_employee(db, account, employee_id)

    # Nobody sets their own salary, not even HR.
    if not isinstance(account, SuperUser) and account.employee_id == employee.id:
        raise HTTPException(status_code=403, detail="You cannot change your own salary")

    if employee.employment_status == "terminated":
        raise HTTPException(status_code=400, detail="Cannot change the salary of a terminated employee")

    if data.effective_date < employee.start_date:
        raise HTTPException(status_code=400, detail="The salary cannot start before the employee started")

    # The new salary must start AFTER the latest one. Changing the past would rewrite history.
    latest = (
        db.query(Salary)
        .filter(Salary.employee_id == employee.id)
        .order_by(Salary.effective_date.desc())
        .first()
    )
    if latest is not None and data.effective_date <= latest.effective_date:
        raise HTTPException(
            status_code=400,
            detail=f"The new salary must start after the latest one ({latest.effective_date})",
        )

    salary = Salary(employee_id=employee.id, amount=data.amount, effective_date=data.effective_date)
    db.add(salary)
    db.commit()
    db.refresh(salary)
    return salary
