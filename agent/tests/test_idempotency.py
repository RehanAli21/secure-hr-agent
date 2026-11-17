# Tests for the Idempotency-Key the agent sends with every write (app/crud_api.py): a write that
# has to be sent again carries the same key, so the CRUD API does it only once.

import pytest

from app import crud_api
from app.sessions import ChatSession
from tests.fake_crud_api import REFRESH_TOKEN, FakeCrudApi

LEAVE = {"leave_type": "annual", "start_date": "2099-03-02", "end_date": "2099-03-06"}


def a_session(fake_api: FakeCrudApi) -> ChatSession:
    return ChatSession(access_token=fake_api.access_token, refresh_token=REFRESH_TOKEN)


def keys_sent(fake_api: FakeCrudApi, method: str, path: str) -> list[str | None]:
    """The Idempotency-Key of every such request that reached the (fake) CRUD API."""
    return [r.headers.get("Idempotency-Key") for r in fake_api.requests
            if r.method == method and r.url.path == path]


def test_a_write_has_a_key_and_a_read_has_none(fake_api: FakeCrudApi) -> None:
    session = a_session(fake_api)

    crud_api.call(session, "POST", "/leave", json=LEAVE)
    crud_api.call(session, "GET", "/me")

    [key] = keys_sent(fake_api, "POST", "/leave")
    assert key is not None and len(key) == 36  # a random UUID, like "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
    assert keys_sent(fake_api, "GET", "/me") == [None]


def test_every_write_gets_a_new_key(fake_api: FakeCrudApi) -> None:
    session = a_session(fake_api)

    crud_api.call(session, "POST", "/leave", json=LEAVE)
    crud_api.call(session, "POST", "/leave", json=LEAVE)

    first, second = keys_sent(fake_api, "POST", "/leave")
    assert first != second  # a second, NEW change (whether it is allowed is up to the CRUD API)


def test_after_a_new_token_the_same_key_is_sent(fake_api: FakeCrudApi) -> None:
    session = a_session(fake_api)
    fake_api.expire_access_token()  # the first try gets 401, then the agent refreshes and tries again

    crud_api.call(session, "POST", "/leave", json=LEAVE)

    first, second = keys_sent(fake_api, "POST", "/leave")
    assert first == second


def test_after_a_network_failure_it_is_sent_once_more_with_the_same_key(fake_api: FakeCrudApi) -> None:
    session = a_session(fake_api)
    fake_api.fail_the_network("POST /leave", times=1)

    created = crud_api.call(session, "POST", "/leave", json=LEAVE)

    first, second = keys_sent(fake_api, "POST", "/leave")
    assert first == second  # so if the first one DID arrive, the CRUD API only replays its answer
    assert created["status"] == "pending"


def test_it_gives_up_after_a_second_network_failure(fake_api: FakeCrudApi) -> None:
    session = a_session(fake_api)
    fake_api.fail_the_network("POST /leave", times=2)

    with pytest.raises(crud_api.CrudApiError) as error:
        crud_api.call(session, "POST", "/leave", json=LEAVE)

    assert error.value.status_code == 503
    assert len(keys_sent(fake_api, "POST", "/leave")) == 2  # tried twice, no more
