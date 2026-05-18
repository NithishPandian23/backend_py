"""
upload_tags.py
==============
Uploads the real SCADA tags to Firestore.

This REPLACES the previous 500 generated tags. It:
  1. Reads wind_turbine_tags.json (produced by import_real_tags.py).
  2. Deletes ALL documents currently in the `tags` Firestore collection.
  3. Uploads the new tags in batches of 400 (Firestore batch write limit is 500).
  4. Document IDs follow the format: tag_C20_0001, tag_GS25_0001, ...
     so tags from different turbine models can't collide.

Run:
    $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\\keys\\wind-turbine-demo.json"
    $env:GCP_PROJECT_ID = "wind-turbine-demo-123456"
    python upload_tags.py

Optional flags:
    --dry-run     Print what would happen without writing to Firestore
    --no-purge    Don't delete existing tags; just upsert
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from google.cloud import firestore
except ImportError:
    print("ERROR: google-cloud-firestore not installed.", file=sys.stderr)
    print("Run: pip install google-cloud-firestore", file=sys.stderr)
    sys.exit(1)


PROJECT_ID  = os.environ.get("GCP_PROJECT_ID", "wind-turbine-demo-123456")
DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "wind-turbine-db")
COLLECTION  = "tags"
INPUT_JSON  = Path(__file__).parent / "wind_turbine_tags.json"

# Firestore batch writes are limited to 500 operations. We use 400 to leave headroom.
BATCH_SIZE = 400


def doc_id_for(tag: dict) -> str:
    """Build a stable document ID: tag_<MODEL_SHORT>_<NNNN>."""
    model = tag.get("turbine_model", "UNK")
    short = "C20" if "C20" in model else "GS25" if "GS25" in model else "UNK"
    return f"tag_{short}_{tag['tag_id']:04d}"


def purge_collection(db: firestore.Client, dry_run: bool) -> int:
    """Delete every document in the tags collection. Returns count deleted."""
    print(f"\nPurging existing '{COLLECTION}' collection …")
    coll = db.collection(COLLECTION)
    total = 0
    while True:
        # Pull a batch of doc IDs
        docs = list(coll.limit(BATCH_SIZE).stream())
        if not docs:
            break
        if dry_run:
            total += len(docs)
            print(f"  [dry-run] would delete {len(docs)} docs (running total {total})")
            # In dry-run mode we can't continue forever; stop after first batch
            break
        batch = db.batch()
        for d in docs:
            batch.delete(d.reference)
        batch.commit()
        total += len(docs)
        print(f"  Deleted {len(docs)} docs (running total {total})")
    print(f"  → {total} documents deleted")
    return total


def upload_tags(db: firestore.Client, tags: list[dict], dry_run: bool) -> int:
    """Upload all tags in batched writes."""
    print(f"\nUploading {len(tags)} tags …")
    now = datetime.now(timezone.utc)

    written = 0
    for i in range(0, len(tags), BATCH_SIZE):
        chunk = tags[i:i + BATCH_SIZE]
        if dry_run:
            print(f"  [dry-run] would write batch of {len(chunk)} tags  "
                  f"(IDs: {doc_id_for(chunk[0])} … {doc_id_for(chunk[-1])})")
            written += len(chunk)
            continue

        batch = db.batch()
        for t in chunk:
            doc_ref = db.collection(COLLECTION).document(doc_id_for(t))
            # Add server-side timestamps; preserve the original tag data
            payload = dict(t)
            payload["last_updated"] = now
            # Ensure types are JSON-safe for Firestore
            payload["min_value"] = float(payload["min_value"])
            payload["max_value"] = float(payload["max_value"])
            payload["update_interval_sec"] = float(payload["update_interval_sec"])
            payload["is_critical"] = bool(payload["is_critical"])
            payload["tag_id"] = int(payload["tag_id"])
            batch.set(doc_ref, payload)
        batch.commit()
        written += len(chunk)
        print(f"  Batch {i // BATCH_SIZE + 1}: wrote {len(chunk)} tags  (running total {written})")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload real SCADA tags to Firestore.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--no-purge", action="store_true", help="Skip deleting existing tags")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  UPLOAD REAL TAGS TO FIRESTORE")
    print(f"{'='*60}")
    print(f"  Project:  {PROJECT_ID}")
    print(f"  Database: {DATABASE_ID}")
    print(f"  Mode:     {'DRY-RUN (no writes)' if args.dry_run else 'LIVE'}")

    if not INPUT_JSON.exists():
        print(f"\nERROR: {INPUT_JSON.name} not found.")
        print(f"Run `python import_real_tags.py` first.")
        return 1

    print(f"\nLoading {INPUT_JSON.name} …")
    with open(INPUT_JSON, encoding="utf-8") as f:
        tags = json.load(f)
    print(f"  → {len(tags)} tags loaded")

    print(f"\nConnecting to Firestore …")
    db = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

    if not args.no_purge:
        purge_collection(db, args.dry_run)

    written = upload_tags(db, tags, args.dry_run)

    print(f"\n{'='*60}")
    if args.dry_run:
        print(f"  DRY-RUN COMPLETE — {written} tags would be uploaded")
    else:
        print(f"  ✓ UPLOAD COMPLETE — {written} tags in Firestore")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ ERROR: {e}", file=sys.stderr)
        sys.exit(1)