"""
users_router.py — Phase B Users CRUD endpoints.

Endpoints (all under /api/users prefix):
  GET    /              → list users (admin sees all, client sees own org only)
  POST   /              → create user
  GET    /{uid}         → get one user
  PUT    /{uid}         → update user
  DELETE /{uid}         → delete user
  POST   /{uid}/reset-password → set a new password (admin or self-org client)

Access rules:
  * admin: full access to all users
  * client: can only see/create/edit/delete users whose clientId matches their
    own clientId, AND can only create role=operator or role=viewer (not admin
    or client). clientId is auto-filled from the caller, ignoring any value
    they pass.
  * operator/viewer: 403 on every endpoint

When creating a user this router performs TWO operations:
  1. firebase_admin.auth.create_user() — Firebase Auth user
  2. db.collection("users").document(uid).set() — Firestore profile

Both must succeed or we roll back. If Firestore write fails after Auth user
was created, we delete the Auth user. Best-effort rollback (if the rollback
delete itself fails, we log and continue — the user will exist in Auth without
a profile and they'll get a 403 on next /api/auth/me, which the admin can fix
by deleting them from the Firebase Console).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from firebase_admin import auth as fb_auth

from firebase_auth import CurrentUser, db, get_current_user
from users_schemas import (
    PasswordResetIn,
    UserCreateIn,
    UserListOut,
    UserOut,
    UserUpdateIn,
)

router = APIRouter(prefix="/api/users", tags=["users"])

USERS_COLLECTION = "users"


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

def _require_admin_or_client(user: CurrentUser) -> None:
    """Operators and viewers cannot manage users. Reject upfront."""
    if user.role not in ("admin", "client"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and clients can manage users.",
        )


def _can_see_user(caller: CurrentUser, target_role: str, target_client_id: Optional[str]) -> bool:
    """
    Visibility check. Admins see everyone. Clients see their own org's users.

    Note: a client user themselves IS visible to other client users in the
    same org (think: an org's operations lead being able to see all their
    team). But we don't currently expose self-promotion — a client can't
    edit another client-role user, they can only manage operator/viewer.
    Edit/delete enforcement is in the respective endpoints.
    """
    if caller.role == "admin":
        return True
    if caller.role == "client":
        return target_client_id is not None and target_client_id == caller.client_id
    return False


def _validate_create_payload(caller: CurrentUser, payload: UserCreateIn) -> None:
    """
    Enforce role-based create rules. Mutates payload.clientId if needed.

    * admin: can create any role. clientId required if role=client, forbidden otherwise.
    * client: can ONLY create role=operator or role=viewer. clientId is auto-set
      to the caller's clientId (any value they passed is ignored).
    """
    if caller.role == "admin":
        if payload.role == "admin" and payload.clientId is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin users cannot be associated with a client.",
            )
        if payload.role == "client" and not payload.clientId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Client users must have a clientId.",
            )
        # operator and viewer can optionally have a clientId (e.g. an operator
        # employed by a specific client org). No constraint.
        return

    # caller is a client
    if payload.role not in ("operator", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clients can only create operator or viewer users.",
        )
    # Force the new user into the caller's org regardless of what they sent
    payload.clientId = caller.client_id


def _validate_update_payload(
    caller: CurrentUser,
    target: dict,
    payload: UserUpdateIn,
) -> None:
    """
    Enforce role-based update rules.

    * admin: no restrictions
    * client: target must be in same org, AND target role must be operator/viewer,
      AND new role (if provided) must be operator/viewer, AND clientId cannot
      be changed (we strip it from the payload).
    """
    if caller.role == "admin":
        return

    # caller is client
    if target.get("clientId") != caller.client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit users in your own organization.",
        )
    if target.get("role") not in ("operator", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit operator and viewer users.",
        )
    if payload.role is not None and payload.role not in ("operator", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can only set role to operator or viewer.",
        )
    # Clients cannot move users between orgs — strip clientId from the update
    if payload.clientId is not None and payload.clientId != caller.client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot move users to another organization.",
        )


def _doc_to_user_out(uid: str, data: dict) -> UserOut:
    """Convert a Firestore document into the response shape."""
    return UserOut(
        uid=uid,
        name=data.get("name"),
        email=data.get("email"),
        role=data.get("role", "viewer"),
        clientId=data.get("clientId"),
        isActive=data.get("isActive", True),
        createdAt=data.get("createdAt"),
        updatedAt=data.get("updatedAt"),
        lastLogin=data.get("lastLogin"),
    )


# ---------------------------------------------------------------------------
# GET /api/users — list
# ---------------------------------------------------------------------------

@router.get("", response_model=UserListOut)
def list_users(
    caller: Annotated[CurrentUser, Depends(get_current_user)],
    role: Optional[str] = Query(None, description="Filter by role"),
    client_id: Optional[str] = Query(None, description="Filter by clientId (admin only)"),
):
    _require_admin_or_client(caller)

    q = db.collection(USERS_COLLECTION)
    if role:
        q = q.where("role", "==", role)
    # Clients always get their own org's users, regardless of any client_id param
    if caller.role == "client":
        q = q.where("clientId", "==", caller.client_id)
    elif client_id:
        q = q.where("clientId", "==", client_id)

    docs = list(q.stream())
    users = [_doc_to_user_out(d.id, d.to_dict() or {}) for d in docs]
    # Sort newest first
    users.sort(key=lambda u: u.createdAt or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return UserListOut(users=users, total=len(users))


# ---------------------------------------------------------------------------
# GET /api/users/{uid} — one
# ---------------------------------------------------------------------------

@router.get("/{uid}", response_model=UserOut)
def get_user(
    uid: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin_or_client(caller)
    doc = db.collection(USERS_COLLECTION).document(uid).get()
    if not doc.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")
    data = doc.to_dict() or {}
    if not _can_see_user(caller, data.get("role", ""), data.get("clientId")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")
    return _doc_to_user_out(uid, data)


# ---------------------------------------------------------------------------
# POST /api/users — create
# ---------------------------------------------------------------------------

@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin_or_client(caller)
    _validate_create_payload(caller, payload)

    # Check email isn't already in use (Firebase will reject too, but we want a
    # cleaner error message)
    try:
        existing = fb_auth.get_user_by_email(payload.email)
        if existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A user with email {payload.email} already exists.",
            )
    except fb_auth.UserNotFoundError:
        pass  # good — email is free

    # Step 1: create Firebase Auth user
    try:
        fb_user = fb_auth.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.name,
            email_verified=True,
        )
    except fb_auth.EmailAlreadyExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A user with email {payload.email} already exists.",
        )

    uid = fb_user.uid
    now = datetime.now(timezone.utc)

    profile = {
        "uid": uid,
        "name": payload.name,
        "email": payload.email,
        "role": payload.role,
        "clientId": payload.clientId,
        "isActive": True,
        "createdAt": now,
        "updatedAt": now,
        "createdBy": caller.uid,
    }

    # Step 2: write Firestore profile. If this fails, roll back the Auth user.
    try:
        db.collection(USERS_COLLECTION).document(uid).set(profile)
    except Exception as e:
        # Best-effort rollback
        try:
            fb_auth.delete_user(uid)
        except Exception:
            pass  # we tried
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to create user profile: {e}",
        )

    return _doc_to_user_out(uid, profile)


# ---------------------------------------------------------------------------
# PUT /api/users/{uid} — update
# ---------------------------------------------------------------------------

@router.put("/{uid}", response_model=UserOut)
def update_user(
    uid: str,
    payload: UserUpdateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin_or_client(caller)

    ref = db.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    target = snap.to_dict() or {}
    if not _can_see_user(caller, target.get("role", ""), target.get("clientId")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    _validate_update_payload(caller, target, payload)

    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    update_data["updatedAt"] = datetime.now(timezone.utc)

    # Sync the display name to Firebase Auth too, so it shows up in the
    # Firebase Console correctly.
    if "name" in update_data:
        try:
            fb_auth.update_user(uid, display_name=update_data["name"])
        except Exception:
            pass  # non-fatal — Firestore is the source of truth

    # Sync disabled flag too
    if "isActive" in update_data:
        try:
            fb_auth.update_user(uid, disabled=not update_data["isActive"])
        except Exception:
            pass

    ref.update(update_data)
    merged = {**target, **update_data}
    return _doc_to_user_out(uid, merged)


# ---------------------------------------------------------------------------
# DELETE /api/users/{uid}
# ---------------------------------------------------------------------------

@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    uid: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin_or_client(caller)

    # Don't let users delete themselves — that locks them out
    if uid == caller.uid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot delete your own account.",
        )

    ref = db.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    target = snap.to_dict() or {}
    if not _can_see_user(caller, target.get("role", ""), target.get("clientId")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    # Clients additionally cannot delete other client-role users
    if caller.role == "client" and target.get("role") == "client":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Clients cannot delete other client users.",
        )

    # Delete Firebase Auth user first (so they can't log in even if the Firestore
    # delete fails). If Auth delete succeeds but Firestore fails, the orphaned
    # profile will return 403 on next /api/auth/me, which is acceptable.
    try:
        fb_auth.delete_user(uid)
    except fb_auth.UserNotFoundError:
        pass  # already gone — proceed to remove the profile

    ref.delete()
    return None


# ---------------------------------------------------------------------------
# POST /api/users/{uid}/reset-password
# ---------------------------------------------------------------------------

@router.post("/{uid}/reset-password", status_code=status.HTTP_200_OK)
def reset_password(
    uid: str,
    payload: PasswordResetIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin_or_client(caller)

    ref = db.collection(USERS_COLLECTION).document(uid)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    target = snap.to_dict() or {}
    if not _can_see_user(caller, target.get("role", ""), target.get("clientId")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {uid} not found")

    # Clients can't reset other client users' passwords
    if caller.role == "client" and target.get("role") == "client" and uid != caller.uid:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Clients cannot reset other client users' passwords.",
        )

    fb_auth.update_user(uid, password=payload.newPassword)
    ref.update({"updatedAt": datetime.now(timezone.utc)})

    return {"status": "ok", "message": "Password updated."}