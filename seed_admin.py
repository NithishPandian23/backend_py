"""
seed_admin.py — one-time script to create the first admin user.

Run this ONCE after setting up Firebase. It creates a Firebase Auth user
and a corresponding Firestore profile document at users/{uid}.

After this seed, all other users must be created via the admin UI
(or POST /api/users in Phase B).

Usage:
    $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\\keys\\wind-turbine-demo.json"
    $env:GCP_PROJECT_ID = "wind-turbine-demo-123456"
    python seed_admin.py

If the admin already exists in Firebase Auth, this script will just
ensure the Firestore profile is up to date (idempotent).
"""
import os
import sys
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, auth as fb_auth
from google.cloud import firestore as gcf_firestore

# Customize these for your environment
ADMIN_EMAIL    = os.environ.get("SEED_ADMIN_EMAIL", "admin@vayona.energy")
ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "Admin@2025")
ADMIN_NAME     = os.environ.get("SEED_ADMIN_NAME", "Vayona Administrator")

PROJECT_ID  = os.environ.get("GCP_PROJECT_ID", "wind-turbine-demo-123456")
DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "wind-turbine-db")


def main():
    print(f"Initializing Firebase Admin for project: {PROJECT_ID}")
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred, {"projectId": PROJECT_ID})

    # NOTE: firebase_admin's firestore.client() wrapper doesn't accept a
    # database argument — it always returns the (default) database.
    # Use google.cloud.firestore.Client() directly to bind to our named DB.
    db = gcf_firestore.Client(project=PROJECT_ID, database=DATABASE_ID)

    # ------------------------------------------------------------------
    # 1) Create or update Firebase Auth user
    # ------------------------------------------------------------------
    try:
        user = fb_auth.get_user_by_email(ADMIN_EMAIL)
        print(f"  ✓ Firebase Auth user already exists: {user.uid}")
        # Reset password to the configured value so the seed is repeatable
        fb_auth.update_user(
            user.uid,
            password=ADMIN_PASSWORD,
            email_verified=True,
            display_name=ADMIN_NAME,
        )
        print(f"  ✓ Password reset to configured value")
    except fb_auth.UserNotFoundError:
        user = fb_auth.create_user(
            email=ADMIN_EMAIL,
            password=ADMIN_PASSWORD,
            display_name=ADMIN_NAME,
            email_verified=True,
        )
        print(f"  ✓ Created Firebase Auth user: {user.uid}")

    uid = user.uid

    # ------------------------------------------------------------------
    # 2) Upsert Firestore profile at users/{uid}
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    user_ref = db.collection("users").document(uid)
    existing = user_ref.get()

    profile = {
        "uid": uid,
        "name": ADMIN_NAME,
        "email": ADMIN_EMAIL,
        "role": "admin",
        "clientId": None,
        "isActive": True,
        "updatedAt": now,
    }

    if existing.exists:
        user_ref.update(profile)
        print(f"  ✓ Updated Firestore profile at users/{uid}")
    else:
        profile["createdAt"] = now
        user_ref.set(profile)
        print(f"  ✓ Created Firestore profile at users/{uid}")

    print()
    print("=" * 60)
    print("  ADMIN SEEDED SUCCESSFULLY")
    print("=" * 60)
    print(f"  Email:    {ADMIN_EMAIL}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"  UID:      {uid}")
    print(f"  Role:     admin")
    print("=" * 60)
    print()
    print("  You can now log in at http://localhost:3000")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}", file=sys.stderr)
        print("\nCheck that:", file=sys.stderr)
        print("  1. GOOGLE_APPLICATION_CREDENTIALS points to a valid service account key", file=sys.stderr)
        print("  2. GCP_PROJECT_ID is set to your Firebase project ID", file=sys.stderr)
        print("  3. Firebase Authentication is enabled in the Firebase Console", file=sys.stderr)
        print("  4. Email/Password sign-in is enabled as a provider", file=sys.stderr)
        sys.exit(1)