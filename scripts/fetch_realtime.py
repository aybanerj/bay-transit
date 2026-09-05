#!/usr/bin/env python3
"""
Poll the 511 regional GTFS-Realtime feeds (protobuf) once and append a
snapshot to rt_vehicle_positions / rt_trip_updates / rt_service_alerts.

This is Phase 3 -- not needed to start doing delay analysis (use the
historic stop_observations feed for that). Stand this up once you need
live/current signal, e.g. for a "best connection right now given current
delays" feature, or once you want to build your own longitudinal RT archive
going forward.

Intended to be run on a schedule (cron every 1-2 min, or a simple
`while True: ...; time.sleep(60)` loop / systemd timer), since 511 does not
archive GTFS-RT history for you -- only the current snapshot is ever
available at these endpoints.
"""
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2
from sqlalchemy import create_engine, text

load_dotenv()

BASE = "https://api.511.org/Transit"


def fetch_feed(endpoint: str, api_key: str) -> gtfs_realtime_pb2.FeedMessage:
    resp = requests.get(f"{BASE}/{endpoint}", params={"api_key": api_key, "agency": "RG"}, timeout=60)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def load_vehicle_positions(engine, feed):
    rows = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        rows.append({
            "vehicle_id": v.vehicle.id or None,
            "trip_id": v.trip.trip_id or None,
            "route_id": v.trip.route_id or None,
            "latitude": v.position.latitude if v.HasField("position") else None,
            "longitude": v.position.longitude if v.HasField("position") else None,
            "bearing": v.position.bearing if v.HasField("position") and v.position.HasField("bearing") else None,
            "speed_mps": v.position.speed if v.HasField("position") and v.position.HasField("speed") else None,
            "current_stop_sequence": v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
            "current_status": gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(v.current_status)
                               if v.HasField("current_status") else None,
            "vehicle_timestamp": datetime.fromtimestamp(v.timestamp, tz=timezone.utc) if v.HasField("timestamp") else None,
        })
    if not rows:
        return 0
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT INTO rt_vehicle_positions
                    (vehicle_id, trip_id, route_id, latitude, longitude, bearing,
                     speed_mps, current_stop_sequence, current_status, vehicle_timestamp, geom)
                VALUES
                    (:vehicle_id, :trip_id, :route_id, :latitude, :longitude, :bearing,
                     :speed_mps, :current_stop_sequence, :current_status, :vehicle_timestamp,
                     CASE WHEN :longitude IS NOT NULL AND :latitude IS NOT NULL
                          THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                          ELSE NULL END)
            """), r)
    return len(rows)


def load_trip_updates(engine, feed):
    rows = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        for stu in tu.stop_time_update:
            rows.append({
                "trip_id": tu.trip.trip_id or None,
                "route_id": tu.trip.route_id or None,
                "vehicle_id": tu.vehicle.id if tu.HasField("vehicle") else None,
                "stop_id": stu.stop_id or None,
                "stop_sequence": stu.stop_sequence if stu.HasField("stop_sequence") else None,
                "arrival_delay_sec": stu.arrival.delay if stu.HasField("arrival") and stu.arrival.HasField("delay") else None,
                "departure_delay_sec": stu.departure.delay if stu.HasField("departure") and stu.departure.HasField("delay") else None,
                "predicted_arrival_ts": datetime.fromtimestamp(stu.arrival.time, tz=timezone.utc)
                                        if stu.HasField("arrival") and stu.arrival.HasField("time") else None,
                "predicted_departure_ts": datetime.fromtimestamp(stu.departure.time, tz=timezone.utc)
                                          if stu.HasField("departure") and stu.departure.HasField("time") else None,
            })
    if not rows:
        return 0
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT INTO rt_trip_updates
                    (trip_id, route_id, vehicle_id, stop_id, stop_sequence,
                     arrival_delay_sec, departure_delay_sec, predicted_arrival_ts, predicted_departure_ts)
                VALUES
                    (:trip_id, :route_id, :vehicle_id, :stop_id, :stop_sequence,
                     :arrival_delay_sec, :departure_delay_sec, :predicted_arrival_ts, :predicted_departure_ts)
            """), r)
    return len(rows)


def main():
    api_key = os.environ.get("MTC_511_API_KEY")
    db_url = os.environ.get("DATABASE_URL")
    if not api_key or api_key == "your_key_here":
        sys.exit("Set MTC_511_API_KEY in your .env first.")
    if not db_url:
        sys.exit("Set DATABASE_URL in your .env first.")

    engine = create_engine(db_url)

    vp_feed = fetch_feed("VehiclePositions", api_key)
    n_vp = load_vehicle_positions(engine, vp_feed)
    print(f"vehicle_positions: {n_vp} rows")

    tu_feed = fetch_feed("TripUpdates", api_key)
    n_tu = load_trip_updates(engine, tu_feed)
    print(f"trip_updates: {n_tu} rows")

    # Service alerts intentionally omitted from this starter script --
    # add similarly to the two above if/when you need them; the schema
    # (rt_service_alerts) is already there.


if __name__ == "__main__":
    main()
