# All API endpoints for departments live here.
#
# Who may do what:
#   - see departments:                     every logged-in user (department names are not secret)
#   - create, rename, delete or restore:   only hr and admin
#
# "Delete" is a SOFT delete: we only fill in deleted_at, so the department can be restored later.
# Every change is written to the audit log (see app/audit.py).

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import Origin, get_origin, record_audit, snapshot
from app.database import get_db
from app.models import Department, Employee, utc_now
from app.permissions import HR_ROLES, require_confirmation, require_role
from app.schemas import DepartmentCreate, DepartmentOut, DepartmentUpdate
from app.security import Account, get_current_user

# Every endpoint in this file starts with /departments.
# dependencies=[...] means: every endpoint here needs a valid login token first.
router = APIRouter(
    prefix="/departments",
    tags=["Departments"],
    dependencies=[Depends(get_current_user)],
)


def find_department(db: Session, department_id: int) -> Department:
    """Load one department that is not deleted, or stop with a 404 error."""
    department = db.get(Department, department_id)
    if department is None or department.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Department not found")
    return department


def check_name_is_free(db: Session, name: str):
    """Stop with a 409 error if another department (even a deleted one) already has this name."""
    existing = db.query(Department).filter(Department.name == name).first()
    if existing is None:
        return
    if existing.deleted_at is not None:
        raise HTTPException(status_code=409, detail="A deleted department has this name. Restore it instead.")
    raise HTTPException(status_code=409, detail="A department with this name already exists")


@router.post("", response_model=DepartmentOut, status_code=201)
def create_department(
    data: DepartmentCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    require_role(account, HR_ROLES)
    check_name_is_free(db, data.name)

    department = Department(name=data.name)
    db.add(department)
    db.flush()  # gives the new department its id
    record_audit(db, account, origin, "create_department", department, before=None)
    db.commit()
    db.refresh(department)
    return department


@router.get("", response_model=list[DepartmentOut])
def list_departments(
    include_deleted: bool = False,  # hr/admin can ask to see deleted departments too
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    query = db.query(Department)

    if include_deleted:
        require_role(account, HR_ROLES)
    else:
        query = query.filter(Department.deleted_at.is_(None))

    return query.order_by(Department.name).all()


@router.get("/{department_id}", response_model=DepartmentOut)
def get_department(department_id: int, db: Session = Depends(get_db)):
    return find_department(db, department_id)


@router.patch("/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: int,
    data: DepartmentUpdate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    require_role(account, HR_ROLES)
    department = find_department(db, department_id)
    before = snapshot(department)

    if data.name is not None and data.name != department.name:
        check_name_is_free(db, data.name)
        department.name = data.name

    db.flush()
    record_audit(db, account, origin, "update_department", department, before)
    db.commit()
    db.refresh(department)
    return department


@router.delete("/{department_id}", response_model=DepartmentOut)
def delete_department(
    department_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Soft delete: hide the department, but keep the row so it can be restored."""
    require_role(account, HR_ROLES)
    require_confirmation(origin)
    department = find_department(db, department_id)

    # Don't leave active employees in a department that no longer exists.
    active_employees = (
        db.query(Employee)
        .filter(Employee.department_id == department.id, Employee.employment_status == "active")
        .count()
    )
    if active_employees > 0:
        raise HTTPException(
            status_code=400,
            detail=f"This department still has {active_employees} active employees. Move them first.",
        )

    before = snapshot(department)
    department.deleted_at = utc_now()

    db.flush()
    record_audit(db, account, origin, "delete_department", department, before)
    db.commit()
    db.refresh(department)
    return department


@router.post("/{department_id}/restore", response_model=DepartmentOut)
def restore_department(
    department_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Undo a delete."""
    require_role(account, HR_ROLES)

    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    if department.deleted_at is None:
        raise HTTPException(status_code=400, detail="This department is not deleted")

    before = snapshot(department)
    department.deleted_at = None

    db.flush()
    record_audit(db, account, origin, "restore_department", department, before)
    db.commit()
    db.refresh(department)
    return department
