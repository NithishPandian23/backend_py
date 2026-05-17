"""
Firebase Admin initialization + FastAPI auth dependency.

Initializes firebase_admin once at import time using the service account
credentials from GOOGLE_APPLICATION_CREDENTIALS.

Exposes:
    - db: Firestore client (named database "wind-turbine-db")
    - get_current_user(): FastAPI dependency that verifies the Bearer token,
      looks up the user's profile in Firestore, and returns the merged
      identity (uid, email, role, client_id, etc.)
    - require_admin(): wrapper that 403s if the caller isn't an admin
"""
import os
from typing import Optional, Annotated

import firebase_admin
from firebase_admin import credentials, auth as fb_auth
from google.cloud import firestore as gcf_firestore
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "wind-turbine-demo-123456")
DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "wind-turbine-db")

# Only initialize once — guards against hot-reload double init in dev
if not firebase_admin._apps:
    # ApplicationDefault picks up GOOGLE_APPLICATION_CREDENTIALS automatically
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": PROJECT_ID})

# Firestore client bound to the named database.
# NOTE: We use google.cloud.firestore directly (not firebase_admin.firestore.client)
# because the firebase_admin wrapper doesn't accept a `database` argument and would
# always return the (default) database. Our tags collection lives in
# `wind-turbine-db`, not `(default)`, so we have to talk to the right one.
db = gcf_firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CurrentUser(BaseModel):
    """Authenticated user identity assembled from Firebase Auth + Firestore profile."""
    uid: str
    email: str
    name: Optional[str] = None
    role: str  # "admin" | "client" | "operator" | "user"
    client_id: Optional[str] = None
    is_active: bool = True


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
) -> CurrentUser:
    """
    Verifies the Firebase ID token from the Authorization header, then loads
    the user's profile from Firestore at users/{uid}. Raises 401 on any
    failure.
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = creds.credentials
    try:
        decoded = fb_auth.verify_id_token(token, clock_skew_seconds=5)
    except fb_auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (fb_auth.InvalidIdTokenError, fb_auth.RevokedIdTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    uid = decoded["uid"]
    email = decoded.get("email", "")

    # Pull the profile document from Firestore
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        # User authenticated against Firebase but has no profile yet —
        # treat as forbidden (they shouldn't have an account if no profile).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not provisioned. Contact your administrator.",
        )

    data = doc.to_dict() or {}
    if data.get("isActive") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    return CurrentUser(
        uid=uid,
        email=email,
        name=data.get("name"),
        role=data.get("role", "user"),
        client_id=data.get("clientId"),
        is_active=data.get("isActive", True),
    )


def require_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """Dependency that allows only admin users through."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user