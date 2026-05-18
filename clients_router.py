"""
clients_router.py — Phase C Clients CRUD endpoints.

Endpoints (all under /api/clients prefix):
  GET    /              → list clients
  POST   /              → create client (admin only)
  GET    /{client_id}   → get one client
  PUT    /{client_id}   → update client (admin only)
  DELETE /{client_id}   → delete client (admin only)
  POST   /{client_id}/allocate → replace this client's turbine allocation

Access rules:
  * admin: full access
  * client: can only GET their own client record (read-only)
  * operator/viewer: 403 on every endpoint

Allocation rules:
  * Turbines can only belong to ONE client at a time
  * Allocating turbine X to client A while it currently belongs to client B
    automatically moves it (server-side, atomic via transaction)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore as gcf_firestore

from firebase_auth import CurrentUser, db, get_current_user
from admin_schemas import (
    ClientAllocateIn,
    ClientCreateIn,
    ClientListOut,
    ClientOut,
    ClientUpdateIn,
)

router = APIRouter(prefix="/api/clients", tags=["clients"])

CLIENTS_COLLECTION = "clients"
USERS_COLLECTION = "users"
TURBINES_COLLECTION = "turbines"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )


def _doc_to_client_out(client_id: str, data: dict, user_count: int = 0) -> ClientOut:
    return ClientOut(
        id=client_id,
        name=data.get("name", ""),
        contact_email=data.get("contact_email", ""),
        logo_url=data.get("logo_url"),
        turbine_ids=data.get("turbine_ids", []),
        user_count=user_count,
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


def _count_users_for_client(client_id: str) -> int:
    """Count users whose clientId == this client. Used to populate user_count."""
    snaps = db.collection(USERS_COLLECTION).where("clientId", "==", client_id).stream()
    return sum(1 for _ in snaps)


# ---------------------------------------------------------------------------
# GET /api/clients — list
# ---------------------------------------------------------------------------

@router.get("", response_model=ClientListOut)
def list_clients(caller: Annotated[CurrentUser, Depends(get_current_user)]):
    """
    Admin: returns all clients.
    Client: returns only their own client (so the UI can show "My organization").
    Operator/viewer: 403.
    """
    if caller.role not in ("admin", "client"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and clients can view client list.",
        )

    if caller.role == "client":
        # Return only their own
        if not caller.client_id:
            return ClientListOut(clients=[], total=0)
        doc = db.collection(CLIENTS_COLLECTION).document(caller.client_id).get()
        if not doc.exists:
            return ClientListOut(clients=[], total=0)
        data = doc.to_dict() or {}
        return ClientListOut(
            clients=[_doc_to_client_out(doc.id, data, _count_users_for_client(doc.id))],
            total=1,
        )

    # Admin: all clients
    docs = list(db.collection(CLIENTS_COLLECTION).stream())
    clients = [
        _doc_to_client_out(d.id, d.to_dict() or {}, _count_users_for_client(d.id))
        for d in docs
    ]
    clients.sort(key=lambda c: c.name.lower())
    return ClientListOut(clients=clients, total=len(clients))


# ---------------------------------------------------------------------------
# GET /api/clients/{client_id} — one
# ---------------------------------------------------------------------------

@router.get("/{client_id}", response_model=ClientOut)
def get_client(
    client_id: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    # Clients can only see their own
    if caller.role == "client" and caller.client_id != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Client {client_id} not found")
    if caller.role not in ("admin", "client"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    doc = db.collection(CLIENTS_COLLECTION).document(client_id).get()
    if not doc.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Client {client_id} not found")
    data = doc.to_dict() or {}
    return _doc_to_client_out(client_id, data, _count_users_for_client(client_id))


# ---------------------------------------------------------------------------
# POST /api/clients — create
# ---------------------------------------------------------------------------

@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: ClientCreateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(caller)

    now = datetime.now(timezone.utc)
    new_ref = db.collection(CLIENTS_COLLECTION).document()  # auto-id
    data = {
        "name": payload.name,
        "contact_email": payload.contact_email,
        "logo_url": payload.logo_url,
        "turbine_ids": [],
        "created_at": now,
        "updated_at": now,
        "created_by": caller.uid,
    }
    new_ref.set(data)
    return _doc_to_client_out(new_ref.id, data, 0)


# ---------------------------------------------------------------------------
# PUT /api/clients/{client_id} — update
# ---------------------------------------------------------------------------

@router.put("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: str,
    payload: ClientUpdateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(caller)

    ref = db.collection(CLIENTS_COLLECTION).document(client_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Client {client_id} not found")

    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    update_data["updated_at"] = datetime.now(timezone.utc)
    ref.update(update_data)

    merged = {**(snap.to_dict() or {}), **update_data}
    return _doc_to_client_out(client_id, merged, _count_users_for_client(client_id))


# ---------------------------------------------------------------------------
# DELETE /api/clients/{client_id}
# ---------------------------------------------------------------------------

@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: str,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    """
    Delete a client. CASCADES:
      * All turbines owned by this client get their client_id set to null
        (turbines become unassigned, but stay in the fleet)
      * All users with clientId == this client are NOT auto-deleted (admin
        must clean them up — that's a deliberate safety pause).
    """
    _require_admin(caller)

    ref = db.collection(CLIENTS_COLLECTION).document(client_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Client {client_id} not found")

    # Check for users still assigned — block delete if any exist
    user_count = _count_users_for_client(client_id)
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: {user_count} user(s) still assigned to this client. "
                   f"Reassign or delete those users first.",
        )

    # Unassign all turbines that point at this client
    turbines_q = db.collection(TURBINES_COLLECTION).where("client_id", "==", client_id).stream()
    batch = db.batch()
    for t in turbines_q:
        batch.update(t.reference, {"client_id": None, "updated_at": datetime.now(timezone.utc)})
    batch.delete(ref)
    batch.commit()
    return None


# ---------------------------------------------------------------------------
# POST /api/clients/{client_id}/allocate — replace allocation
# ---------------------------------------------------------------------------

@router.post("/{client_id}/allocate", response_model=ClientOut)
def allocate_turbines(
    client_id: str,
    payload: ClientAllocateIn,
    caller: Annotated[CurrentUser, Depends(get_current_user)],
):
    """
    Replace this client's turbine allocation.

    Behavior:
      * Each turbine_id in the payload is set to client_id = this client.
      * Any turbine not in the payload that previously belonged to this
        client gets client_id = null.
      * Turbines currently owned by OTHER clients get auto-moved to this
        client (the new payload is authoritative).

    This is a single transaction — either all writes succeed or none do.
    """
    _require_admin(caller)

    client_ref = db.collection(CLIENTS_COLLECTION).document(client_id)
    client_snap = client_ref.get()
    if not client_snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Client {client_id} not found")

    requested_ids = set(payload.turbine_ids)
    now = datetime.now(timezone.utc)

    @gcf_firestore.transactional
    def reassign(transaction: gcf_firestore.Transaction):
        # Pull all turbines we need to touch:
        #   1. Currently belonging to this client (might need to be unassigned)
        #   2. Requested (might need to be moved here from another client)
        current_q = db.collection(TURBINES_COLLECTION).where("client_id", "==", client_id)
        current_ids: set[str] = set()
        for t in current_q.stream(transaction=transaction):
            current_ids.add(t.id)

        # Turbines to unassign: previously here, not requested now
        to_unassign = current_ids - requested_ids
        # Turbines to assign: requested, not currently here
        to_assign = requested_ids - current_ids

        # Verify all requested turbines exist
        if to_assign:
            for tid in to_assign:
                ref = db.collection(TURBINES_COLLECTION).document(tid)
                snap = ref.get(transaction=transaction)
                if not snap.exists:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Turbine {tid} not found",
                    )

        # Apply updates
        for tid in to_unassign:
            ref = db.collection(TURBINES_COLLECTION).document(tid)
            transaction.update(ref, {"client_id": None, "updated_at": now})

        for tid in to_assign:
            ref = db.collection(TURBINES_COLLECTION).document(tid)
            transaction.update(ref, {"client_id": client_id, "updated_at": now})

        # Update client doc — keep turbine_ids in sync (denormalized cache)
        transaction.update(client_ref, {
            "turbine_ids": list(requested_ids),
            "updated_at": now,
        })

    transaction = db.transaction()
    reassign(transaction)

    # Return updated client
    updated_snap = client_ref.get()
    return _doc_to_client_out(
        client_id,
        updated_snap.to_dict() or {},
        _count_users_for_client(client_id),
    )