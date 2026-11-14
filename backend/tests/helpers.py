# Small helpers that put test data into the (empty) test database.
# A test calls them in its "arrange" step, for example:
#
#     employee = create_employee(db, "Sara Khan")
#     create_login(db, employee)
#     headers = log_in(client, employee.email)

from dataclasses import dataclass
from datetime import date
from typing import Optional

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Department, Employee, SuperUser, User
from app.security import hash_password

PASSWORD = "Password123!"

# Hashing a password is slow on purpose. We hash it ONCE here and reuse it, so the tests stay fast.
PASSWORD_HASH = hash_password(PASSWORD)


def create_department(db: Session, name: str = "Engineering") -> Department:
    department = Department(name=name)
    db.add(department)
    db.commit()
    return department


def create_employee(
    db: Session,
    name: str,
    manager: Optional[Employee] = None,
    department: Optional[Department] = None,
) -> Employee:
    """Create an employee. The email is made from the name: "Sara Khan" -> sara.khan@test.local"""
    if department is None:
        # Use the first department, or create one if there is none yet.
        department = db.query(Department).first() or create_department(db)

    employee = Employee(
        name=name,
        email=name.lower().replace(" ", ".") + "@test.local",
        job_title="Software Engineer",
        department_id=department.id,
        manager_id=manager.id if manager is not None else None,
        start_date=date(2024, 1, 15),
        employment_status="active",
    )
    db.add(employee)
    db.commit()
    return employee


def create_login(db: Session, employee: Employee, role: str = "employee", is_active: bool = True) -> User:
    """Give an employee a login account, with the password PASSWORD."""
    user = User(
        email=employee.email,
        password_hash=PASSWORD_HASH,
        employee_id=employee.id,
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def create_superuser(db: Session, email: str = "root@test.local") -> SuperUser:
    superuser = SuperUser(email=email, password_hash=PASSWORD_HASH)
    db.add(superuser)
    db.commit()
    return superuser


def log_in(client: TestClient, email: str, superuser: bool = False) -> dict[str, str]:
    """Log in and return the header to send with requests: {"Authorization": "Bearer <token>"}"""
    path = "/auth/superuser/login" if superuser else "/auth/login"
    response = client.post(path, json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.json()
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def as_agent(headers: dict[str, str]) -> dict[str, str]:
    """The same login, but the request says it comes from the AI agent (like our agent service will)."""
    return {**headers, "X-Request-Source": "agent"}


# ---------- A small company that many tests use ----------
#
#     Hana (hr)        Mona (manager)        Omar (employee, the "stranger")
#                            |
#                      Ravi (employee, reports to Mona)

@dataclass
class Org:
    hr: Employee
    manager: Employee
    report: Employee
    stranger: Employee


def create_org(db: Session) -> Org:
    hr = create_employee(db, "Hana Hr")
    manager = create_employee(db, "Mona Manager")
    report = create_employee(db, "Ravi Report", manager=manager)
    stranger = create_employee(db, "Omar Other")

    create_login(db, hr, role="hr")
    create_login(db, manager, role="manager")
    create_login(db, report)
    create_login(db, stranger)

    # Contact details, so we can test who may see the (private) home address.
    report.phone = "+1 555 111 2222"
    report.home_address = "1 Secret Street, Lakeside"
    db.commit()

    return Org(hr=hr, manager=manager, report=report, stranger=stranger)
