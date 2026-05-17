"""
One-time script: upload the 500 wind turbine tag definitions to Firestore.

Run AFTER you have:
  1. Created a Firestore database in your GCP project
  2. Generated wind_turbine_tags.json by running generate_tags.py
  3. Set GOOGLE_APPLICATION_CREDENTIALS to a service-account key file
  4. Set GCP_PROJECT_ID

Usage:
    python upload_tags.py
"""
import json
import os
import sys

from google.cloud import firestore

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
if not PROJECT_ID:
    sys.exit("Set GCP_PROJECT_ID environment variable first.")

TAGS_COLLECTION = "wind_turbine_tags"
JSON_FILE = "wind_turbine_tags.json"


def main():
    with open(JSON_FILE) as f:
        tags = json.load(f)

    print(f"Loaded {len(tags)} tags from {JSON_FILE}")
    db = firestore.Client(project=PROJECT_ID,
    database="wind-turbine-db" )

    # Firestore batched writes: max 500 ops per commit.
    BATCH_LIMIT = 400
    batch = db.batch()
    count = 0
    total = 0

    for t in tags:
        doc_id = f"tag_{int(t['tag_id']):04d}"
        ref = db.collection(TAGS_COLLECTION).document(doc_id)
        # Initialise current_value as None so the field exists.
        payload = {
            **t,
            "current_value": None,
            "quality": None,
            "last_updated": None,
            "last_updated_ts": None,
        }
        batch.set(ref, payload)
        count += 1
        total += 1

        if count >= BATCH_LIMIT:
            batch.commit()
            print(f"  Committed {total}/{len(tags)}")
            batch = db.batch()
            count = 0

    if count > 0:
        batch.commit()
        print(f"  Committed {total}/{len(tags)}")

    print(f"\n✓ Uploaded {total} tag definitions to '{TAGS_COLLECTION}' in project {PROJECT_ID}")


if __name__ == "__main__":
    main()