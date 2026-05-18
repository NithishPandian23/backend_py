"""
turbines_router.py — Phase C Turbines CRUD endpoints.

Endpoints (all under /api/turbines prefix):
  GET    /                       → list turbines (CLIENT-SCOPED)
  GET    /unfiltered             → list ALL turbines including decommissioned (admin only)
  GET    /{turbine_id}           → get one turbine
  PUT    /{turbine_id}           → update turbine metadata (admin only)
  POST   /{turbine_id}/reassign  → change turbine's client (admin only)
  POST   /{turbine_id}/decommission   → mark decommissioned (admin only)
  POST   /{turbine_id}/recommission   → mark active again (admin only)

Access rules:
  * admin: full access to all endpoints
  * client: GET / returns only turbines where client_id == caller's clientId.
            All mutations are 403.
  * operator: GET / returns ALL active turbines (no scope filter).
              All mutations are 403.
  * viewer: same as operator (read-only).

This is the SERVER-SIDE enforcement of client scoping. The frontend filter
that lived in `useTurbines()` becomes redundant — Phase D will remove it.

NOTE: Telemetry (power_kw, rpm, wind_speed) is NOT stored in these documents.
The frontend simulator generates that locally. Backend stores only the
static asset metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from google.cloud import firestore as gcf_firestore

from firebase_auth import CurrentUser, db, get_current_user
from admin_schemas import (
    TurbineListOut,
    TurbineLocation,
    TurbineOut,
    TurbineReassignIn,
    TurbineUpdateIn,
)

router = APIRouter(prefix="/api/turbines", tags=["turbines"])

TURBINES_COLLECTION = "turbines"
CLIENTS_COLLECTION = "clients"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )


def _doc_to_turbine_out(turbine_id: str, data: dict) -> TurbineOut:
    loc = data.get("location", {}) or {}
    return TurbineOut(
        id=turbine_id,
        name=data.get("name", ""),
        serial=data.get("serial", ""),
        model=data.get("model", ""),
        rated_power_kw=int(data.get("rated_power_kw", 0)),
        hub_height_m=int(data.get("hub_height_m", 0)),
        rotor_diameter_m=int(data.get("rotor_diameter_m", 0)),
        commissioned=data.get("commissioned", ""),
        location=TurbineLocation(
            site=loc.get("site", ""),
            state=loc.get("state", ""),
            lat=float(loc.get("lat", 0)),
            lng=float(loc.get("lng", 0)),
        ),
        client_id=data.get("client_id"),
        decommissioned=bool(data.get("decommissioned", False)),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


def _maybe_update_client_turbine_ids(client_id: Optional[str], turbine_id: str, *, add: bool):
    """
    Keep the denormalized turbine_ids array on the client document in sync
    when we change a turbine's client_id.
    """
    if not client_id:
        return
    ref = db.collection(CLIENTS_COLLECTION).document(client_id)
    snap = ref.get()
    if not snap.exists:
        return
    ids = list((snap.to_dict() or {}).get("turbine_ids", []))
    if add and turbine_id not in ids:
        ids.append(turbine_id)
    elif not add and turbine_id in ids:
        ids.remove(turbine_id)
    ref.update({"turbine_ids": ids, "updated_at": datetime.now(timezone.utc)})


# ---------------------------------------------------------------------------
# GET /api/turbines — list (CLIENT-SCOPED)
# ---------------------------------------------------------------------------

@router.get("", response_model=TurbineListOut)
def list_turbines(
    caller: Annotated[CurrentUser, Depends(get_current_user)],
    include_decommissioned: bool = Query(False, description="Admin-only flag"),
):
    """
    Returns turbines visible to the caller.

      * admin:    all active turbines (or all including decommissioned if
                  include_decommissioned=true)
      * client:   only turbines where client_id == caller.client_id
      * operator/viewer: all active turbines (no client scoping)

    All callers see only active (non-decommissioned) turbines by default.
    """
    q = db.collection(TURBINES_COLLECTION)

    if caller.role == "client":
        if not caller.client_id:
            return TurbineListOut(turbines=[], total=0)
        q = q.where("client_id", "==", caller.client_id)

    docs = list(q.stream())
    turbines: list[TurbineOut] = []
    for d in docs:
        data = d.to_dict() or {}
        is_decom = bool(data.get("decommissioned", False))
        # Filter out decommissioned for everyone except admin who explicitly opted in
        if is_decom and not (caller.role == "admin" and include_decommissioned):
            continue
        turbines.append(_doc_to_turbine_out(d.id, data))

    turbines.sort(key=lambda t: t.name.lower())
    return TurbineListOut(turbines=turbines, total=len(turbines))


# ---------------------------------------------------------------------------
# GET /api/turbines/unfiltered — admin-only, ALL turbines incl. decommissioned
# ---------------------------------------------------------------------------

@router.get("/unfiltered", response_model=TurbineListOut)
def list_turbines_unfiltered(
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    """
    Admin-only endpoint that returns EVERY turbine including decommissioned.
    Used by the /admin/turbines page so the admin can see and recommission
    retired turbines.
    """
    _require_admin(caller)

    docs = list(db.collection(TURBINES_COLLECTION).stream())
    turbines = [_doc_to_turbine_out(d.id, d.to_dict() or {}) for d in docs]
    turbines.sort(key=lambda t: t.name.lower())
    return TurbineListOut(turbines=turbines, total=len(turbines))


# ---------------------------------------------------------------------------
# GET /api/turbines/{turbine_id} — one
# ---------------------------------------------------------------------------

@router.get("/{turbine_id}", response_model=TurbineOut)
def get_turbine(
    turbine_id: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    ref = db.collection(TURBINES_COLLECTION).document(turbine_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    data = snap.to_dict() or {}
    # Client scope check
    if caller.role == "client" and data.get("client_id") != caller.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    return _doc_to_turbine_out(turbine_id, data)


# ---------------------------------------------------------------------------
# PUT /api/turbines/{turbine_id} — update
# ---------------------------------------------------------------------------

@router.put("/{turbine_id}", response_model=TurbineOut)
def update_turbine(
    turbine_id: str,
    payload: TurbineUpdateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(caller)

    ref = db.collection(TURBINES_COLLECTION).document(turbine_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    old = snap.to_dict() or {}
    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    update_data["updated_at"] = datetime.now(timezone.utc)

    # If client_id is changing, sync the denormalized turbine_ids arrays on both client docs
    if "client_id" in update_data and update_data["client_id"] != old.get("client_id"):
        _maybe_update_client_turbine_ids(old.get("client_id"), turbine_id, add=False)
        _maybe_update_client_turbine_ids(update_data["client_id"], turbine_id, add=True)

    ref.update(update_data)
    merged = {**old, **update_data}
    return _doc_to_turbine_out(turbine_id, merged)


# ---------------------------------------------------------------------------
# POST /api/turbines/{turbine_id}/reassign — change client
# ---------------------------------------------------------------------------

@router.post("/{turbine_id}/reassign", response_model=TurbineOut)
def reassign_turbine(
    turbine_id: str,
    payload: TurbineReassignIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    """
    Move a turbine to a different client (or unassign by sending null).

    Same effect as PUT /api/turbines/{id} with just client_id, but this
    endpoint is the canonical entry point so the frontend admin Fleet page
    can call it without confusion.
    """
    _require_admin(caller)

    ref = db.collection(TURBINES_COLLECTION).document(turbine_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    # Verify new client exists if provided
    if payload.client_id is not None:
        client_snap = db.collection(CLIENTS_COLLECTION).document(payload.client_id).get()
        if not client_snap.exists:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Client {payload.client_id} not found",
            )

    old = snap.to_dict() or {}
    old_client_id = old.get("client_id")
    new_client_id = payload.client_id

    if old_client_id != new_client_id:
        _maybe_update_client_turbine_ids(old_client_id, turbine_id, add=False)
        _maybe_update_client_turbine_ids(new_client_id, turbine_id, add=True)

    now = datetime.now(timezone.utc)
    ref.update({"client_id": new_client_id, "updated_at": now})

    merged = {**old, "client_id": new_client_id, "updated_at": now}
    return _doc_to_turbine_out(turbine_id, merged)


# ---------------------------------------------------------------------------
# POST /api/turbines/{turbine_id}/decommission
# ---------------------------------------------------------------------------

@router.post("/{turbine_id}/decommission", response_model=TurbineOut)
def decommission_turbine(
    turbine_id: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(caller)

    ref = db.collection(TURBINES_COLLECTION).document(turbine_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    now = datetime.now(timezone.utc)
    ref.update({"decommissioned": True, "updated_at": now})

    merged = {**(snap.to_dict() or {}), "decommissioned": True, "updated_at": now}
    return _doc_to_turbine_out(turbine_id, merged)


# ---------------------------------------------------------------------------
# POST /api/turbines/{turbine_id}/recommission
# ---------------------------------------------------------------------------

@router.post("/{turbine_id}/recommission", response_model=TurbineOut)
def recommission_turbine(
    turbine_id: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(caller)

    ref = db.collection(TURBINES_COLLECTION).document(turbine_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Turbine {turbine_id} not found")

    now = datetime.now(timezone.utc)
    ref.update({"decommissioned": False, "updated_at": now})

    merged = {**(snap.to_dict() or {}), "decommissioned": False, "updated_at": now}
    return _doc_to_turbine_out(turbine_id, merged)