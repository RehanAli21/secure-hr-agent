# Tests for idempotency keys (app/idempotency.py): a write sent twice with the same
# Idempotency-Key header only happens once.

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AuditLog, IdempotencyKey, LeaveRequest, User, utc_now
from app.routers import leave as leave_router
from tests.helpers import Org, log_in

LEAVE = {"leave_type": "annual", "start_date": "2099-03-01", "end_date": "2099-03-05"}


def with_key(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def ravis_user_id(db: Session, org: Org) -> int:
    user = db.query(User).filter(User.employee_id == org.report.id).one()
    return user.id


def test_the_same_request_twice_happens_once(client: TestClient, db: Session, org: Org) -> None:
    ravi = with_key(log_in(client, org.report.email), "key-1")

    first = client.post("/leave", json=LEAVE, headers=ravi)
    second = client.post("/leave", json=LEAVE, headers=ravi)

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()  # the very same answer, with the same id
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert db.query(LeaveRequest).count() == 1
    assert db.query(AuditLog).count() == 1  # and only one change in the audit log


def test_a_refusal_is_replayed_too(client: TestClient, db: Session, org: Org) -> None:
    # The first answer was "no" (end before start): the retry gets the same "no", nothing runs.
    ravi = with_key(log_in(client, org.report.email), "key-1")
    backwards = {**LEAVE, "end_date": "2099-02-01"}

    first = client.post("/leave", json=backwards, headers=ravi)
    second = client.post("/leave", json=backwards, headers=ravi)

    assert first.status_code == second.status_code
    assert first.status_code >= 400
    assert second.headers["Idempotent-Replayed"] == "true"


def test_a_key_cannot_be_reused_for_another_request(client: TestClient, db: Session, org: Org) -> None:
    ravi = with_key(log_in(client, org.report.email), "key-1")
    client.post("/leave", json=LEAVE, headers=ravi)

    response = client.post("/leave", json={**LEAVE, "leave_type": "sick"}, headers=ravi)

    assert response.status_code == 422
    assert response.json()["detail"] == "This Idempotency-Key was already used for a different request"
    assert db.query(LeaveRequest).count() == 1


def test_a_key_belongs_to_one_account(client: TestClient, db: Session, org: Org) -> None:
    # Ravi and Omar happen to pick the same key: both requests run.
    client.post("/leave", json=LEAVE, headers=with_key(log_in(client, org.report.email), "same"))
    response = client.post("/leave", json=LEAVE, headers=with_key(log_in(client, org.stranger.email), "same"))

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    assert db.query(LeaveRequest).count() == 2


def test_without_a_key_nothing_changes(client: TestClient, db: Session, org: Org) -> None:
    ravi = log_in(client, org.report.email)

    client.post("/leave", json=LEAVE, headers=ravi)
    response = client.post("/leave", json=LEAVE, headers=ravi)

    assert response.status_code == 409  # the normal rule: leave can't overlap
    assert db.query(IdempotencyKey).count() == 0


def test_the_same_request_while_it_is_still_running_gives_409(client: TestClient, db: Session, org: Org) -> None:
    ravi = with_key(log_in(client, org.report.email), "key-1")
    client.post("/leave", json=LEAVE, headers=ravi)
    row = db.query(IdempotencyKey).one()
    row.status_code = None  # pretend it hasn't finished yet
    row.response_body = None
    db.commit()

    response = client.post("/leave", json=LEAVE, headers=ravi)

    assert response.status_code == 409
    assert db.query(LeaveRequest).count() == 1


def test_a_key_is_forgotten_after_24_hours(client: TestClient, db: Session, org: Org) -> None:
    ravi = with_key(log_in(client, org.report.email), "key-1")
    client.post("/leave", json=LEAVE, headers=ravi)
    row = db.query(IdempotencyKey).one()
    row.created_at = utc_now() - timedelta(hours=25)
    db.commit()

    response = client.post("/leave", json=LEAVE, headers=ravi)

    # Not replayed: it really ran again (and the normal overlap rule said no).
    assert response.status_code == 409
    assert "Idempotent-Replayed" not in response.headers


def test_after_a_server_error_the_key_can_be_used_again(
    client: TestClient, db: Session, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the database fell over")

    ravi = with_key(log_in(client, org.report.email), "key-1")
    monkeypatch.setattr(leave_router, "record_audit", broken)
    with pytest.raises(RuntimeError):
        client.post("/leave", json=LEAVE, headers=ravi)
    assert db.query(IdempotencyKey).count() == 0  # not saved

    monkeypatch.undo()
    response = client.post("/leave", json=LEAVE, headers=ravi)  # the retry runs for real

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers


def test_reads_are_never_saved(client: TestClient, db: Session, org: Org) -> None:
    client.get("/leave", headers=with_key(log_in(client, org.report.email), "key-1"))

    assert db.query(IdempotencyKey).count() == 0


def test_a_huge_key_is_refused(client: TestClient, org: Org) -> None:
    response = client.post("/leave", json=LEAVE, headers=with_key(log_in(client, org.report.email), "x" * 101))

    assert response.status_code == 400
