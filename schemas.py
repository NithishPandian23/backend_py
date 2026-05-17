"""
Pydantic schemas for the admin API.

Phase A defines the User shapes used by /api/auth/me.
Phase B will extend with full CRUD payloads.
Phase C will add Client + Turbine schemas.
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field


UserRole = Literal["admin", "client", "operator", "user"]


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    """A user's profile as returned from /api/auth/me and /api/users."""
    uid: str
    name: Optional[str] = None
    email: EmailStr
    role: UserRole
    clientId: Optional[str] = None
    isActive: bool = True
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    lastLogin: Optional[datetime] = None

    model_config = {"populate_by_name": True}