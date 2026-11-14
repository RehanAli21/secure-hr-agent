# Tests for logging in, tokens and "who am I?" (app/routers/auth.py and app/security.py).

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.security import create_token
from tests.helpers import PASSWORD, create_employee, create_login, create_superuser, log_in


# ---------- Logging in ----------

def test_login_with_the_right_password_gives_two_tokens(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)

    response = client.post("/auth/login", json={"email": "sara.khan@test.local", "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] != ""
    assert body["refresh_token"] != ""
    assert body["token_type"] == "bearer"


def test_login_with_a_wrong_password_fails(client: TestClient, db: Session) -> None:
    create_login(db, create_employee(db, "Sara Khan"))

    response = client.post("/auth/login", json={"email": "sara.khan@test.local", "password": "wrong-password"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong email or password"}


def test_unknown_email_gets_the_same_error_as_a_wrong_password(client: TestClient) -> None:
    # The same message on purpose: nobody can use the login form to find out which emails exist.
    response = client.post("/auth/login", json={"email": "nobody@test.local", "password": PASSWORD})

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong email or password"}


def test_a_disabled_account_cannot_log_in(client: TestClient, db: Session) -> None:
    create_login(db, create_employee(db, "Sara Khan"), is_active=False)

    response = client.post("/auth/login", json={"email": "sara.khan@test.local", "password": PASSWORD})

    assert response.status_code == 401
    assert response.json() == {"detail": "This account is disabled"}


def test_login_rejects_unknown_fields(client: TestClient, db: Session) -> None:
    create_login(db, create_employee(db, "Sara Khan"))

    response = client.post(
        "/auth/login",
        json={"email": "sara.khan@test.local", "password": PASSWORD, "role": "admin"},
    )

    assert response.status_code == 422


# ---------- Who am I? ----------

def test_me_shows_who_is_logged_in(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    user = create_login(db, employee, role="manager")
    headers = log_in(client, employee.email)

    response = client.get("/me", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "email": "sara.khan@test.local",
        "kind": "user",
        "employee_id": employee.id,
        "role": "manager",
    }


def test_me_never_shows_the_password_hash(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)

    response = client.get("/me", headers=log_in(client, employee.email))

    assert "password_hash" not in response.text


# ---------- Bad tokens ----------

def test_a_made_up_token_is_rejected(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": "Bearer this-is-not-a-real-token"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}


def test_a_changed_token_is_rejected(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)
    headers = log_in(client, employee.email)

    # Change one letter in the signature (the last part of the token). It no longer matches.
    # We change a letter 10 from the end, not the very last one: the last letter has some unused
    # bits, so changing it does not always change the signature.
    token = headers["Authorization"]
    position = len(token) - 10
    new_letter = "A" if token[position] != "A" else "B"
    changed_token = token[:position] + new_letter + token[position + 1:]
    response = client.get("/me", headers={"Authorization": changed_token})

    assert response.status_code == 401


def test_an_expired_token_is_rejected(client: TestClient, db: Session) -> None:
    user = create_login(db, create_employee(db, "Sara Khan"))
    # A token that expired one second ago.
    token = create_token(user.id, "user", "access", timedelta(seconds=-1))

    response = client.get("/me", headers={"Authorization": "Bearer " + token})

    assert response.status_code == 401
    assert response.json() == {"detail": "Token has expired"}


def test_a_user_disabled_after_login_is_blocked_right_away(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    user = create_login(db, employee)
    headers = log_in(client, employee.email)

    # The token is still valid for 15 minutes, but we disable the account now.
    user.is_active = False
    db.commit()

    response = client.get("/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "User is not active"}


# ---------- Refresh tokens ----------

def test_a_refresh_token_gives_a_new_access_token(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)
    tokens = client.post("/auth/login", json={"email": employee.email, "password": PASSWORD}).json()

    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    new_access_token = response.json()["access_token"]
    assert client.get("/me", headers={"Authorization": "Bearer " + new_access_token}).status_code == 200


def test_a_refresh_token_cannot_be_used_as_an_access_token(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)
    tokens = client.post("/auth/login", json={"email": employee.email, "password": PASSWORD}).json()

    response = client.get("/me", headers={"Authorization": "Bearer " + tokens["refresh_token"]})

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong type of token"}


def test_an_access_token_cannot_be_used_to_refresh(client: TestClient, db: Session) -> None:
    employee = create_employee(db, "Sara Khan")
    create_login(db, employee)
    tokens = client.post("/auth/login", json={"email": employee.email, "password": PASSWORD}).json()

    response = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong type of token"}


# ---------- Superusers ----------

def test_a_superuser_logs_in_on_their_own_endpoint(client: TestClient, db: Session) -> None:
    superuser = create_superuser(db)

    response = client.get("/me", headers=log_in(client, superuser.email, superuser=True))

    assert response.status_code == 200
    assert response.json()["kind"] == "superuser"
    assert response.json()["role"] is None


def test_a_superuser_cannot_use_the_normal_login(client: TestClient, db: Session) -> None:
    create_superuser(db)

    response = client.post("/auth/login", json={"email": "root@test.local", "password": PASSWORD})

    assert response.status_code == 401


def test_user_1_and_superuser_1_are_different_people(client: TestClient, db: Session) -> None:
    # Both tables start counting at 1, so user #1 and superuser #1 exist at the same time.
    # The "kind" inside the token makes sure user #1 does NOT become superuser #1.
    employee = create_employee(db, "Sara Khan")
    user = create_login(db, employee)
    superuser = create_superuser(db)
    assert user.id == superuser.id == 1

    response = client.get("/me", headers=log_in(client, employee.email))

    assert response.json()["kind"] == "user"
    assert response.json()["email"] == "sara.khan@test.local"
