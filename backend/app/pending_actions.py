# The logic behind pending actions (big changes the user must confirm). The endpoints are in
# app/routers/actions.py. For every action there are three small jobs:
#
#   check_args(...)  -> is the request valid? (uses the same schema as the real endpoint)
#   describe(...)    -> build the preview from the REAL data, and copy the record's current version
#   run(...)         -> do the change, by calling the real endpoint function
#
# Because run() calls the real endpoints, a confirmed action goes through exactly the same
# permission checks and rules as a normal request. There is no second, weaker way in.

from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.audit import Origin, snapshot
from app.models import Employee, PerformanceReview, Salary, User
from app.routers.departments import delete_department, find_department
from app.routers.employees import find_visible_employee, terminate_employee, update_employee
from app.routers.reviews import delete_review
from app.routers.roles import change_role
from app.routers.salaries import add_salary
from app.schemas import EmployeeUpdate, ManagerUpdate, NoArgs, RoleUpdate, SalaryCreate, TerminateRequest
from app.security import Account

# How long the user has to confirm, in minutes.
CONFIRM_MINUTES = 5

# Which schema checks the args of each action. These are the same schemas the real endpoints use.
ARGS_SCHEMAS: dict[str, type[BaseModel]] = {
    "terminate_employee": TerminateRequest,
    "change_salary": SalaryCreate,
    "change_role": RoleUpdate,
    "change_manager": ManagerUpdate,
    "delete_department": NoArgs,
    "delete_review": NoArgs,
}


def check_args(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Check the args with the action's schema. Returns them cleaned up, ready to store as JSON."""
    try:
        checked = ARGS_SCHEMAS[action].model_validate(args)
    except ValidationError as error:
        # The same 422 error FastAPI gives for a bad request body.
        raise RequestValidationError(error.errors())
    return checked.model_dump(mode="json")


def find_review(db: Session, review_id: int) -> PerformanceReview:
    review = db.get(PerformanceReview, review_id)
    if review is None or review.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


def find_user_of(db: Session, employee: Employee) -> User:
    user = db.query(User).filter(User.employee_id == employee.id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="This employee has no login account")
    return user


def latest_salary(db: Session, employee: Employee) -> "Salary | None":
    return (
        db.query(Salary)
        .filter(Salary.employee_id == employee.id)
        .order_by(Salary.effective_date.desc())
        .first()
    )


def describe(
    db: Session, account: Account, action: str, target_id: int, args: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (preview, version) for an action.

    preview = what the user sees before confirming, built from the real records.
    version = a copy of the records right now. At confirm time we compare it again (409 if changed).
    """
    if action == "terminate_employee":
        employee = find_visible_employee(db, account, target_id)
        summary = (f"Terminate {employee.name} ({employee.job_title}). "
                   f"Last working day: {args['termination_date']}. Their login will be disabled.")
        return {"summary": summary, "employee": employee.name}, {"employee": snapshot(employee)}

    if action == "change_salary":
        employee = find_visible_employee(db, account, target_id)
        current = latest_salary(db, employee)
        current_amount = str(current.amount) if current is not None else "none"
        summary = (f"Change the salary of {employee.name} from {current_amount} to {args['amount']}, "
                   f"starting {args['effective_date']}.")
        version = {"employee": snapshot(employee), "latest_salary": snapshot(current) if current else None}
        return {"summary": summary, "employee": employee.name, "current_salary": current_amount}, version

    if action == "change_role":
        employee = find_visible_employee(db, account, target_id)
        user = find_user_of(db, employee)
        summary = f"Change the role of {employee.name} from {user.role} to {args['role']}."
        return {"summary": summary, "employee": employee.name, "current_role": user.role}, {"user": snapshot(user)}

    if action == "change_manager":
        employee = find_visible_employee(db, account, target_id)
        if args["manager_id"] == employee.manager_id:
            raise HTTPException(status_code=400, detail="This is already their manager")
        old_manager = db.get(Employee, employee.manager_id) if employee.manager_id is not None else None
        new_manager = db.get(Employee, args["manager_id"]) if args["manager_id"] is not None else None
        if args["manager_id"] is not None and new_manager is None:
            raise HTTPException(status_code=400, detail="Manager does not exist")
        old_name = old_manager.name if old_manager is not None else "nobody"
        new_name = new_manager.name if new_manager is not None else "nobody"
        summary = f"Change the manager of {employee.name} from {old_name} to {new_name}."
        return {"summary": summary, "employee": employee.name}, {"employee": snapshot(employee)}

    if action == "delete_department":
        department = find_department(db, target_id)
        summary = f"Delete the department {department.name}. It can be restored later."
        return {"summary": summary, "department": department.name}, {"department": snapshot(department)}

    if action == "delete_review":
        review = find_review(db, target_id)
        employee = find_visible_employee(db, account, review.employee_id)
        summary = (f"Delete the {review.period} review of {employee.name} (rating {review.rating}/5). "
                   f"It can be restored later.")
        return {"summary": summary, "employee": employee.name}, {"review": snapshot(review)}

    raise HTTPException(status_code=400, detail="Unknown action")


def run(db: Session, account: Account, origin: Origin, action: str, target_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Do the change by calling the real endpoint function. Returns what that endpoint returns."""
    if action == "terminate_employee":
        result = terminate_employee(target_id, TerminateRequest.model_validate(args), db=db, account=account, origin=origin)
    elif action == "change_salary":
        result = add_salary(target_id, SalaryCreate.model_validate(args), db=db, account=account, origin=origin)
    elif action == "change_role":
        result = change_role(target_id, RoleUpdate.model_validate(args), db=db, account=account, origin=origin)
    elif action == "change_manager":
        result = update_employee(target_id, EmployeeUpdate.model_validate(args), db=db, account=account, origin=origin)
    elif action == "delete_department":
        result = delete_department(target_id, db=db, account=account, origin=origin)
    elif action == "delete_review":
        review = find_review(db, target_id)
        result = delete_review(review.employee_id, target_id, db=db, account=account, origin=origin)
    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    return jsonable_encoder(result, custom_encoder={Decimal: str})  # keep money exact, e.g. "99000.00"
