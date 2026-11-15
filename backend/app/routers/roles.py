# Everything about roles: what each role may do, what YOU may do, and changing someone's role.
#
# Who may do what:
#   - GET /roles and GET /me/permissions:   every logged-in user
#   - change a role:                         hr/admin, never your own role,
#                                            and only admins may give or take away the admin role
#
# The AI agent calls GET /me/permissions before every answer, so it only offers what you may do.
# But the lists only DESCRIBE the rules: every endpoint still checks the real rules itself.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import Origin, get_origin, record_audit, snapshot
from app.database import get_db
from app.models import Employee, SuperUser, User
from app.permissions import (
    HR_ROLES,
    MANAGER_PERMISSIONS,
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    ROLES,
    describe_permissions,
    require_confirmation,
    require_role,
)
from app.routers.employees import find_visible_employee  # reuse: "load an employee I may see, or 404"
from app.schemas import PermissionsOut, RoleInfo, RoleOut, RoleUpdate
from app.security import Account, get_current_user

router = APIRouter(tags=["Roles"], dependencies=[Depends(get_current_user)])


@router.get("/roles", response_model=list[RoleInfo])
def list_roles():
    """All roles, with what each one may do."""
    return [
        RoleInfo(
            name=role,
            description=ROLE_DESCRIPTIONS[role],
            permissions=ROLE_PERMISSIONS[role],
            descriptions=describe_permissions(ROLE_PERMISSIONS[role]),
        )
        for role in ROLES
    ]


@router.get("/me/permissions", response_model=PermissionsOut)
def my_permissions(
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """What the logged-in account may do. Worked out fresh on every request, never cached."""
    if isinstance(account, SuperUser):
        return PermissionsOut(kind="superuser", role=None, is_manager=False, permissions=["everything"],
                              descriptions=describe_permissions(["everything"]))

    permissions = list(ROLE_PERMISSIONS[account.role])

    # Anyone with people reporting to them can approve their leave and write their reviews,
    # whatever their role. We look at the org chart right now, so changes count immediately.
    is_manager = False
    if account.employee_id is not None:
        reports = (
            db.query(Employee)
            .filter(Employee.manager_id == account.employee_id, Employee.employment_status == "active")
            .count()
        )
        is_manager = reports > 0

    if is_manager:
        for permission in MANAGER_PERMISSIONS:
            if permission not in permissions:
                permissions.append(permission)

    return PermissionsOut(kind="user", role=account.role, is_manager=is_manager, permissions=permissions,
                          descriptions=describe_permissions(permissions))


@router.patch("/employees/{employee_id}/role", response_model=RoleOut)
def change_role(
    employee_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """Change the role of an employee's login account."""
    require_role(account, HR_ROLES)
    require_confirmation(origin)  # role changes asked for by the AI agent must be confirmed
    employee = find_visible_employee(db, account, employee_id)

    user = db.query(User).filter(User.employee_id == employee.id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="This employee has no login account")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="This login account is disabled")

    # Nobody changes their own role. Otherwise HR could make themselves admin.
    if not isinstance(account, SuperUser) and user.id == account.id:
        raise HTTPException(status_code=403, detail="You cannot change your own role")

    # Only admins may give the admin role, or take it away from someone.
    is_admin = isinstance(account, SuperUser) or account.role == "admin"
    if (data.role == "admin" or user.role == "admin") and not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can give or take away the admin role")

    before = snapshot(user)
    user.role = data.role

    db.flush()
    record_audit(db, account, origin, "change_role", user, before)
    db.commit()
    return RoleOut(employee_id=employee.id, user_id=user.id, role=user.role)
