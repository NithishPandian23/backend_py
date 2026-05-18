"""
admin_schemas.py — Pydantic models for Phase C CRUD endpoints.

Covers:
  - Clients (companies that own turbines)
  - Turbines (the physical assets)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

# ===========================================================================
# CLIENTS
# ===========================================================================

class ClientOut(BaseModel):
    """Client company as returned to the frontend."""
    id: str
    name: str
    contact_email: EmailStr
    logo_url: Optional[str] = None
    turbine_ids: list[str] = Field(default_factory=list)
    user_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ClientCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    contact_email: EmailStr
    logo_url: Optional[str] = Field(None, max_length=2048)


class ClientUpdateIn(BaseModel):
    """Partial update — all fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    contact_email: Optional[EmailStr] = None
    logo_url: Optional[str] = Field(None, max_length=2048)


class ClientAllocateIn(BaseModel):
    """Replace the client's turbine allocation. Backend handles unassignment from other clients."""
    turbine_ids: list[str]


class ClientListOut(BaseModel):
    clients: list[ClientOut]
    total: int


# ===========================================================================
# TURBINES
# ===========================================================================

TurbineStatus = Literal["online", "warning", "alarm", "offline"]


class TurbineLocation(BaseModel):
    """Geo + site info for a turbine."""
    site: str
    state: str
    lat: float
    lng: float


class TurbineOut(BaseModel):
    """
    Turbine metadata as returned to the frontend.

    NOTE: this does NOT include live telemetry (power_kw, rpm, wind_speed).
    Those values are synthesized client-side by the simulator. The backend
    stores only the *static* asset record.
    """
    id: str
    name: str
    serial: str
    model: str
    rated_power_kw: int
    hub_height_m: int
    rotor_diameter_m: int
    commissioned: str  # ISO date
    location: TurbineLocation
    client_id: Optional[str] = None
    decommissioned: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TurbineUpdateIn(BaseModel):
    """Partial update of turbine metadata. All fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    client_id: Optional[str] = None
    decommissioned: Optional[bool] = None


class TurbineReassignIn(BaseModel):
    """Move a turbine to a different client (or unassign with null)."""
    client_id: Optional[str] = None


class TurbineListOut(BaseModel):
    turbines: list[TurbineOut]
    total: int