"""
Wind Turbine Tag API — FastAPI backend backed by Google Cloud Firestore.

Endpoints
---------
GET    /                          → API info / health
GET    /api/health                → Phase A liveness check
GET    /api/auth/me               → Phase A: verify Firebase token, return profile
GET    /tags                      → list all tag definitions (paginated, filterable)
GET    /tags/{tag_id}             → get a single tag's metadata + current value
GET    /tags/by-name/{tag_name}   → look up a tag by its full name
POST   /tags/{tag_id}/value       → update value for one tag
POST   /tags/values/batch         → update many tag values at once (FAST)
GET    /tags/{tag_id}/history     → recent value history for one tag
GET    /categories                → list of categories + counts
GET    /tags/critical/active      → all critical tags with active alarms

Run locally
-----------
    $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\\keys\\wind-turbine-demo.json"
    $env:GCP_PROJECT_ID = "wind-turbine-demo-123456"
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore
from pydantic import BaseModel, Field

# Phase A auth — importing firebase_auth has the side-effect of initializing
# the Firebase Admin SDK. It must be imported BEFORE we use the db client.
from firebase_auth import (
    get_current_user,
    require_admin,
    CurrentUser,
    db as firebase_db,
)
from schemas import UserProfile

from users_router import router as users_router
from clients_router  import router as clients_router
from turbines_router import router as turbines_router

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "wind-turbine-demo-123456")
DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "wind-turbine-db")

# Firestore client used by all the /tags endpoints below.
# (firebase_auth.py already creates its own client for the users collection;
# we use a separate one here to keep the two namespaces clearly distinct.)
db = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

TAGS_COLLECTION = "wind_turbine_tags"
HISTORY_SUBCOLLECTION = "history"
MAX_HISTORY_PER_TAG = 1000


# ---------------------------------------------------------------------------
# FastAPI app — SINGLE instance, not two
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Wind Turbine Monitoring API",
    description="Backend API for Vayona Energy wind turbine monitoring platform.",
    version="1.1.0",
)

app.include_router(users_router)
app.include_router(clients_router)
app.include_router(turbines_router)

# CORS — explicit because the frontend sends Authorization headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic models — request/response shapes
# ---------------------------------------------------------------------------
class TagValueIn(BaseModel):
    """Single value update."""
    value: Any = Field(..., description="New value — float, int, bool, or string")
    quality: str = Field("GOOD", description="Quality flag: GOOD, BAD, UNCERTAIN")
    timestamp: Optional[float] = Field(
        None, description="Epoch seconds. Server time used if omitted."
    )


class BatchValueItem(BaseModel):
    tag_id: int
    value: Any
    quality: str = "GOOD"
    timestamp: Optional[float] = None


class BatchValueIn(BaseModel):
    updates: list[BatchValueItem]


class TagOut(BaseModel):
    tag_id: int
    tag_name: str
    description: str
    unit: str
    data_type: str
    min_value: float
    max_value: float
    category: str
    update_interval_sec: int
    is_critical: bool
    current_value: Optional[Any] = None
    quality: Optional[str] = None
    last_updated: Optional[str] = None


# ===========================================================================
# Phase A — auth + health endpoints
# ===========================================================================

@app.get("/api/health")
def health():
    """Liveness check. Returns 200 with basic project info."""
    return {
        "status": "ok",
        "project": PROJECT_ID,
        "database": DATABASE_ID,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/auth/me", response_model=UserProfile)
def me(user: CurrentUser = Depends(get_current_user)):
    """
    Verifies the Firebase ID token and returns the caller's full profile
    from Firestore. The frontend calls this immediately after login.
    Also updates lastLogin.
    """
    user_ref = firebase_db.collection("users").document(user.uid)
    now = datetime.now(timezone.utc)
    user_ref.update({"lastLogin": now})

    doc = user_ref.get().to_dict() or {}
    return UserProfile(
        uid=user.uid,
        name=doc.get("name"),
        email=doc.get("email", user.email),
        role=doc.get("role", "user"),
        clientId=doc.get("clientId"),
        isActive=doc.get("isActive", True),
        createdAt=doc.get("createdAt"),
        updatedAt=doc.get("updatedAt"),
        lastLogin=now,
    )


# ===========================================================================
# Tag endpoints — unchanged from your existing backend
# ===========================================================================

@app.get("/")
def root():
    return {
        "service": "Wind Turbine Tag API",
        "status": "running",
        "project": PROJECT_ID,
        "collection": TAGS_COLLECTION,
        "docs_url": "/docs",
    }


@app.get("/tags", response_model=list[TagOut])
def list_tags(
    category: Optional[str] = Query(None, description="Filter by category"),
    is_critical: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List tag metadata with optional filters."""
    q = db.collection(TAGS_COLLECTION)
    if category:
        q = q.where("category", "==", category)
    if is_critical is not None:
        q = q.where("is_critical", "==", is_critical)

    docs = q.order_by("tag_id").offset(offset).limit(limit).stream()
    return [_doc_to_tag_out(d.to_dict()) for d in docs]


@app.get("/tags/{tag_id}", response_model=TagOut)
def get_tag(tag_id: int):
    snap = db.collection(TAGS_COLLECTION).document(_doc_id(tag_id)).get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tag {tag_id} not found")
    return _doc_to_tag_out(snap.to_dict())


@app.get("/tags/by-name/{tag_name}", response_model=TagOut)
def get_tag_by_name(tag_name: str):
    q = db.collection(TAGS_COLLECTION).where("tag_name", "==", tag_name).limit(1).stream()
    docs = list(q)
    if not docs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tag '{tag_name}' not found")
    return _doc_to_tag_out(docs[0].to_dict())


@app.post("/tags/{tag_id}/value", response_model=TagOut)
def update_tag_value(tag_id: int, payload: TagValueIn):
    """Update the current value of a single tag and record the history point."""
    ref = db.collection(TAGS_COLLECTION).document(_doc_id(tag_id))
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tag {tag_id} not found")

    meta = snap.to_dict()
    value = _validate_against_metadata(meta, payload.value)
    ts = payload.timestamp if payload.timestamp is not None else time.time()
    iso_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    # batched write: update current + append history
    batch = db.batch()
    batch.update(
        ref,
        {
            "current_value": value,
            "quality": payload.quality,
            "last_updated": iso_ts,
            "last_updated_ts": ts,
        },
    )
    hist_ref = ref.collection(HISTORY_SUBCOLLECTION).document()
    batch.set(hist_ref, {"value": value, "quality": payload.quality, "ts": ts, "iso_ts": iso_ts})
    batch.commit()

    meta.update(
        {"current_value": value, "quality": payload.quality, "last_updated": iso_ts}
    )
    return _doc_to_tag_out(meta)


@app.post("/tags/values/batch")
def batch_update_values(payload: BatchValueIn):
    """
    Update many tag values in one call. This is the endpoint a SCADA gateway,
    PLC bridge, or simulator would actually call.

    Firestore batched writes max out at 500 ops per commit; we chunk automatically.
    """
    if not payload.updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No updates provided")

    # Prefetch metadata to validate
    refs = [
        db.collection(TAGS_COLLECTION).document(_doc_id(u.tag_id))
        for u in payload.updates
    ]
    snaps = db.get_all(refs)
    meta_by_id = {s.to_dict()["tag_id"]: s.to_dict() for s in snaps if s.exists}

    results = {"updated": 0, "failed": [], "missing": []}
    CHUNK = 200  # well below 500 op/batch limit (we do 2 ops per tag)
    chunk = db.batch()
    chunk_count = 0

    for u in payload.updates:
        meta = meta_by_id.get(u.tag_id)
        if not meta:
            results["missing"].append(u.tag_id)
            continue
        try:
            value = _validate_against_metadata(meta, u.value)
        except HTTPException as e:
            results["failed"].append({"tag_id": u.tag_id, "error": e.detail})
            continue

        ts = u.timestamp if u.timestamp is not None else time.time()
        iso_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        ref = db.collection(TAGS_COLLECTION).document(_doc_id(u.tag_id))
        chunk.update(
            ref,
            {
                "current_value": value,
                "quality": u.quality,
                "last_updated": iso_ts,
                "last_updated_ts": ts,
            },
        )
        hist_ref = ref.collection(HISTORY_SUBCOLLECTION).document()
        chunk.set(hist_ref, {"value": value, "quality": u.quality, "ts": ts, "iso_ts": iso_ts})
        chunk_count += 1
        results["updated"] += 1

        if chunk_count >= CHUNK:
            chunk.commit()
            chunk = db.batch()
            chunk_count = 0

    if chunk_count > 0:
        chunk.commit()

    return results


@app.get("/tags/{tag_id}/history")
def get_tag_history(tag_id: int, limit: int = Query(100, ge=1, le=1000)):
    ref = db.collection(TAGS_COLLECTION).document(_doc_id(tag_id))
    if not ref.get().exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tag {tag_id} not found")
    docs = (
        ref.collection(HISTORY_SUBCOLLECTION)
        .order_by("ts", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]


@app.get("/categories")
def list_categories():
    """Aggregate count of tags per category (one-time computed, not real-time)."""
    docs = db.collection(TAGS_COLLECTION).stream()
    counts: dict[str, int] = {}
    for d in docs:
        c = d.to_dict().get("category", "Unknown")
        counts[c] = counts.get(c, 0) + 1
    return [{"category": k, "tag_count": v} for k, v in sorted(counts.items())]


@app.get("/tags/critical/active")
def critical_alarms_active():
    """Return any critical tags whose current value indicates an alarm (BOOL=True or out of range)."""
    docs = (
        db.collection(TAGS_COLLECTION).where("is_critical", "==", True).stream()
    )
    active = []
    for d in docs:
        t = d.to_dict()
        v = t.get("current_value")
        if v is None:
            continue
        if t["data_type"] == "BOOL" and bool(v):
            active.append(t)
        elif t["data_type"] in ("INT", "FLOAT"):
            if v < t["min_value"] or v > t["max_value"]:
                active.append(t)
    return active


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_against_metadata(meta: dict, value: Any) -> Any:
    """Coerce + range-check a value against the tag's metadata."""
    dtype = meta.get("data_type", "FLOAT")
    try:
        if dtype == "INT":
            value = int(value)
        elif dtype == "FLOAT":
            value = float(value)
        elif dtype == "BOOL":
            if isinstance(value, str):
                value = value.lower() in ("1", "true", "yes", "on")
            value = bool(value)
        elif dtype == "STRING":
            value = str(value)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Value '{value}' cannot be coerced to {dtype}: {e}",
        )

    # numeric range check — skip for booleans and strings
    if dtype in ("INT", "FLOAT"):
        vmin, vmax = meta.get("min_value"), meta.get("max_value")
        if vmin is not None and vmax is not None and not (vmin <= value <= vmax):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Value {value} is out of range [{vmin}, {vmax}] for {meta['tag_name']}",
            )
    return value


def _doc_id(tag_id: int) -> str:
    """Use zero-padded tag IDs for natural sorting in Firestore console."""
    return f"tag_{int(tag_id):04d}"


def _doc_to_tag_out(d: dict) -> TagOut:
    return TagOut(
        tag_id=d["tag_id"],
        tag_name=d["tag_name"],
        description=d["description"],
        unit=d.get("unit", ""),
        data_type=d.get("data_type", "FLOAT"),
        min_value=float(d.get("min_value", 0)),
        max_value=float(d.get("max_value", 0)),
        category=d.get("category", ""),
        update_interval_sec=int(d.get("update_interval_sec", 60)),
        is_critical=bool(d.get("is_critical", False)),
        current_value=d.get("current_value"),
        quality=d.get("quality"),
        last_updated=d.get("last_updated"),
    )