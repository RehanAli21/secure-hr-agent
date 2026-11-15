# Who is allowed to do what. Every permission rule lives in this one file.
#
# The four roles:
#   employee -> can see only their own profile
#   manager  -> can see their own profile + their direct reports
#   hr       -> can see and change all employees and departments
#   admin    -> can see and change everything
#
# Superusers skip every check: they are allowed to do everything.
#
# Important: the role and the employee id come from the logged-in account (loaded using the token),
# never from the request. So nobody can just say "I am HR" to get more access.

from fastapi import HTTPException
from sqlalchemy import false, or_
from sqlalchemy.orm import Query

from app.audit import Origin
from app.models import Employee, SuperUser
from app.security import Account

ROLES = ("employee", "manager", "hr", "admin")

# The roles that manage HR data for the whole company.
HR_ROLES = ("hr", "admin")


def require_role(account: Account, allowed_roles: tuple[str, ...]) -> None:
    """Stop with 403 (Forbidden) if this account's role is not in allowed_roles."""
    if isinstance(account, SuperUser):
        return  # superusers may do everything

    if account.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="You are not allowed to do this")


def can_see_employee(account: Account, employee: Employee) -> bool:
    """True if this account is allowed to see this employee's profile."""
    if isinstance(account, SuperUser):
        return True

    if account.role in HR_ROLES:
        return True

    # Everyone can see themselves.
    if account.employee_id is not None and employee.id == account.employee_id:
        return True

    # A manager can see the people who report directly to them.
    # We check manager_id on the live record every time, so a change in the org chart works right away.
    if account.role == "manager" and account.employee_id is not None and employee.manager_id == account.employee_id:
        return True

    return False


def only_visible_employees(query: "Query[Employee]", account: Account) -> "Query[Employee]":
    """Add a filter to a list query, so it only returns employees this account may see.

    This is the same rule as can_see_employee, but written as a database filter.
    """
    if isinstance(account, SuperUser) or account.role in HR_ROLES:
        return query  # no filter: they can see everyone

    if account.employee_id is None:
        return query.filter(false())  # not linked to an employee: they see nobody

    if account.role == "manager":
        return query.filter(or_(
            Employee.id == account.employee_id,           # themselves
            Employee.manager_id == account.employee_id,   # their direct reports
        ))

    return query.filter(Employee.id == account.employee_id)  # only themselves


# ---------- Contact details ----------

def can_see_home_address(account: Account, employee: Employee) -> bool:
    """The home address is private: only the employee themselves and hr/admin may see it."""
    if isinstance(account, SuperUser) or account.role in HR_ROLES:
        return True
    return account.employee_id == employee.id


def can_change_contact_details(account: Account, employee: Employee) -> bool:
    """You may change your own phone and address. hr/admin may change anyone's."""
    if isinstance(account, SuperUser) or account.role in HR_ROLES:
        return True
    return account.employee_id == employee.id


# ---------- Big changes need confirmation ----------

def require_confirmation(origin: Origin) -> None:
    """Big changes (terminate, salary, role, manager, deletes) asked for by the AI agent
    must go through a pending action that the user confirms in the app (POST /actions).

    The agent service is not allowed to make these changes directly.
    """
    if origin.source == "agent" and origin.pending_action_id is None:
        raise HTTPException(
            status_code=403,
            detail="This change needs the user's confirmation. Create a pending action with POST /actions.",
        )


# ---------- Leave requests ----------
#
# The rule for SEEING leave is simple: you can see someone's leave if you can see that employee.
# So an employee sees their own leave, a manager also sees their reports' leave, and hr/admin see all.

def is_direct_manager(account: Account, employee: Employee) -> bool:
    """True if this account is the employee's direct manager (checked live from manager_id)."""
    if isinstance(account, SuperUser) or account.employee_id is None:
        return False
    return employee.manager_id == account.employee_id


def can_see_leave_reason(account: Account, employee: Employee) -> bool:
    """The reason can be private (for example a health issue).

    Only the employee themselves and hr/admin may read it. Managers can approve leave without it.
    """
    if isinstance(account, SuperUser) or account.role in HR_ROLES:
        return True
    return account.employee_id == employee.id


# ---------- Salaries ----------

def can_see_salary(account: Account, employee: Employee) -> bool:
    """Salaries are private: only the employee themselves and hr/admin may see them.

    Notice: managers can NOT see their reports' salaries.
    """
    if isinstance(account, SuperUser) or account.role in HR_ROLES:
        return True
    return account.employee_id == employee.id


# ---------- What each role may do (shown to the user and the AI agent) ----------
#
# These lists DESCRIBE the rules above, so the app and the AI agent know what to offer the user.
# They do not decide anything: every endpoint still checks the real rules itself.

ROLE_DESCRIPTIONS = {
    "employee": "Sees their own profile, leave, reviews and salary. Changes their own contact details. "
                "Submits and cancels their own leave.",
    "manager": "Everything an employee can do. Also sees their direct reports' profiles, leave and reviews "
               "(not their salaries), approves or rejects their leave and writes their reviews.",
    "hr": "Sees all employees, including salaries. Creates and changes employees, departments, salaries "
          "and roles. Terminates employees. Deletes and restores records. Reads the audit log.",
    "admin": "Everything HR can do. Only admins can give someone the admin role or take it away.",
}

EMPLOYEE_PERMISSIONS = [
    "view_own_profile", "update_own_contact_details", "view_own_salary", "view_own_reviews",
    "view_own_leave", "submit_leave", "cancel_own_leave",
]

# Anyone with direct reports gets these, whatever their role (the check is "are you their manager?").
MANAGER_PERMISSIONS = [
    "view_reports_profiles", "view_reports_leave", "decide_reports_leave",
    "view_reports_reviews", "write_reports_reviews",
]

HR_PERMISSIONS = [
    "view_all_employees", "create_employee", "update_employee", "terminate_employee",
    "view_all_salaries", "change_salary", "change_role", "manage_departments",
    "delete_records", "restore_records", "view_audit_log",
]

ROLE_PERMISSIONS = {
    "employee": EMPLOYEE_PERMISSIONS,
    "manager": EMPLOYEE_PERMISSIONS + MANAGER_PERMISSIONS,
    "hr": EMPLOYEE_PERMISSIONS + HR_PERMISSIONS,
    "admin": EMPLOYEE_PERMISSIONS + HR_PERMISSIONS + ["grant_admin_role"],
}

# Every permission in plain words, so people (and the AI agent) understand them.
PERMISSION_DESCRIPTIONS = {
    # employee
    "view_own_profile": "See your own employee profile",
    "update_own_contact_details": "Change your own phone number and home address",
    "view_own_salary": "See your own salary and its history",
    "view_own_reviews": "See your own performance reviews",
    "view_own_leave": "See your own leave requests",
    "submit_leave": "Ask for leave (annual, sick, unpaid or parental)",
    "cancel_own_leave": "Cancel your own leave: while it is pending, or approved but not started yet",
    # manager (anyone with direct reports)
    "view_reports_profiles": "See the profiles of the people who report to you",
    "view_reports_leave": "See the leave requests of the people who report to you",
    "decide_reports_leave": "Approve or reject the leave requests of the people who report to you",
    "view_reports_reviews": "See the performance reviews of the people who report to you",
    "write_reports_reviews": "Write performance reviews for the people who report to you",
    # hr
    "view_all_employees": "See every employee",
    "create_employee": "Add new employees",
    "update_employee": "Change employee details such as job title, department and manager",
    "terminate_employee": "Terminate employees (this also disables their login)",
    "view_all_salaries": "See everyone's salaries",
    "change_salary": "Set a new salary for an employee (but not your own)",
    "change_role": "Change someone's role (but not your own)",
    "manage_departments": "Create, rename and delete departments",
    "delete_records": "Delete departments and performance reviews (they can be restored)",
    "restore_records": "Restore deleted departments and performance reviews",
    "view_audit_log": "Read the audit log of every change",
    # admin
    "grant_admin_role": "Give someone the admin role, or take it away",
    # superuser
    "everything": "Do everything: a superuser skips all permission checks",
}


def describe_permissions(permissions: list[str]) -> dict[str, str]:
    """{"submit_leave": "Ask for leave (...)", ...} for the given permission names."""
    return {permission: PERMISSION_DESCRIPTIONS[permission] for permission in permissions}
