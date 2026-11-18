# Tests for GET /analytics (app/routers/analytics.py and app/analytics.py).
#
# The test company (on top of the shared org fixture):
#   Engineering (5 people): Hana (hr), Mona (manager), Ravi (Mona's report), Omar, Ana (analyst)
#   Sales (2 people):       Sam, Sue
# Current salaries: Engineering 100, 200, 300, 400, 500 (average 300); Sales 1000, 2000.

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Employee, LeaveRequest, PerformanceReview, Salary
from tests.helpers import Org, create_department, create_employee, create_login, create_superuser, log_in

TODAY = date.today()


@dataclass
class Company:
    org: Org
    analyst: Employee
    sam: Employee
    sue: Employee

    def engineering(self) -> list[Employee]:
        return [self.org.hr, self.org.manager, self.org.report, self.org.stranger, self.analyst]


@pytest.fixture
def company(db: Session, org: Org) -> Company:
    analyst = create_employee(db, "Ana Analyst")
    create_login(db, analyst, role="analyst")
    sales = create_department(db, "Sales")
    sam = create_employee(db, "Sam Sales", department=sales)
    sue = create_employee(db, "Sue Sales", department=sales)
    created = Company(org=org, analyst=analyst, sam=sam, sue=sue)

    for amount, person in zip([100, 200, 300, 400, 500], created.engineering()):
        db.add(Salary(employee_id=person.id, amount=Decimal(amount), effective_date=date(2025, 1, 1)))
    db.add(Salary(employee_id=sam.id, amount=Decimal(1000), effective_date=date(2025, 1, 1)))
    db.add(Salary(employee_id=sue.id, amount=Decimal(2000), effective_date=date(2025, 1, 1)))
    # Only the CURRENT salary counts: not an old one, and not a raise that hasn't started yet.
    db.add(Salary(employee_id=analyst.id, amount=Decimal(50), effective_date=date(2020, 1, 1)))
    db.add(Salary(employee_id=analyst.id, amount=Decimal(9999), effective_date=date(2099, 1, 1)))
    db.commit()
    return created


def ask(client: TestClient, email: str, **question: Any) -> Any:
    """GET /analytics as this user. Returns the response (check .status_code and .json())."""
    return client.get("/analytics", params=question, headers=log_in(client, email))


def rows(response: Any) -> list[tuple[str, float, int]]:
    assert response.status_code == 200, response.json()
    return [(row["group"], row["value"], row["people"]) for row in response.json()["rows"]]


# ---------- Who may ask ----------

def test_an_employee_without_reports_cannot_see_analytics(client: TestClient, company: Company) -> None:
    response = ask(client, company.org.stranger.email, metric="headcount")
    assert response.status_code == 403


def test_an_analyst_sees_the_whole_company(client: TestClient, company: Company) -> None:
    response = ask(client, company.analyst.email, metric="headcount", group_by="department")
    assert rows(response) == [("Engineering", 5, 5), ("Sales", 2, 2)]
    assert response.json()["scope"] == "company"


def test_an_analyst_still_cannot_see_anyone_s_records(client: TestClient, company: Company) -> None:
    # Numbers, yes. One person's profile or salary, no: an analyst is an employee for records.
    headers = log_in(client, company.analyst.email)
    assert client.get(f"/employees/{company.sam.id}", headers=headers).status_code == 404
    assert client.get(f"/employees/{company.sam.id}/salaries", headers=headers).status_code == 404
    assert [person["id"] for person in client.get("/employees", headers=headers).json()] == [company.analyst.id]


def test_a_manager_sees_only_their_team_and_no_salaries(client: TestClient, company: Company) -> None:
    response = ask(client, company.org.manager.email, metric="headcount")
    assert rows(response) == [("All", 1, 1)]  # only Ravi
    assert response.json()["scope"] == "team"
    assert ask(client, company.org.manager.email, metric="average_salary").status_code == 403


def test_hr_and_superusers_see_the_whole_company(client: TestClient, db: Session, company: Company) -> None:
    assert rows(ask(client, company.org.hr.email, metric="headcount")) == [("All", 7, 7)]
    root = create_superuser(db)
    response = client.get("/analytics", params={"metric": "headcount"}, headers=log_in(client, root.email, superuser=True))
    assert rows(response) == [("All", 7, 7)]


# ---------- Salaries and the small-group rule ----------

def test_small_departments_are_left_out_of_salary_numbers(client: TestClient, company: Company) -> None:
    # Sales has 2 people: its average would show them each other's salaries, so it is left out.
    response = ask(client, company.analyst.email, metric="average_salary", group_by="department")
    assert rows(response) == [("Engineering", 300.0, 5)]
    assert "fewer than 5 employees" in response.json()["note"]


def test_small_departments_cannot_be_worked_out_by_subtraction(client: TestClient, company: Company) -> None:
    # "Everyone's payroll" minus "every department shown" must NOT give Sales' payroll (3000):
    # Sales is not in the total either.
    everyone = rows(ask(client, company.analyst.email, metric="payroll_total"))[0][1]
    shown = rows(ask(client, company.analyst.email, metric="payroll_total", group_by="department"))
    assert everyone - sum(value for _, value, _ in shown) == 0


def test_a_filter_cannot_get_around_the_small_group_rule(client: TestClient, company: Company) -> None:
    response = ask(client, company.analyst.email, metric="payroll_total", department="Sales")
    assert rows(response) == []


def test_salary_numbers_use_each_person_s_current_salary(client: TestClient, company: Company) -> None:
    # Ana: 50 (2020, old), 500 (now), 9999 (2099, not started): only 500 counts.
    # Engineering: 100 + 200 + 300 + 400 + 500. (Sales is too small to count.)
    assert rows(ask(client, company.analyst.email, metric="payroll_total")) == [("All", 1500.0, 5)]


def test_a_manager_s_team_needs_5_people_for_private_numbers(client: TestClient, db: Session, company: Company) -> None:
    add_leave(db, company.org.report, "annual", 2)
    response = ask(client, company.org.manager.email, metric="leave_days")  # Mona has 1 report
    assert rows(response) == []
    assert "groups of at least 5 people" in response.json()["note"]


# ---------- People over time ----------

def test_new_hires_and_leavers_in_a_date_range(client: TestClient, db: Session, company: Company) -> None:
    company.sam.start_date = TODAY - timedelta(days=10)
    company.org.stranger.employment_status = "terminated"
    company.org.stranger.termination_date = TODAY - timedelta(days=3)
    db.commit()

    assert rows(ask(client, company.analyst.email, metric="new_hires", group_by="department")) == [("Sales", 1, 1)]
    assert rows(ask(client, company.analyst.email, metric="leavers")) == [("All", 1, 1)]
    # Everyone else started on 2024-01-15 (see tests/helpers.py).
    old = ask(client, company.analyst.email, metric="new_hires", from_date="2024-01-01", to_date="2024-01-31")
    assert rows(old) == [("All", 6, 6)]
    # Omar has left, so he is not in the headcount any more.
    assert rows(ask(client, company.analyst.email, metric="headcount", group_by="department")) == [
        ("Engineering", 4, 4), ("Sales", 1 + 1, 2)]


# ---------- Leave ----------

def add_leave(db: Session, person: Employee, leave_type: str, days: int, status: str = "approved") -> None:
    start = TODAY - timedelta(days=30)
    db.add(LeaveRequest(employee_id=person.id, leave_type=leave_type, start_date=start,
                        end_date=start + timedelta(days=days - 1), status=status))
    db.commit()


def test_leave_days_count_only_approved_leave(client: TestClient, db: Session, company: Company) -> None:
    for person in company.engineering():
        add_leave(db, person, "annual", 2)
    add_leave(db, company.org.hr, "annual", 9, status="pending")        # not approved: not counted
    add_leave(db, company.org.manager, "annual", 9, status="rejected")  # not approved: not counted
    add_leave(db, company.org.report, "sick", 3)  # 1 person: hidden from this answer (part 2)
    add_leave(db, company.sam, "annual", 4)       # Sales is too small: never counted (part 1)

    response = ask(client, company.analyst.email, metric="leave_days", group_by="leave_type")
    assert rows(response) == [("annual", 10.0, 5)]
    assert response.json()["hidden_groups"] == 1
    assert rows(ask(client, company.analyst.email, metric="leave_days", leave_type="annual")) == [("All", 10.0, 5)]


# ---------- Reviews ----------

def test_average_rating_leaves_out_deleted_reviews(client: TestClient, db: Session, company: Company) -> None:
    for person in company.engineering():
        reviewer = company.org.manager if person is not company.org.manager else company.org.hr
        db.add(PerformanceReview(employee_id=person.id, reviewer_id=reviewer.id, period="2025-H2", rating=4))
    # A deleted review (Ravi's second one for the period) must not count.
    db.add(PerformanceReview(employee_id=company.org.report.id, reviewer_id=company.org.manager.id,
                             period="2025-H2", rating=1, deleted_at=datetime.now(timezone.utc)))
    db.commit()

    assert rows(ask(client, company.analyst.email, metric="average_rating", group_by="period")) == [("2025-H2", 4.0, 5)]


# ---------- Questions that make no sense ----------

@pytest.mark.parametrize("question", [
    {"metric": "salaries"},                                         # not a metric
    {"metric": "headcount", "group_by": "name"},                    # not a grouping
    {"metric": "headcount", "group_by": "month"},                   # headcount is about today
    {"metric": "headcount", "from_date": "2025-01-01"},             # ... so it takes no dates
    {"metric": "headcount", "leave_type": "sick"},                  # leave_type only for leave_days
    {"metric": "headcount", "sql": "SELECT * FROM salaries"},       # unknown parameter
    {"metric": "new_hires", "from_date": "2026-01-01", "to_date": "2025-01-01"},  # backwards
    {"metric": "new_hires", "from_date": "2015-01-01", "to_date": "2026-01-01"},  # over 5 years
])
def test_questions_that_make_no_sense_get_422(client: TestClient, company: Company, question: dict[str, str]) -> None:
    assert ask(client, company.analyst.email, **question).status_code == 422


def test_an_unknown_department_gets_404(client: TestClient, company: Company) -> None:
    response = ask(client, company.analyst.email, metric="headcount", department="Marketing")
    assert response.status_code == 404


# ---------- The analyst role ----------

def test_the_analyst_role_is_described_and_can_be_given(client: TestClient, company: Company) -> None:
    analyst = log_in(client, company.analyst.email)
    mine = client.get("/me/permissions", headers=analyst).json()
    assert mine["role"] == "analyst"
    assert "view_analytics" in mine["permissions"]
    assert "analyst" in [role["name"] for role in client.get("/roles", headers=analyst).json()]

    hr = log_in(client, company.org.hr.email)
    response = client.patch(f"/employees/{company.org.stranger.id}/role", json={"role": "analyst"}, headers=hr)
    assert response.status_code == 200
    assert response.json()["role"] == "analyst"
