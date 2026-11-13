# Pending actions: big changes (terminate, salary, role, manager, deletes) that wait for the user.
#
# How it works:
#   1. POST /actions                 -> the AI agent (or the app) asks for a change. Nothing changes yet!
#                                       The API checks it and builds a preview from the real data.
#   2. The app shows the preview with a "Confirm" button.
#   3. POST /actions/{id}/confirm    -> ONLY the app calls this, when the user clicks. Now the change runs.
#
# Safety rules:
#   - Only the account that asked can see, confirm or cancel the action.
#   - It can be confirmed only once, and only within 5 minutes.
#   - It runs exactly the stored arguments, through the real endpoint with all its checks.
#   - If the record changed since the preview, confirm fails with 409. Ask again for a fresh preview.
#   - The AI agent can NOT confirm. Typing "yes" in the chat never confirms anything.

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import Origin, get_origin
from app.database import get_db
from app.models import PendingAction, utc_now
from app.pending_actions import CONFIRM_MINUTES, check_args, describe, run
from app.permissions import HR_ROLES, require_role
from app.schemas import ConfirmOut, PendingActionCreate, PendingActionOut
from app.security import Account, account_kind, get_current_user

router = APIRouter(prefix="/actions", tags=["Pending actions"], dependencies=[Depends(get_current_user)])


def find_my_action(db: Session, account: Account, action_id: int) -> PendingAction:
    """Load a pending action that belongs to this account. Someone else's gives 404."""
    pending = db.get(PendingAction, action_id)
    if pending is None or pending.account_kind != account_kind(account) or pending.account_id != account.id:
        raise HTTPException(status_code=404, detail="Pending action not found")
    return pending


@router.post("", response_model=PendingActionOut, status_code=201)
def create_action(
    data: PendingActionCreate,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """Ask for a big change. Returns a preview. Nothing is changed until it is confirmed."""
    # All actions that need confirmation are hr/admin actions. Stop everyone else right away.
    # (When the action runs, the real endpoint checks everything again.)
    require_role(account, HR_ROLES)

    args = check_args(data.action, data.args)
    preview, version = describe(db, account, data.action, data.target_id, args)

    pending = PendingAction(
        account_kind=account_kind(account),
        account_id=account.id,
        action=data.action,
        target_id=data.target_id,
        args=args,
        target_version=version,
        preview=preview,
        status="pending",
        expires_at=utc_now() + timedelta(minutes=CONFIRM_MINUTES),
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    return pending


@router.get("/{action_id}", response_model=PendingActionOut)
def get_action(
    action_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    return find_my_action(db, account, action_id)


@router.post("/{action_id}/confirm", response_model=ConfirmOut)
def confirm_action(
    action_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
    origin: Origin = Depends(get_origin),
):
    """The user clicked "Confirm" in the app. Run the change now."""
    if origin.source == "agent":
        raise HTTPException(status_code=403, detail="Only the user can confirm, in the app")

    pending = find_my_action(db, account, action_id)

    if pending.status != "pending":
        raise HTTPException(status_code=400, detail=f"This action is already {pending.status}")

    if utc_now() > pending.expires_at:
        pending.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="This action has expired. Please ask again.")

    # Did someone change the record since the preview was made?
    _, version_now = describe(db, account, pending.action, pending.target_id, pending.args)
    if version_now != pending.target_version:
        raise HTTPException(status_code=409, detail="The record changed since the preview. Please ask again.")

    # Mark it as done. It is saved together with the change itself, in the endpoint's commit.
    # If the change fails (for example a permission check), nothing is saved and it stays "pending".
    pending.status = "done"
    pending.decided_at = utc_now()

    # source "agent" + the pending action id are written into the audit log.
    result = run(db, account, Origin(source="agent", pending_action_id=pending.id),
                 pending.action, pending.target_id, pending.args)

    db.refresh(pending)
    return ConfirmOut(action=PendingActionOut.model_validate(pending), result=result)


@router.post("/{action_id}/cancel", response_model=PendingActionOut)
def cancel_action(
    action_id: int,
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """The user clicked "Cancel". The change will never run."""
    pending = find_my_action(db, account, action_id)

    if pending.status != "pending":
        raise HTTPException(status_code=400, detail=f"This action is already {pending.status}")

    pending.status = "cancelled"
    pending.decided_at = utc_now()
    db.commit()
    db.refresh(pending)
    return pending
