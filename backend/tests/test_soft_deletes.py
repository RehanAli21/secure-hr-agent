# Tests for soft deletes and restore (departments and reviews).
# "Soft delete" = the row stays in the database, it only gets a deleted_at date and is hidden.

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Department
from tests.helpers import Org, create_department, log_in


# ---------- Departments ----------

def test_a_deleted_department_is_hidden_but_kept(client: TestClient, db: Session, org: Org) -> None:
    empty = create_department(db, "Old Team")
    hr = log_in(client, org.hr.email)

    response = client.delete(f"/departments/{empty.id}", headers=hr)

    assert response.status_code == 200
    names = [d["name"] for d in client.get("/departments", headers=hr).json()]
    assert "Old Team" not in names
    assert client.get(f"/departments/{empty.id}", headers=hr).status_code == 404
    # ...but the row is still in the database.
    db.expire_all()  # forget what this session remembers, read it fresh
    kept = db.get(Department, empty.id)
    assert kept is not None and kept.deleted_at is not None


def test_hr_can_see_deleted_departments_others_cannot(client: TestClient, db: Session, org: Org) -> None:
    empty = create_department(db, "Old Team")
    client.delete(f"/departments/{empty.id}", headers=log_in(client, org.hr.email))

    with_deleted = client.get("/departments?include_deleted=true", headers=log_in(client, org.hr.email))
    by_employee = client.get("/departments?include_deleted=true", headers=log_in(client, org.report.email))

    assert "Old Team" in [d["name"] for d in with_deleted.json()]
    assert by_employee.status_code == 403


def test_a_department_with_active_employees_cannot_be_deleted(client: TestClient, org: Org) -> None:
    # Everyone in the org fixture works in department 1.
    response = client.delete("/departments/1", headers=log_in(client, org.hr.email))

    assert response.status_code == 400


def test_only_hr_can_delete_a_department(client: TestClient, db: Session, org: Org) -> None:
    empty = create_department(db, "Old Team")

    response = client.delete(f"/departments/{empty.id}", headers=log_in(client, org.manager.email))

    assert response.status_code == 403


def test_a_deleted_department_can_be_restored(client: TestClient, db: Session, org: Org) -> None:
    empty = create_department(db, "Old Team")
    hr = log_in(client, org.hr.email)
    client.delete(f"/departments/{empty.id}", headers=hr)

    response = client.post(f"/departments/{empty.id}/restore", headers=hr)

    assert response.status_code == 200
    assert response.json()["deleted_at"] is None
    assert client.get(f"/departments/{empty.id}", headers=hr).status_code == 200


def test_nobody_can_be_hired_into_a_deleted_department(client: TestClient, db: Session, org: Org) -> None:
    empty = create_department(db, "Old Team")
    hr = log_in(client, org.hr.email)
    client.delete(f"/departments/{empty.id}", headers=hr)
    new_person = {"name": "Nina New", "email": "nina@test.local", "job_title": "x",
                  "department_id": empty.id, "start_date": "2026-01-01"}

    response = client.post("/employees", json=new_person, headers=hr)

    assert response.status_code == 400


# ---------- Reviews ----------

def write_review(client: TestClient, org: Org, rating: int = 4) -> int:
    """Mona reviews Ravi for 2025-H2. Returns the review id."""
    data = {"period": "2025-H2", "rating": rating}
    response = client.post(f"/employees/{org.report.id}/reviews", json=data, headers=log_in(client, org.manager.email))
    assert response.status_code == 201
    return response.json()["id"]


def test_hr_deletes_a_review_and_the_employee_no_longer_sees_it(client: TestClient, org: Org) -> None:
    review_id = write_review(client, org)

    response = client.delete(f"/employees/{org.report.id}/reviews/{review_id}", headers=log_in(client, org.hr.email))

    assert response.status_code == 200
    assert client.get(f"/employees/{org.report.id}/reviews", headers=log_in(client, org.report.email)).json() == []


def test_the_manager_cannot_delete_a_review(client: TestClient, org: Org) -> None:
    review_id = write_review(client, org)

    response = client.delete(f"/employees/{org.report.id}/reviews/{review_id}", headers=log_in(client, org.manager.email))

    assert response.status_code == 403


def test_after_a_delete_the_period_can_be_reviewed_again(client: TestClient, org: Org) -> None:
    old_review = write_review(client, org, rating=1)
    client.delete(f"/employees/{org.report.id}/reviews/{old_review}", headers=log_in(client, org.hr.email))

    write_review(client, org, rating=5)  # would fail with 409 if the deleted review still counted


def test_a_review_cannot_be_restored_if_its_period_was_reviewed_again(client: TestClient, org: Org) -> None:
    hr = log_in(client, org.hr.email)
    old_review = write_review(client, org, rating=1)
    client.delete(f"/employees/{org.report.id}/reviews/{old_review}", headers=hr)
    write_review(client, org, rating=5)

    response = client.post(f"/employees/{org.report.id}/reviews/{old_review}/restore", headers=hr)

    assert response.status_code == 409
