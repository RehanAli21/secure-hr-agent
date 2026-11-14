# Our first tests. They check that the test setup itself works.
#
# Every test follows the same 3 steps:
#   1. Arrange: get things ready     (here: the fixtures do it for us)
#   2. Act:     do the thing         (here: send a request)
#   3. Assert:  check the result     ("assert" = this MUST be true, or the test fails)

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Employee


def test_health_says_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_every_test_starts_with_an_empty_database(db: Session) -> None:
    assert db.query(Employee).count() == 0


def test_endpoints_need_a_login(client: TestClient) -> None:
    response = client.get("/employees")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not logged in"}
