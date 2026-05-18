"""
simulator.py — Live SCADA tag value simulator.

Pushes plausible values for every tag in the Firestore collection. Designed
to mimic what a real SCADA gateway / PLC bridge would do.

Behavior:
  * Polls GET /tags on startup (paginated) to discover the live tag list
  * Generates plausible values per tag:
      - BOOL critical tags: 98% inactive (false). 2% alarm pulses.
      - BOOL non-critical tags: ~50/50 (running indicators)
      - FLOAT: unit-aware ranges (RPM 10-20, temps 30-90°C, etc.)
      - INT: same as FLOAT but integer
  * Sends them to POST /tags/values/batch every INTERVAL seconds
  * Refreshes the tag list every REFRESH_EVERY ticks so newly added tags
    show up automatically without restarting

Run:
    python simulator.py
    python simulator.py --interval 2
    python simulator.py --dry-run         # don't actually POST, just print
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from typing import Any

import requests

# ----------------------------------------------------------------------- config

API = "http://127.0.0.1:8000"
INTERVAL = 5            # seconds between batch pushes
REFRESH_EVERY = 60      # refresh the tag list every N ticks
PAGE_SIZE = 1000        # backend caps limit at 1000; we paginate beyond that


def fetch_all_tags(api: str) -> list[dict]:
    """
    Fetch every tag from GET /tags by paginating until the server returns
    fewer than PAGE_SIZE tags. Works regardless of whether you have 50 tags
    or 50,000.
    """
    tags: list[dict] = []
    offset = 0
    while True:
        r = requests.get(
            f"{api}/tags",
            params={"limit": PAGE_SIZE, "offset": offset},
            timeout=10,
        )
        r.raise_for_status()
        page = r.json()
        tags.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return tags


def generate_value(tag: dict) -> Any:
    """Generate a plausible new value for one tag based on its metadata."""
    dtype = tag.get("data_type", "FLOAT")
    is_critical = tag.get("is_critical", False)

    if dtype == "BOOL":
        # Critical bool tags are usually alarms — keep them quiet most of the time
        if is_critical:
            return random.random() < 0.02   # 2% chance of pulse
        # Non-critical bools are running/status indicators — bias toward "on"
        return random.random() < 0.85

    vmin = float(tag.get("min_value") or 0)
    vmax = float(tag.get("max_value") or 100)
    # Bias the value toward the middle 60% of the range so we don't constantly
    # trigger range-violation alarms on every push
    span = vmax - vmin
    lo = vmin + span * 0.20
    hi = vmax - span * 0.20
    value = random.uniform(lo, hi)

    if dtype == "INT":
        return int(round(value))
    return round(value, 2)


def build_batch(tags: list[dict]) -> dict:
    """
    Build the POST body. The backend's BatchValueIn schema expects:
        {"updates": [{"tag_id": ..., "value": ..., "quality": "GOOD"}, ...]}
    NOT {"values": [...]} (which is what the old simulator was sending).
    """
    return {
        "updates": [
            {
                "tag_id": int(t["tag_id"]),
                "value": generate_value(t),
                "quality": "GOOD",
            }
            for t in tags
        ],
    }


def push_batch(api: str, batch: dict) -> dict:
    r = requests.post(f"{api}/tags/values/batch", json=batch, timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api",      default=API,      help="Base URL of the backend")
    p.add_argument("--interval", type=float, default=INTERVAL, help="Seconds between pushes")
    p.add_argument("--dry-run",  action="store_true", help="Don't POST, just print")
    args = p.parse_args()

    print("=" * 60)
    print("  REAL-TAG SIMULATOR")
    print("=" * 60)
    print(f"  API:      {args.api}")
    print(f"  Interval: {args.interval}s")
    print(f"  Dry run:  {args.dry_run}")
    print("  Press Ctrl+C to stop.")
    print()

    tags: list[dict] = []
    tick = 0

    while True:
        # (Re)fetch the tag list on startup and every REFRESH_EVERY ticks
        if not tags or tick % REFRESH_EVERY == 0:
            try:
                tags = fetch_all_tags(args.api)
                print(f"  \u21bb refreshed tag list: {len(tags)} tags")
            except Exception as e:
                print(f"  ! couldn't fetch tags: {e}")
                time.sleep(args.interval)
                continue

        if not tags:
            print("  ! no tags found in backend — sleeping")
            time.sleep(args.interval)
            continue

        # Build and push
        batch = build_batch(tags)
        if args.dry_run:
            print(f"  [dry-run] would push {len(batch['updates'])} values; sample:")
            for sample in batch["updates"][:3]:
                print(f"            {sample}")
        else:
            try:
                resp = push_batch(args.api, batch)
                print(
                    f"  \u2713 pushed {resp.get('updated', 0)} values"
                    f" (missing: {len(resp.get('missing', []))},"
                    f" failed: {len(resp.get('failed', []))})"
                )
            except requests.HTTPError as e:
                # Show the actual server detail so debugging is easy
                body = e.response.text if e.response is not None else ""
                print(f"  ! batch update failed: HTTP {e.response.status_code if e.response else '?'} {body[:300]}")
            except Exception as e:
                print(f"  ! batch update failed: {e}")

        tick += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(0)