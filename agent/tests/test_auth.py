# Tests for logging in, "who am I?" and logging out (app/routers/auth.py).

from fastapi.testclient import TestClient

from app.sessions import SESSIONS, ChatSession
from tests.fake_crud_api import EMAIL, ME, PASSWORD, FakeCrudApi


def log_in(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200


# ---------- Logging in ----------

def test_login_gives_the_browser_only_a_session_cookie(client: TestClient, fake_api: FakeCrudApi) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    # The tokens stay on the server: they are not in the answer the browser gets.
    assert fake_api.access_token not in response.text
    assert fake_api.access_token not in response.headers["set-cookie"]


def test_a_wrong_password_gives_401_and_no_session(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Wrong email or password"
    assert SESSIONS == {}


def test_login_rejects_unknown_fields(client: TestClient) -> None:
    data = {"email": EMAIL, "password": PASSWORD, "role": "admin"}

    assert client.post("/auth/login", json=data).status_code == 422


# ---------- Who am I? ----------

def test_me_asks_the_crud_api_with_the_users_own_token(client: TestClient, fake_api: FakeCrudApi) -> None:
    log_in(client)

    response = client.get("/me")

    assert response.json() == ME
    sent = fake_api.requests[-1]
    assert sent.headers["Authorization"] == f"Bearer {fake_api.access_token}"
    assert sent.headers["X-Request-Source"] == "agent"


def test_me_without_logging_in_gives_401(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_a_made_up_session_id_gives_401(client: TestClient) -> None:
    client.cookies.set("session_id", "i-made-this-up")

    assert client.get("/me").status_code == 401


# ---------- Tokens running out ----------

def test_an_expired_access_token_is_renewed_automatically(client: TestClient, fake_api: FakeCrudApi) -> None:
    log_in(client)
    fake_api.expire_access_token()

    response = client.get("/me")

    assert response.status_code == 200
    paths = [request.url.path for request in fake_api.requests]
    assert paths[-3:] == ["/me", "/auth/refresh", "/me"]  # failed, renewed, tried again


def test_when_the_login_has_ended_the_user_must_log_in_again(client: TestClient, fake_api: FakeCrudApi) -> None:
    log_in(client)
    fake_api.expire_access_token()
    fake_api.refresh_works = False  # e.g. the account was disabled, or 7 days have passed

    response = client.get("/me")

    assert response.status_code == 401


# ---------- Logging out ----------

def test_after_logout_the_session_is_gone(client: TestClient) -> None:
    log_in(client)

    client.post("/auth/logout")

    assert SESSIONS == {}
    assert client.get("/me").status_code == 401


# ---------- Tokens never leak ----------

def test_printing_a_session_does_not_show_the_tokens() -> None:
    session = ChatSession(access_token="secret-access", refresh_token="secret-refresh")

    assert "secret" not in repr(session)
    assert "secret" not in str(session)
