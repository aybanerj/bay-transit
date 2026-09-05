#!/usr/bin/env python3
"""
Load an extracted GTFS (+ optional stop_observations.txt) directory into
Postgres, tagged with a feed_version row so multiple monthly snapshots can
coexist.

Usage
-----
python scripts/load_gtfs.py --path data/raw/RG_2025-08_so --source RG --label 2025-08

If --source/--label are omitted they're inferred from the directory name
(expects "<source>_<label>[_so]").

For first-time setup, run with --init-schema to apply sql/schema/*.sql
before loading (not needed if you started the DB via `docker compose up`,
since that mounts sql/schema as /docker-entrypoint-initdb.d).
"""
import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# Maps GTFS filename -> (table name, columns to keep in that exact order).
# Only lists the subset of official GTFS columns we actually use; extra
# columns present in a real feed are simply ignored (not an error).
TABLE_SPECS = {
    "agency.txt": ("agency", ["agency_id", "agency_name", "agency_url",
                               "agency_timezone", "agency_lang", "agency_phone"]),
    "routes.txt": ("routes", ["route_id", "agency_id", "route_short_name",
                               "route_long_name", "route_desc", "route_type",
                               "route_color", "route_text_color"]),
    "stops.txt": ("stops", ["stop_id", "stop_code", "stop_name", "stop_desc",
                             "stop_lat", "stop_lon", "zone_id", "location_type",
                             "parent_station", "wheelchair_boarding"]),
    "calendar.txt": ("calendar", ["service_id", "monday", "tuesday", "wednesday",
                                   "thursday", "friday", "saturday", "sunday",
                                   "start_date", "end_date"]),
    "calendar_dates.txt": ("calendar_dates", ["service_id", "date", "exception_type"]),
    "trips.txt": ("trips", ["trip_id", "route_id", "service_id", "trip_headsign",
                             "trip_short_name", "direction_id", "block_id",
                             "shape_id", "wheelchair_accessible", "bikes_allowed"]),
    "shapes.txt": ("shapes", ["shape_id", "shape_pt_sequence", "shape_pt_lat",
                               "shape_pt_lon", "shape_dist_traveled"]),
    "transfers.txt": ("transfers", ["from_stop_id", "to_stop_id",
                                     "transfer_type", "min_transfer_time"]),
}
# frequencies.txt is handled by load_frequencies() below, not the generic
# loader, because start_time/end_time need HH:MM:SS -> seconds conversion
# (same reasoning as stop_times) and the schema column names differ
# (start_sec/end_sec) from the raw GTFS names.

TIME_RE = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)$")


def time_to_seconds(val):
    """'25:03:00' -> 90180. GTFS times can exceed 24:00:00 for
    trips that run past midnight relative to the service day."""
    if pd.isna(val) or val == "":
        return None
    m = TIME_RE.match(str(val).strip())
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def read_txt(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def get_or_create_feed_version(engine, source: str, label: str, has_obs: bool) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT feed_version_id FROM feed_version WHERE source=:s AND label=:l"),
            {"s": source, "l": label},
        ).fetchone()
        if row:
            print(f"feed_version already exists (id={row[0]}); rows will be re-inserted "
                  f"only if not already present is NOT guaranteed -- delete it first to reload cleanly.")
            return row[0]
        row = conn.execute(
            text("""INSERT INTO feed_version (source, label, has_stop_observations)
                     VALUES (:s, :l, :o) RETURNING feed_version_id"""),
            {"s": source, "l": label, "o": has_obs},
        ).fetchone()
        return row[0]


def load_simple_table(engine, feed_version_id: int, df: pd.DataFrame, table: str, cols: list[str]):
    keep = [c for c in cols if c in df.columns]
    out = df[keep].copy()
    out.insert(0, "feed_version_id", feed_version_id)
    out.to_sql(table, engine, if_exists="append", index=False, method="multi", chunksize=5000)
    print(f"  loaded {len(out):>8} rows -> {table}")


def load_stop_times(engine, feed_version_id: int, df: pd.DataFrame):
    out = pd.DataFrame({
        "feed_version_id": feed_version_id,
        "trip_id": df["trip_id"],
        "stop_id": df["stop_id"],
        "stop_sequence": df["stop_sequence"].astype(int),
        "arrival_sec": df["arrival_time"].map(time_to_seconds),
        "departure_sec": df["departure_time"].map(time_to_seconds),
        "stop_headsign": df.get("stop_headsign"),
        "pickup_type": pd.to_numeric(df.get("pickup_type"), errors="coerce"),
        "drop_off_type": pd.to_numeric(df.get("drop_off_type"), errors="coerce"),
        "shape_dist_traveled": pd.to_numeric(df.get("shape_dist_traveled"), errors="coerce"),
        "timepoint": pd.to_numeric(df.get("timepoint"), errors="coerce"),
    })
    out.to_sql("stop_times", engine, if_exists="append", index=False, method="multi", chunksize=5000)
    print(f"  loaded {len(out):>8} rows -> stop_times")


def load_frequencies(engine, feed_version_id: int, df: pd.DataFrame):
    out = pd.DataFrame({
        "feed_version_id": feed_version_id,
        "trip_id": df["trip_id"],
        "start_sec": df["start_time"].map(time_to_seconds),
        "end_sec": df["end_time"].map(time_to_seconds),
        "headway_secs": pd.to_numeric(df["headway_secs"], errors="coerce"),
        "exact_times": pd.to_numeric(df.get("exact_times"), errors="coerce"),
    })
    out.to_sql("frequencies", engine, if_exists="append", index=False, method="multi", chunksize=5000)
    print(f"  loaded {len(out):>8} rows -> frequencies")


def load_stop_observations(engine, feed_version_id: int, df: pd.DataFrame):
    # Real column names as actually shipped by 511 (confirmed against a
    # live file), which differ from the 2022 GTFS-Performance draft doc:
    # no plain stop_id (from_stop_id/to_stop_id instead), service_date
    # instead of trip_start_date, and scheduled_arrival_time/
    # scheduled_departure_time included directly on the row. If a future
    # month's file uses different names again, this raises a clear
    # KeyError naming the missing column rather than silently loading
    # nulls or garbage.
    required = ["trip_id", "stop_sequence", "service_date", "to_stop_id",
                "observed_arrival_time", "scheduled_arrival_time"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"stop_observations.txt is missing expected columns {missing}. "
            f"Actual columns: {list(df.columns)}. Update load_stop_observations() to match."
        )

    out = pd.DataFrame()
    out["feed_version_id"] = [feed_version_id] * len(df)
    out["trip_id"] = df["trip_id"]
    out["stop_sequence"] = pd.to_numeric(df["stop_sequence"], errors="coerce").astype("Int64")
    out["stop_id"] = df["to_stop_id"]                      # observation is FOR the stop being arrived at
    out["from_stop_id"] = df.get("from_stop_id")
    out["schedule_relationship"] = df.get("schedule_relationship")
    out["vehicle_id"] = df.get("vehicle_id")

    raw_date = df["service_date"]
    parsed = pd.to_datetime(raw_date, format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        parsed = pd.to_datetime(raw_date, errors="coerce")
    out["service_date"] = parsed

    out["scheduled_arrival_sec"] = df["scheduled_arrival_time"].map(time_to_seconds)
    out["observed_arrival_sec"] = df["observed_arrival_time"].map(time_to_seconds)
    if "scheduled_departure_time" in df.columns:
        out["scheduled_departure_sec"] = df["scheduled_departure_time"].map(time_to_seconds)
    if "observed_departure_time" in df.columns:
        out["observed_departure_sec"] = df["observed_departure_time"].map(time_to_seconds)
    if "dwell_time_secs" in df.columns:
        out["dwell_time_secs"] = pd.to_numeric(df["dwell_time_secs"], errors="coerce")
    if "scheduled_dwell_time_secs" in df.columns:
        out["scheduled_dwell_time_secs"] = pd.to_numeric(df["scheduled_dwell_time_secs"], errors="coerce")
    if "uncertainty" in df.columns:
        out["uncertainty"] = pd.to_numeric(df["uncertainty"], errors="coerce")

    # Rows missing a stop_sequence or service_date can't satisfy the primary
    # key -- drop them rather than let the whole load fail.
    before = len(out)
    out = out.dropna(subset=["stop_sequence", "service_date"])
    if len(out) < before:
        print(f"  WARNING: dropped {before - len(out)} stop_observations rows with null "
              f"stop_sequence/service_date (couldn't satisfy primary key)")

    # A trip can appear more than once for the same stop_sequence/date in
    # rare re-broadcast/correction cases -- keep the last one seen.
    out = out.drop_duplicates(subset=["feed_version_id", "trip_id", "stop_sequence", "service_date"], keep="last")

    out.to_sql("stop_observations", engine, if_exists="append", index=False, method="multi", chunksize=5000)
    print(f"  loaded {len(out):>8} rows -> stop_observations")


def load_stops_with_geom(engine, feed_version_id: int, df: pd.DataFrame):
    keep = [c for c in TABLE_SPECS["stops.txt"][1] if c in df.columns]
    out = df[keep].copy()
    out.insert(0, "feed_version_id", feed_version_id)
    out["stop_lat"] = pd.to_numeric(out["stop_lat"], errors="coerce")
    out["stop_lon"] = pd.to_numeric(out["stop_lon"], errors="coerce")
    out.to_sql("stops", engine, if_exists="append", index=False, method="multi", chunksize=5000)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE stops SET geom = ST_SetSRID(ST_MakePoint(stop_lon, stop_lat), 4326)::geography
            WHERE feed_version_id = :fv AND geom IS NULL AND stop_lat IS NOT NULL AND stop_lon IS NOT NULL
        """), {"fv": feed_version_id})
    print(f"  loaded {len(out):>8} rows -> stops (with geom)")


def apply_schema(engine, schema_dir: Path):
    for f in sorted(schema_dir.glob("*.sql")):
        print(f"Applying {f.name} ...")
        sql = f.read_text()
        with engine.begin() as conn:
            conn.execute(text(sql))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True, help="Path to extracted GTFS directory")
    ap.add_argument("--source", help="Feed source label, e.g. RG (inferred from dir name if omitted)")
    ap.add_argument("--label", help="Feed vintage label, e.g. 2025-08 (inferred if omitted)")
    ap.add_argument("--init-schema", action="store_true",
                     help="Apply sql/schema/*.sql before loading (idempotent, safe to re-run)")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("Set DATABASE_URL in your .env first (see .env.example)")
    engine = create_engine(db_url)

    if args.init_schema:
        apply_schema(engine, Path(__file__).resolve().parent.parent / "sql" / "schema")

    path = Path(args.path)
    if not path.is_dir():
        sys.exit(f"{path} is not a directory. Did you run scripts/download_gtfs.py first?")

    dirname = path.name
    has_obs = dirname.endswith("_so") or (path / "stop_observations.txt").exists()
    m = re.match(r"([^_]+)_([^_]+(?:-\d+)?)", dirname)
    source = args.source or (m.group(1) if m else "unknown")
    label = args.label or (m.group(2) if m else dirname)

    feed_version_id = get_or_create_feed_version(engine, source, label, has_obs)
    print(f"feed_version_id = {feed_version_id} (source={source}, label={label}, has_obs={has_obs})")

    if (path / "stops.txt").exists():
        load_stops_with_geom(engine, feed_version_id, read_txt(path / "stops.txt"))

    for fname, (table, cols) in TABLE_SPECS.items():
        if fname in ("stops.txt",):
            continue
        fpath = path / fname
        if fpath.exists():
            load_simple_table(engine, feed_version_id, read_txt(fpath), table, cols)

    if (path / "stop_times.txt").exists():
        load_stop_times(engine, feed_version_id, read_txt(path / "stop_times.txt"))

    if (path / "frequencies.txt").exists():
        load_frequencies(engine, feed_version_id, read_txt(path / "frequencies.txt"))

    obs_path = path / "stop_observations.txt"
    if obs_path.exists():
        load_stop_observations(engine, feed_version_id, read_txt(obs_path))

    print("Done.")


if __name__ == "__main__":
    main()
