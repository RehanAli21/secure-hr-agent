# Login endpoints: log in, get a new access token, and "who am I?".

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SuperUser, User
from app.schemas import AccessTokenOut, LoginRequest, MeOut, RefreshRequest, TokensOut
from app.security import (
    Account,
    create_access_token,
    create_refresh_token,
    get_current_user,
    load_account,
    read_token,
    verify_password,
)

router = APIRouter(tags=["Auth"])


@router.post("/auth/login", response_model=TokensOut)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Log in as a normal user."""
    user = db.query(User).filter(User.email == data.email).first()

    # We give the SAME error for "no such email" and "wrong password",
    # so nobody can use this endpoint to find out which emails exist.
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="This account is disabled")

    return TokensOut(
        access_token=create_access_token(user.id, "user"),
        refresh_token=create_refresh_token(user.id, "user"),
    )


@router.post("/auth/superuser/login", response_model=TokensOut)
def superuser_login(data: LoginRequest, db: Session = Depends(get_db)):
    """Log in as a superuser (an account that can access everything)."""
    superuser = db.query(SuperUser).filter(SuperUser.email == data.email).first()

    if superuser is None or not verify_password(data.password, superuser.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password")

    return TokensOut(
        access_token=create_access_token(superuser.id, "superuser"),
        refresh_token=create_refresh_token(superuser.id, "superuser"),
    )


@router.post("/auth/refresh", response_model=AccessTokenOut)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)):
    """Trade a refresh token for a new access token. Works for both kinds of accounts."""
    account_id, kind = read_token(data.refresh_token, expected_type="refresh")

    # Check the account again: if it was disabled or removed, it gets no new token.
    account = load_account(db, account_id, kind)

    return AccessTokenOut(access_token=create_access_token(account.id, kind))


@router.get("/me", response_model=MeOut)
def me(current_user: Account = Depends(get_current_user)):
    """Return the logged-in account. Who you are comes only from your token."""
    if isinstance(current_user, SuperUser):
        return MeOut(id=current_user.id, email=current_user.email, kind="superuser")

    return MeOut(
        id=current_user.id,
        email=current_user.email,
        kind="user",
        employee_id=current_user.employee_id,
        role=current_user.role,
    )
