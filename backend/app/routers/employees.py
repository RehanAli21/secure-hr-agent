# All API endpoints for employees live here.
# Notice there is NO delete endpoint: in HR we never delete people, we terminate them.
#
# Who may do what (the rules themselves live in app/permissions.py):
#   - see employees:                 everyone, but only the employees their role allows
#   - create, change or terminate:   only hr and admin

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Department, Employee, User
from app.schemas import EmployeeCreate, EmployeeUpdate, EmployeeOut, TerminateRequest
from app.permissions import HR_ROLES, can_see_employee, only_visible_employees, require_role
from app.security import Account, get_current_user

# Every endpoint in this file starts with /employees.
# dependencies=[...] means: every endpoint here needs a valid login token first.
# Endpoints that need to know WHO is calling also ask for `account: Account = Depends(get_current_user)`.
# (FastAPI is smart: it runs get_current_user only once per request, even though we ask twice.)
router = APIRouter(
    prefix="/employees",
    tags=["Employees"],
    dependencies=[Depends(get_current_user)],
)


# ---------- Helper checks ----------

def find_employee(db: Session, employee_id: int) -> Employee:
    """Load one employee, or stop with a 404 error if they do not exist."""
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def find_visible_employee(db: Session, account: Account, employee_id: int) -> Employee:
    """Load one employee that this account is allowed to see.

    If they may NOT see this employee, we answer 404 "not found" instead of 403 "forbidden".
    That way nobody can find out which employee ids exist by trying them one by one.
    """
    employee = find_employee(db, employee_id)
    if not can_see_employee(account, employee):
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def check_department_exists(db: Session, department_id: int):
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=400, detail="Department does not exist")


def check_manager_exists(db: Session, manager_id: int):
    if db.get(Employee, manager_id) is None:
        raise HTTPException(status_code=400, detail="Manager does not exist")


def check_email_is_free(db: Session, email: str):
    existing = db.query(Employee).filter(Employee.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An employee with this email already exists")


# ---------- Endpoints ----------

@router.post("", response_model=EmployeeOut, status_code=201)
def create_employee(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    require_role(account, HR_ROLES)

    check_email_is_free(db, data.email)
    check_department_exists(db, data.department_id)
    if data.manager_id is not None:
        check_manager_exists(db, data.manager_id)

    employee = Employee(
        name=data.name,
        email=data.email,
        job_title=data.job_title,
        department_id=data.department_id,
        manager_id=data.manager_id,
        start_date=data.start_date,
        employment_status="active",
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@router.get("", response_model=list[EmployeeOut])
def list_employees(
    name: Optional[str] = None,           # search by part of a name, e.g. "ali"
    department_id: Optional[int] = None,  # only show one department
    limit: int = Query(default=20, ge=1, le=100),  # never return more than 100 rows
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    # Start with only the employees this account may see, then apply the search filters.
    query = only_visible_employees(db.query(Employee), account)

    if name is not None:
        # ilike = "contains this text", ignoring upper/lower case
        query = query.filter(Employee.name.ilike(f"%{name}%"))
    if department_id is not None:
        query = query.filter(Employee.department_id == department_id)

    return query.order_by(Employee.name).limit(limit).all()


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    return find_visible_employee(db, account, employee_id)


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    require_role(account, HR_ROLES)
    employee = find_employee(db, employee_id)

    if employee.employment_status == "terminated":
        raise HTTPException(status_code=400, detail="Cannot change a terminated employee")

    # Only the fields the client actually sent.
    changes = data.model_dump(exclude_unset=True)

    if "email" in changes and changes["email"] != employee.email:
        check_email_is_free(db, changes["email"])
    if "department_id" in changes:
        check_department_exists(db, changes["department_id"])
    if changes.get("manager_id") is not None:
        if changes["manager_id"] == employee.id:
            raise HTTPException(status_code=400, detail="An employee cannot be their own manager")
        check_manager_exists(db, changes["manager_id"])

    # Copy each change onto the employee, e.g. employee.job_title = "Senior Developer"
    for field, value in changes.items():
        setattr(employee, field, value)

    db.commit()
    db.refresh(employee)
    return employee


@router.post("/{employee_id}/terminate", response_model=EmployeeOut)
def terminate_employee(
    employee_id: int,
    data: TerminateRequest,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    require_role(account, HR_ROLES)
    employee = find_employee(db, employee_id)

    if employee.employment_status == "terminated":
        raise HTTPException(status_code=400, detail="Employee is already terminated")

    if data.termination_date < employee.start_date:
        raise HTTPException(status_code=400, detail="Termination date cannot be before the start date")

    # We keep the record forever. We only change the status and save the last day.
    employee.employment_status = "terminated"
    employee.termination_date = data.termination_date

    # Also disable their login account (if they have one), so they can't log in anymore.
    user = db.query(User).filter(User.employee_id == employee.id).first()
    if user is not None:
        user.is_active = False

    db.commit()
    db.refresh(employee)
    return employee
