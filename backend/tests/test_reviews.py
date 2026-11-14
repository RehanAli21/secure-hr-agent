# Tests for performance reviews and their permission rules (app/routers/reviews.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import Org, create_superuser, log_in


def write_review(client: TestClient, headers: dict[str, str], employee_id: int, period: str = "2025-H2", rating: int = 4) -> Any:
    data = {"period": period, "rating": rating, "comments": "Great teamwork this half year."}
    return client.post(f"/employees/{employee_id}/reviews", json=data, headers=headers)


# ---------- Writing a review ----------

def test_the_manager_reviews_their_report(client: TestClient, org: Org) -> None:
    response = write_review(client, log_in(client, org.manager.email), org.report.id)

    assert response.status_code == 201
    assert response.json()["reviewer_id"] == org.manager.id  # taken from the token


def test_nobody_can_write_a_review_in_someone_elses_name(client: TestClient, org: Org) -> None:
    data = {"period": "2025-H2", "rating": 5, "reviewer_id": org.hr.id}

    response = client.post(f"/employees/{org.report.id}/reviews", json=data, headers=log_in(client, org.manager.email))

    assert response.status_code == 422


def test_nobody_reviews_themselves(client: TestClient, org: Org) -> None:
    for person in (org.report, org.manager):
        response = write_review(client, log_in(client, person.email), person.id)
        assert response.status_code == 403


def test_hr_cannot_write_reviews_because_hr_is_not_the_manager(client: TestClient, org: Org) -> None:
    response = write_review(client, log_in(client, org.hr.email), org.report.id)

    assert response.status_code == 403


def test_a_superuser_cannot_write_reviews(client: TestClient, db: Session, org: Org) -> None:
    # A review needs a reviewer who is an employee. Superusers are not employees.
    superuser = create_superuser(db)

    response = write_review(client, log_in(client, superuser.email, superuser=True), org.report.id)

    assert response.status_code == 400


def test_only_one_review_per_period(client: TestClient, org: Org) -> None:
    manager = log_in(client, org.manager.email)
    write_review(client, manager, org.report.id, period="2025-H2")

    response = write_review(client, manager, org.report.id, period="2025-H2")

    assert response.status_code == 409


def test_a_future_period_cannot_be_reviewed(client: TestClient, org: Org) -> None:
    response = write_review(client, log_in(client, org.manager.email), org.report.id, period="2099-H1")

    assert response.status_code == 400


@pytest.mark.parametrize("period, rating", [("2025-H3", 4), ("25-H1", 4), ("2025-H1", 0), ("2025-H1", 6)])
def test_bad_periods_and_ratings_are_refused(client: TestClient, org: Org, period: str, rating: int) -> None:
    response = write_review(client, log_in(client, org.manager.email), org.report.id, period=period, rating=rating)

    assert response.status_code == 422


# ---------- Who can read reviews ----------

def test_the_employee_and_their_manager_can_read_the_reviews(client: TestClient, org: Org) -> None:
    write_review(client, log_in(client, org.manager.email), org.report.id)

    for person in (org.report, org.manager, org.hr):
        response = client.get(f"/employees/{org.report.id}/reviews", headers=log_in(client, person.email))
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_a_stranger_cannot_read_the_reviews(client: TestClient, org: Org) -> None:
    write_review(client, log_in(client, org.manager.email), org.report.id)

    response = client.get(f"/employees/{org.report.id}/reviews", headers=log_in(client, org.stranger.email))

    assert response.status_code == 404


def test_reviews_can_never_be_edited(client: TestClient, org: Org) -> None:
    review_id = write_review(client, log_in(client, org.manager.email), org.report.id).json()["id"]

    response = client.patch(
        f"/employees/{org.report.id}/reviews/{review_id}",
        json={"rating": 5},
        headers=log_in(client, org.hr.email),
    )

    assert response.status_code == 405
