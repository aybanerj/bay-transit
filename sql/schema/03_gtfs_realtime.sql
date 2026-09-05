-- Tables for scripts/fetch_realtime.py, which polls the GTFS-RT protobuf
-- feeds (TripUpdates / VehiclePositions / ServiceAlerts) and appends rows.
-- Not needed for Phase 1 historic analysis -- only stand this up once you
-- want live-vehicle-level signal (e.g. "how late is this specific bus right
-- now") that the monthly stop_observations archive can't give you.

CREATE TABLE IF NOT EXISTS rt_vehicle_positions (
    id                  BIGSERIAL PRIMARY KEY,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    vehicle_id          TEXT,
    trip_id             TEXT,
    route_id            TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    bearing             REAL,
    speed_mps           REAL,
    current_stop_sequence INTEGER,
    current_status      TEXT,     -- IN_TRANSIT_TO / STOPPED_AT / INCOMING_AT
    congestion_level    TEXT,
    occupancy_status    TEXT,
    vehicle_timestamp   TIMESTAMPTZ,   -- timestamp reported by the vehicle itself, may lag fetched_at
    geom                geography(Point, 4326)
);
CREATE INDEX IF NOT EXISTS idx_rt_vp_trip_time ON rt_vehicle_positions(trip_id, vehicle_timestamp);
CREATE INDEX IF NOT EXISTS idx_rt_vp_geom ON rt_vehicle_positions USING GIST (geom);

CREATE TABLE IF NOT EXISTS rt_trip_updates (
    id                  BIGSERIAL PRIMARY KEY,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    trip_id             TEXT,
    route_id            TEXT,
    vehicle_id          TEXT,
    stop_id             TEXT,
    stop_sequence       INTEGER,
    arrival_delay_sec   INTEGER,
    departure_delay_sec INTEGER,
    predicted_arrival_ts   TIMESTAMPTZ,
    predicted_departure_ts TIMESTAMPTZ,
    schedule_relationship TEXT
);
CREATE INDEX IF NOT EXISTS idx_rt_tu_trip_time ON rt_trip_updates(trip_id, fetched_at);

CREATE TABLE IF NOT EXISTS rt_service_alerts (
    id                  BIGSERIAL PRIMARY KEY,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_id            TEXT,
    cause               TEXT,
    effect              TEXT,
    header_text         TEXT,
    description_text    TEXT,
    affected_route_id   TEXT,
    affected_stop_id    TEXT,
    active_period_start TIMESTAMPTZ,
    active_period_end   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rt_alerts_route ON rt_service_alerts(affected_route_id);

-- Convenience view: reconstruct each shape as an ordered LineString from the
-- raw per-point shapes table. Useful for map plotting and, later, spatial
-- gap analysis for candidate new stops.
CREATE OR REPLACE VIEW v_shape_lines AS
SELECT
    feed_version_id,
    shape_id,
    ST_MakeLine(
        array_agg(
            ST_SetSRID(ST_MakePoint(shape_pt_lon, shape_pt_lat), 4326)
            ORDER BY shape_pt_sequence
        )
    )::geography AS geom
FROM shapes
GROUP BY feed_version_id, shape_id;
