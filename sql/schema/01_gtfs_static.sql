-- Core GTFS static schema.
-- Column names/types follow the GTFS reference (https://gtfs.org/schedule/reference/)
-- closely so that CSV loads are close to 1:1 with minimal transformation.
-- Run order matters: files in this directory are applied alphabetically by
-- docker's init mechanism and by scripts/load_gtfs.py --init-schema.

CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per GTFS feed you've loaded (a "vintage"). Lets you keep multiple
-- historic monthly snapshots side by side without collisions, and know which
-- feed_version any given trip/stop/etc. row came from.
CREATE TABLE IF NOT EXISTS feed_version (
    feed_version_id     SERIAL PRIMARY KEY,
    source              TEXT NOT NULL,           -- e.g. 'RG' (regional), or an operator_id
    label               TEXT NOT NULL,            -- e.g. '2025-08' for a historic month, or 'live'
    has_stop_observations BOOLEAN NOT NULL DEFAULT FALSE,
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, label)
);

CREATE TABLE IF NOT EXISTS agency (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    agency_id           TEXT NOT NULL,
    agency_name         TEXT NOT NULL,
    agency_url          TEXT,
    agency_timezone     TEXT,
    agency_lang         TEXT,
    agency_phone        TEXT,
    PRIMARY KEY (feed_version_id, agency_id)
);

CREATE TABLE IF NOT EXISTS routes (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    route_id            TEXT NOT NULL,
    agency_id           TEXT,
    route_short_name    TEXT,
    route_long_name     TEXT,
    route_desc          TEXT,
    route_type          SMALLINT,     -- 0=tram,1=subway,2=rail,3=bus,4=ferry,...
    route_color         TEXT,
    route_text_color    TEXT,
    PRIMARY KEY (feed_version_id, route_id)
);

CREATE TABLE IF NOT EXISTS stops (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    stop_id             TEXT NOT NULL,
    stop_code           TEXT,
    stop_name           TEXT,
    stop_desc           TEXT,
    stop_lat            DOUBLE PRECISION,
    stop_lon            DOUBLE PRECISION,
    zone_id             TEXT,
    location_type       SMALLINT,     -- 0=stop/platform,1=station,2=entrance,...
    parent_station      TEXT,
    wheelchair_boarding  SMALLINT,
    geom                geography(Point, 4326),
    PRIMARY KEY (feed_version_id, stop_id)
);

CREATE TABLE IF NOT EXISTS calendar (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    service_id          TEXT NOT NULL,
    monday SMALLINT, tuesday SMALLINT, wednesday SMALLINT, thursday SMALLINT,
    friday SMALLINT, saturday SMALLINT, sunday SMALLINT,
    start_date          DATE,
    end_date            DATE,
    PRIMARY KEY (feed_version_id, service_id)
);

CREATE TABLE IF NOT EXISTS calendar_dates (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    service_id          TEXT NOT NULL,
    date                DATE NOT NULL,
    exception_type      SMALLINT NOT NULL,  -- 1=added, 2=removed
    PRIMARY KEY (feed_version_id, service_id, date)
);

CREATE TABLE IF NOT EXISTS trips (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    trip_id             TEXT NOT NULL,
    route_id            TEXT NOT NULL,
    service_id          TEXT NOT NULL,
    trip_headsign       TEXT,
    trip_short_name     TEXT,
    direction_id        SMALLINT,
    block_id            TEXT,
    shape_id            TEXT,
    wheelchair_accessible SMALLINT,
    bikes_allowed       SMALLINT,
    PRIMARY KEY (feed_version_id, trip_id)
);

-- Scheduled stop times. arrival/departure stored as seconds-since-midnight
-- (INTEGER) rather than TIME, because GTFS allows values >24:00:00 for
-- trips that run past midnight -- a TIME column would silently corrupt those.
CREATE TABLE IF NOT EXISTS stop_times (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    trip_id             TEXT NOT NULL,
    stop_id             TEXT NOT NULL,
    stop_sequence       INTEGER NOT NULL,
    arrival_sec         INTEGER,      -- seconds since midnight of service day
    departure_sec       INTEGER,
    stop_headsign       TEXT,
    pickup_type         SMALLINT,
    drop_off_type       SMALLINT,
    shape_dist_traveled DOUBLE PRECISION,
    timepoint           SMALLINT,
    PRIMARY KEY (feed_version_id, trip_id, stop_sequence)
);

CREATE TABLE IF NOT EXISTS shapes (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    shape_id            TEXT NOT NULL,
    shape_pt_sequence   INTEGER NOT NULL,
    shape_pt_lat        DOUBLE PRECISION,
    shape_pt_lon        DOUBLE PRECISION,
    shape_dist_traveled DOUBLE PRECISION,
    PRIMARY KEY (feed_version_id, shape_id, shape_pt_sequence)
);

CREATE TABLE IF NOT EXISTS frequencies (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    trip_id             TEXT NOT NULL,
    start_sec           INTEGER NOT NULL,
    end_sec             INTEGER NOT NULL,
    headway_secs        INTEGER NOT NULL,
    exact_times         SMALLINT
);

CREATE TABLE IF NOT EXISTS transfers (
    feed_version_id     INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    from_stop_id        TEXT,
    to_stop_id          TEXT,
    transfer_type       SMALLINT,
    min_transfer_time   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(feed_version_id, trip_id);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(feed_version_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_trips_route ON trips(feed_version_id, route_id);
CREATE INDEX IF NOT EXISTS idx_stops_geom ON stops USING GIST (geom);
