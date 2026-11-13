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
