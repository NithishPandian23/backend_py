"""
Simulator — pushes plausible random values for all 500 tags through the
batch-update endpoint of your running API.

Use this to test your backend end-to-end without a real turbine.

Usage:
    # in one terminal:
    uvicorn main:app --reload --port 8080

    # in another:
    python simulator.py --url http://localhost:8080 --interval 5
"""
import argparse
import json
import random
import time

import requests


def generate_value(tag: dict):
    dtype = tag["data_type"]
    if dtype == "BOOL":
        # Critical bool alarms stay False most of the time; non-critical 50/50.
        if tag.get("is_critical"):
            return random.random() < 0.02
        return random.random() < 0.5
    if dtype == "INT":
        return random.randint(int(tag["min_value"]), int(tag["max_value"]))
    if dtype == "FLOAT":
        lo, hi = tag["min_value"], tag["max_value"]
        # Bias around middle of range so most values are "normal"
        mid = (lo + hi) / 2
        span = (hi - lo) / 4
        v = random.gauss(mid, span)
        return round(max(lo, min(hi, v)), 3)
    if dtype == "STRING":
        return f"value_{random.randint(1, 100)}"
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000", help="Base URL of the API")
    p.add_argument("--interval", type=float, default=5.0, help="Seconds between updates")
    p.add_argument("--tags-file", default="wind_turbine_tags.json")
    p.add_argument("--once", action="store_true", help="Run a single update then exit")
    args = p.parse_args()

    with open(args.tags_file) as f:
        tags = json.load(f)
    print(f"Loaded {len(tags)} tag definitions.")

    endpoint = f"{args.url}/tags/values/batch"
    print(f"Pushing values to {endpoint} every {args.interval}s. Ctrl+C to stop.")

    try:
        while True:
            updates = [
                {
                    "tag_id": t["tag_id"],
                    "value": generate_value(t),
                    "quality": "GOOD",
                }
                for t in tags
            ]
            t0 = time.time()
            r = requests.post(endpoint, json={"updates": updates}, timeout=120)
            elapsed = time.time() - t0
            if r.ok:
                res = r.json()
                print(
                    f"  pushed {res['updated']}/{len(updates)} in {elapsed:.2f}s "
                    f"(failed={len(res['failed'])}, missing={len(res['missing'])})"
                )
            else:
                print(f"  ERROR {r.status_code}: {r.text[:300]}")
            if args.once:
                break
            time.sleep(max(0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()