"""
seed_fleet.py — One-time seed for Phase C.

Populates Firestore with:
  * 4 clients (Vayona Energy, Adani Green, Reliance Power, Tata Power Renewables)
  * 23 turbines spread across Indian wind corridors with proper client_id assignments

Idempotent: if a client/turbine already exists with the same ID, it's left alone.
Re-running this script after admin has edited names/assignments is SAFE — it
only creates missing records, never overwrites existing ones.

Usage:
    $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\\keys\\wind-turbine-demo.json"
    $env:GCP_PROJECT_ID = "wind-turbine-demo-123456"
    python seed_fleet.py

If you want to RESET everything (wipe + reseed), pass --reset:
    python seed_fleet.py --reset
"""
import argparse
import os
import sys
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials
from google.cloud import firestore as gcf_firestore

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "wind-turbine-demo-123456")
DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "wind-turbine-db")

# ---------------------------------------------------------------------------
# Seed data — matches your existing client-side mock fleet
# ---------------------------------------------------------------------------

CLIENTS = [
    {
        "id": "c_vayona",
        "name": "Vayona Energy",
        "contact_email": "ops@vayona.energy",
        "logo_url": None,
    },
    {
        "id": "c_adani",
        "name": "Adani Green Energy",
        "contact_email": "scada@adani-green.example",
        "logo_url": None,
    },
    {
        "id": "c_reliance",
        "name": "Reliance Power",
        "contact_email": "wind-ops@reliance-power.example",
        "logo_url": None,
    },
    {
        "id": "c_tata",
        "name": "Tata Power Renewables",
        "contact_email": "renewables@tatapower.example",
        "logo_url": None,
    },
]

# 23 turbines across the four major Indian wind-farm regions
TURBINES = [
    # ─── Aralvaimozhi Pass (Tamil Nadu) — Vayona ─────────────────────────
    {"id": "t_aralvaimozhi_01", "name": "Aralvaimozhi-01", "serial": "AV-2024-001",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-01-15",
     "location": {"site": "Aralvaimozhi Pass", "state": "Tamil Nadu", "lat": 8.2247, "lng": 77.3953},
     "client_id": "c_vayona"},
    {"id": "t_aralvaimozhi_02", "name": "Aralvaimozhi-02", "serial": "AV-2024-002",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-01-18",
     "location": {"site": "Aralvaimozhi Pass", "state": "Tamil Nadu", "lat": 8.2289, "lng": 77.4012},
     "client_id": "c_vayona"},
    # ─── Chitradurga (Karnataka) — Vayona ───────────────────────────────
    {"id": "t_chitradurga_01", "name": "Chitradurga-01", "serial": "CT-2023-014",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-08-22",
     "location": {"site": "Chitradurga", "state": "Karnataka", "lat": 14.2226, "lng": 76.4006},
     "client_id": "c_vayona"},
    # ─── Gadag (Karnataka) — Vayona ─────────────────────────────────────
    {"id": "t_gadag_01", "name": "Gadag-01", "serial": "GD-2024-007",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2024-03-10",
     "location": {"site": "Gadag", "state": "Karnataka", "lat": 15.4191, "lng": 75.6359},
     "client_id": "c_vayona"},
    # ─── Satara (Maharashtra) — Vayona ──────────────────────────────────
    {"id": "t_satara_01", "name": "Satara-01", "serial": "ST-2023-021",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2023-11-05",
     "location": {"site": "Satara", "state": "Maharashtra", "lat": 17.6805, "lng": 73.9913},
     "client_id": "c_vayona"},

    # ─── Jamnagar (Gujarat) — Adani ─────────────────────────────────────
    {"id": "t_jamnagar_01", "name": "Jamnagar-01", "serial": "JM-2023-008",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2023-06-14",
     "location": {"site": "Jamnagar", "state": "Gujarat", "lat": 22.4707, "lng": 70.0577},
     "client_id": "c_adani"},
    # ─── Jaisalmer (Rajasthan) — Adani ──────────────────────────────────
    {"id": "t_jaisalmer_01", "name": "Jaisalmer-01", "serial": "JS-2024-003",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-02-20",
     "location": {"site": "Jaisalmer", "state": "Rajasthan", "lat": 26.9157, "lng": 70.9083},
     "client_id": "c_adani"},
    {"id": "t_jaisalmer_02", "name": "Jaisalmer-02", "serial": "JS-2024-004",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-02-22",
     "location": {"site": "Jaisalmer", "state": "Rajasthan", "lat": 26.9211, "lng": 70.9143},
     "client_id": "c_adani"},

    # ─── Muppandal (Tamil Nadu) — Reliance ──────────────────────────────
    {"id": "t_muppandal_01", "name": "Muppandal-01", "serial": "MP-2023-014",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-09-12",
     "location": {"site": "Muppandal", "state": "Tamil Nadu", "lat": 8.2520, "lng": 77.5400},
     "client_id": "c_reliance"},
    {"id": "t_muppandal_02", "name": "Muppandal-02", "serial": "MP-2023-015",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-09-14",
     "location": {"site": "Muppandal", "state": "Tamil Nadu", "lat": 8.2566, "lng": 77.5443},
     "client_id": "c_reliance"},
    # ─── Tirunelveli (Tamil Nadu) — Reliance ────────────────────────────
    {"id": "t_tirunelveli_01", "name": "Tirunelveli-01", "serial": "TN-2023-022",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2023-10-08",
     "location": {"site": "Tirunelveli", "state": "Tamil Nadu", "lat": 8.7139, "lng": 77.7567},
     "client_id": "c_reliance"},
    # ─── Anantapur (Andhra Pradesh) — Reliance ──────────────────────────
    {"id": "t_anantapur_01", "name": "Anantapur-01", "serial": "AN-2024-005",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-04-02",
     "location": {"site": "Anantapur", "state": "Andhra Pradesh", "lat": 14.6819, "lng": 77.6006},
     "client_id": "c_reliance"},
    {"id": "t_anantapur_02", "name": "Anantapur-02", "serial": "AN-2024-006",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-04-04",
     "location": {"site": "Anantapur", "state": "Andhra Pradesh", "lat": 14.6892, "lng": 77.6048},
     "client_id": "c_reliance"},

    # ─── Kurnool (Andhra Pradesh) — Tata ────────────────────────────────
    {"id": "t_kurnool_01", "name": "Kurnool-01", "serial": "KN-2023-018",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-07-20",
     "location": {"site": "Kurnool", "state": "Andhra Pradesh", "lat": 15.8281, "lng": 78.0373},
     "client_id": "c_tata"},
    {"id": "t_kurnool_02", "name": "Kurnool-02", "serial": "KN-2023-019",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-07-22",
     "location": {"site": "Kurnool", "state": "Andhra Pradesh", "lat": 15.8312, "lng": 78.0421},
     "client_id": "c_tata"},
    # ─── Pavagada (Karnataka) — Tata ────────────────────────────────────
    {"id": "t_pavagada_01", "name": "Pavagada-01", "serial": "PV-2024-009",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-03-25",
     "location": {"site": "Pavagada", "state": "Karnataka", "lat": 14.0964, "lng": 77.2814},
     "client_id": "c_tata"},
    {"id": "t_pavagada_02", "name": "Pavagada-02", "serial": "PV-2024-010",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-03-28",
     "location": {"site": "Pavagada", "state": "Karnataka", "lat": 14.0998, "lng": 77.2856},
     "client_id": "c_tata"},
    # ─── Villupuram (Tamil Nadu) — Tata ─────────────────────────────────
    {"id": "t_villupuram_01", "name": "Villupuram-01", "serial": "VP-2024-012",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-05-10",
     "location": {"site": "Villupuram", "state": "Tamil Nadu", "lat": 11.9401, "lng": 79.4861},
     "client_id": "c_tata"},

    # ─── Some unassigned turbines (no client_id) for admin to allocate ─
    {"id": "t_dhule_01", "name": "Dhule-01", "serial": "DH-2024-015",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2024-06-01",
     "location": {"site": "Dhule", "state": "Maharashtra", "lat": 20.9042, "lng": 74.7749},
     "client_id": None},
    {"id": "t_kayathar_01", "name": "Kayathar-01", "serial": "KY-2024-018",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-07-15",
     "location": {"site": "Kayathar", "state": "Tamil Nadu", "lat": 8.9437, "lng": 77.7674},
     "client_id": None},
    {"id": "t_palladam_01", "name": "Palladam-01", "serial": "PD-2023-025",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2023-12-08",
     "location": {"site": "Palladam", "state": "Tamil Nadu", "lat": 10.9921, "lng": 77.2779},
     "client_id": None},
    {"id": "t_kanyakumari_01", "name": "Kanyakumari-01", "serial": "KK-2024-020",
     "model": "Vayona V126-3.0", "rated_power_kw": 3000, "hub_height_m": 90, "rotor_diameter_m": 126,
     "commissioned": "2024-08-12",
     "location": {"site": "Kanyakumari", "state": "Tamil Nadu", "lat": 8.0883, "lng": 77.5385},
     "client_id": None},
    {"id": "t_lambasingi_01", "name": "Lambasingi-01", "serial": "LB-2024-022",
     "model": "Vayona V120-2.5", "rated_power_kw": 2500, "hub_height_m": 80, "rotor_diameter_m": 120,
     "commissioned": "2024-09-05",
     "location": {"site": "Lambasingi", "state": "Andhra Pradesh", "lat": 17.7833, "lng": 82.6167},
     "client_id": None},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Wipe all clients and turbines before seeding")
    args = parser.parse_args()

    print(f"Initializing Firebase Admin for project: {PROJECT_ID}")
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred, {"projectId": PROJECT_ID})

    db = gcf_firestore.Client(project=PROJECT_ID, database=DATABASE_ID)
    now = datetime.now(timezone.utc)

    # --------------------------------------------------------------- reset
    if args.reset:
        print("⚠ RESETTING clients + turbines collections...")
        for col in ("clients", "turbines"):
            docs = list(db.collection(col).stream())
            for d in docs:
                d.reference.delete()
            print(f"  deleted {len(docs)} docs from {col}")

    # --------------------------------------------------------------- clients
    print("\nSeeding clients...")
    for c in CLIENTS:
        ref = db.collection("clients").document(c["id"])
        if ref.get().exists and not args.reset:
            print(f"  · skip (exists): {c['name']}")
            continue
        # Compute turbine_ids for this client by scanning the TURBINES list
        turbine_ids = [t["id"] for t in TURBINES if t["client_id"] == c["id"]]
        ref.set({
            "name": c["name"],
            "contact_email": c["contact_email"],
            "logo_url": c["logo_url"],
            "turbine_ids": turbine_ids,
            "created_at": now,
            "updated_at": now,
        })
        print(f"  ✓ {c['name']} ({len(turbine_ids)} turbines)")

    # --------------------------------------------------------------- turbines
    print("\nSeeding turbines...")
    created = 0
    skipped = 0
    for t in TURBINES:
        ref = db.collection("turbines").document(t["id"])
        if ref.get().exists and not args.reset:
            skipped += 1
            continue
        ref.set({
            "name": t["name"],
            "serial": t["serial"],
            "model": t["model"],
            "rated_power_kw": t["rated_power_kw"],
            "hub_height_m": t["hub_height_m"],
            "rotor_diameter_m": t["rotor_diameter_m"],
            "commissioned": t["commissioned"],
            "location": t["location"],
            "client_id": t["client_id"],
            "decommissioned": False,
            "created_at": now,
            "updated_at": now,
        })
        created += 1
    print(f"  created: {created}, skipped (exists): {skipped}")

    print("\n" + "=" * 60)
    print("  FLEET SEED COMPLETE")
    print("=" * 60)
    print(f"  Clients:  {len(CLIENTS)}")
    print(f"  Turbines: {len(TURBINES)} ({sum(1 for t in TURBINES if t['client_id'])} assigned, "
          f"{sum(1 for t in TURBINES if not t['client_id'])} unassigned)")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}", file=sys.stderr)
        sys.exit(1)