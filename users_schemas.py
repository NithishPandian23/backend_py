"""
users_schemas.py — Pydantic models for Phase B Users CRUD endpoints.

Mirrors the shapes the frontend Users page already uses, plus a few backend-
only fields (createdAt, updatedAt, lastLogin).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Roles — same four roles your frontend already knows about
# ---------------------------------------------------------------------------
UserRole = Literal["admin", "operator", "viewer", "client"]


# ---------------------------------------------------------------------------
# Output — what GET /api/users and GET /api/users/{uid} return
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    """User profile as returned to the frontend."""
    uid: str
    name: Optional[str] = None
    email: EmailStr
    role: UserRole
    clientId: Optional[str] = None
    isActive: bool = True
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    lastLogin: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class UserCreateIn(BaseModel):
    """Body for POST /api/users."""
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole
    clientId: Optional[str] = Field(
        None,
        description="Required for role=client. Forbidden for role=admin.",
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class UserUpdateIn(BaseModel):
    """
    Body for PUT /api/users/{uid}.

    All fields optional — only provided fields are updated. Email cannot be
    changed (would require a Firebase Auth re-verification flow we don't have
    yet).
    """
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    role: Optional[UserRole] = None
    clientId: Optional[str] = None
    isActive: Optional[bool] = None


# ---------------------------------------------------------------------------
# Password reset (admin-triggered)
# ---------------------------------------------------------------------------
class PasswordResetIn(BaseModel):
    """
    Body for POST /api/users/{uid}/reset-password.
    Sets the user's password directly. (Per your spec, option (a) — admin types
    the password into the form, user logs in with it, optionally changes it
    on first login. We aren't building the email-flow path.)
    """
    newPassword: str = Field(..., min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Bulk fetch response — used by GET /api/users
# ---------------------------------------------------------------------------
class UserListOut(BaseModel):
    """Wrapper around a list of users so we can add pagination later."""
    users: list[UserOut]
    total: int