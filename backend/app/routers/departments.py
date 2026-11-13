# All API endpoints for departments live here.
#
# Who may do what:
#   - see departments:            every logged-in user (department names are not secret)
#   - create or rename one:       only hr and admin

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Department
from app.schemas import DepartmentCreate, DepartmentUpdate, DepartmentOut
from app.permissions import HR_ROLES, require_role
from app.security import Account, get_current_user

# Every endpoint in this file starts with /departments.
# dependencies=[...] means: every endpoint here needs a valid login token first.
router = APIRouter(
    prefix="/departments",
    tags=["Departments"],
    dependencies=[Depends(get_current_user)],
)


def find_department(db: Session, department_id: int) -> Department:
    """Load one department, or stop with a 404 error if it does not exist."""
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return department


def check_name_is_free(db: Session, name: str):
    """Stop with a 409 error if another department already has this name."""
    existing = db.query(Department).filter(Department.name == name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A department with this name already exists")


@router.post("", response_model=DepartmentOut, status_code=201)
def create_department(
    data: DepartmentCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    require_role(account, HR_ROLES)
    check_name_is_free(db, data.name)

    department = Department(name=data.name)
    db.add(department)
    db.commit()
    db.refresh(department)  # reload it so we get the new id from the database
    return department


@router.get("", response_model=list[DepartmentOut])
def list_departments(db: Session = Depends(get_db)):
    return db.query(Department).order_by(Department.name).all()


@router.get("/{department_id}", response_model=DepartmentOut)
def get_department(department_id: int, db: Session = Depends(get_db)):
    return find_department(db, department_id)


@router.patch("/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: int,
    data: DepartmentUpdate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    require_role(account, HR_ROLES)
    department = find_department(db, department_id)

    if data.name is not None and data.name != department.name:
        check_name_is_free(db, data.name)
        department.name = data.name

    db.commit()
    db.refresh(department)
    return department
