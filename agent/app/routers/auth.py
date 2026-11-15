# Logging in and out of the chat.
#
# The user types their email and password into the LOGIN FORM in the app, never into the chat.
# The form sends them here, we pass them on to the CRUD API, and we keep the tokens we get back.

from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, ConfigDict

from app import crud_api
from app.sessions import SESSION_COOKIE, ChatSession, create_session, current_session, delete_session

router = APIRouter(tags=["Auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject unknown fields

    email: str
    password: str


@router.post("/auth/login")
def login(data: LoginRequest, response: Response):
    """Log in. A wrong password gives the CRUD API's own 401 error."""
    access_token, refresh_token = crud_api.log_in(data.email, data.password)
    session_id = create_session(access_token, refresh_token)

    # The browser gets ONLY the session id, as a cookie:
    #   httponly=True  -> JavaScript on the page can't read it, so a malicious script can't steal it
    #   samesite="lax" -> other websites can't make the browser send it with their requests
    # (In production you would also add secure=True, so it is only sent over HTTPS.)
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return {"message": "Logged in"}


@router.post("/auth/logout")
def logout(response: Response, session_id: Optional[str] = Cookie(default=None)):
    """Log out: forget the tokens and remove the cookie."""
    delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "Logged out"}


@router.get("/me")
def me(session: ChatSession = Depends(current_session)) -> Any:
    """Who is logged in? We ask the CRUD API, using this user's own token."""
    return crud_api.call(session, "GET", "/me")
