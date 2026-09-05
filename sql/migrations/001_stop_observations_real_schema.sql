-- Migration: replace the stop_observations table + delay views with a
-- version that matches the actual file 511 ships (which differs from the
-- 2022 GTFS-Performance draft doc in a few ways worth noting):
--   * no plain `stop_id` -- instead `from_stop_id`/`to_stop_id`. The
--     observation is *for* to_stop_id (the stop being arrived at); this
--     migration renames the concept accordingly.
--   * `service_date` instead of `trip_start_date`.
--   * scheduled_arrival_time / scheduled_departure_time are included
--     directly on the row, so delay can be computed without joining back
--     to stop_times (which is also more robust: historic trips.txt IDs are
--     sometimes hashed/namespaced differently than the RT feed's trip_id
--     that this file's trip_id actually references).
--
-- Safe to run even if stop_observations already has rows from a prior
-- partial/successful load using the old schema -- those rows are dropped,
-- since the old schema was based on an incorrect column-name guess. Nothing
-- else (agency/routes/trips/stop_times/etc.) is touched.

DROP VIEW IF EXISTS v_route_otp_daily;
DROP VIEW IF EXISTS v_stop_delays;
DROP TABLE IF EXISTS stop_observations;

CREATE TABLE stop_observations (
    feed_version_id         INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    trip_id                 TEXT NOT NULL,
    stop_sequence           INTEGER NOT NULL,
    service_date            DATE NOT NULL,
    stop_id                 TEXT,      -- = to_stop_id: the stop this observation is for
    from_stop_id            TEXT,      -- previous stop, kept for context/debugging
    schedule_relationship    TEXT,      -- SCHEDULED / SKIPPED / NO_DATA / UNSCHEDULED / CANCELED / DUPLICATED / MODIFIED
    vehicle_id               TEXT,
    scheduled_arrival_sec     INTEGER,
    observed_arrival_sec      INTEGER,
    scheduled_departure_sec  INTEGER,
    observed_departure_sec   INTEGER,
    dwell_time_secs           INTEGER,
    scheduled_dwell_time_secs INTEGER,
    uncertainty               INTEGER,
    PRIMARY KEY (feed_version_id, trip_id, stop_sequence, service_date)
);

CREATE INDEX idx_stop_obs_stop_date ON stop_observations(feed_version_id, stop_id, service_date);
CREATE INDEX idx_stop_obs_trip_date ON stop_observations(feed_version_id, trip_id, service_date);

-- Delay computed directly from the file's own scheduled_*/observed_*
-- columns -- no join to stop_times needed (see note above on why that's
-- more robust for this feed).
CREATE OR REPLACE VIEW v_stop_delays AS
SELECT
    o.feed_version_id,
    o.trip_id,
    o.stop_id,
    o.from_stop_id,
    o.stop_sequence,
    o.service_date,
    o.vehicle_id,
    o.schedule_relationship,
    t.route_id,
    r.route_short_name,
    r.route_type,
    o.scheduled_arrival_sec,
    o.observed_arrival_sec,
    (o.observed_arrival_sec - o.scheduled_arrival_sec) AS arrival_delay_sec,
    o.scheduled_departure_sec,
    o.observed_departure_sec,
    (o.observed_departure_sec - o.scheduled_departure_sec) AS departure_delay_sec,
    EXTRACT(DOW FROM o.service_date)::SMALLINT AS day_of_week,   -- 0=Sunday
    (o.scheduled_arrival_sec / 3600) AS scheduled_hour            -- naive hour-of-day bucket, note trips >24:00
FROM stop_observations o
JOIN trips t
  ON t.feed_version_id = o.feed_version_id
 AND t.trip_id = o.trip_id
JOIN routes r
  ON r.feed_version_id = t.feed_version_id
 AND r.route_id = t.route_id;

CREATE OR REPLACE VIEW v_route_otp_daily AS
SELECT
    feed_version_id,
    route_id,
    route_short_name,
    service_date,
    count(*) AS n_stop_visits,
    avg(arrival_delay_sec) AS avg_arrival_delay_sec,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY arrival_delay_sec) AS median_arrival_delay_sec,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY arrival_delay_sec) AS p90_arrival_delay_sec,
    avg(CASE WHEN arrival_delay_sec BETWEEN -60 AND 300 THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM v_stop_delays
WHERE arrival_delay_sec IS NOT NULL
GROUP BY feed_version_id, route_id, route_short_name, service_date;
