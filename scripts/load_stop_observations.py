#!/usr/bin/env python3
"""
Load (or reload) just stop_observations.txt for an already-loaded
feed_version -- reuses the exact same parsing logic as load_gtfs.py.

Use this instead of a full scripts/load_gtfs.py run when every other GTFS
table (agency, routes, trips, stop_times, ...) is already correctly loaded
for a given feed_version and only stop_observations needs (re)loading --
e.g. right after fixing a stop_observations-specific bug, without redoing
the full multi-million-row stop_times load. The resulting database is
identical to what a full reload would produce, since stop_observations
sits at the end of the load order and nothing else depends on it.

Usage
-----
# find the feed_version_id first if you don't already know it:
#   docker compose exec -T db psql -U transit -d bay_transit \
#       -c "select * from feed_version;"

python scripts/load_stop_observations.py --path data/raw/RG_2025-08_so --feed-version-id 1
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from load_gtfs import load_stop_observations, read_txt  # reuse the same parsing logic, no duplication

load_dotenv()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True,
                    help="Path to extracted GTFS directory containing stop_observations.txt")
    ap.add_argument("--feed-version-id", type=int, required=True,
                    help="feed_version_id to attach these rows to (must already exist -- "
                         "see the docstring above for how to find it)")
    ap.add_argument("--replace", action="store_true",
                    help="Delete any existing stop_observations rows for this feed_version_id "
                         "first (safe to always pass; a no-op if there's nothing to delete)")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("Set DATABASE_URL in your .env first")
    engine = create_engine(db_url)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT source, label FROM feed_version WHERE feed_version_id = :fv"),
            {"fv": args.feed_version_id},
        ).fetchone()
    if not row:
        sys.exit(f"feed_version_id={args.feed_version_id} does not exist. Load the rest of the feed "
                  f"first with scripts/load_gtfs.py, or check ids with: select * from feed_version;")
    print(f"Target feed_version_id={args.feed_version_id} (source={row[0]}, label={row[1]})")

    obs_path = Path(args.path) / "stop_observations.txt"
    if not obs_path.exists():
        sys.exit(f"{obs_path} not found")

    if args.replace:
        with engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM stop_observations WHERE feed_version_id = :fv"),
                {"fv": args.feed_version_id},
            )
            print(f"Deleted {result.rowcount} existing stop_observations rows for this feed_version_id")

    load_stop_observations(engine, args.feed_version_id, read_txt(obs_path))
    print("Done.")


if __name__ == "__main__":
    main()
