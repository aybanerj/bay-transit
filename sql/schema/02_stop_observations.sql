-- stop_observations.txt: 511's own matching of observed real-world
-- arrival/departure times to actual trip runs, distributed with the
-- historic monthly regional GTFS (operator_id=RG, historic=YYYY-MM-so).
-- This is the fastest path to a delay dataset: no need to run a real-time
-- poller for months before you have anything to analyze.
--
-- Schema note: the file 511 actually ships differs from the 2022
-- GTFS-Performance draft doc in a few ways worth knowing up front:
--   * no plain `stop_id` column -- instead `from_stop_id`/`to_stop_id`.
--     The observation is *for* to_stop_id (the stop being arrived at), so
--     that's what we store as `stop_id` below; from_stop_id (the previous
--     stop) is kept alongside for context/debugging.
--   * `service_date` instead of the documented `trip_start_date`.
--   * scheduled_arrival_time / scheduled_departure_time are included
--     directly on each row, so delay can be computed without joining back
--     to stop_times -- which is also more robust here, since historic
--     trips.txt IDs are sometimes hashed/namespaced differently than the
--     RT-feed trip_id that this file's trip_id actually references.
CREATE TABLE IF NOT EXISTS stop_observations (
    feed_version_id           INTEGER NOT NULL REFERENCES feed_version(feed_version_id) ON DELETE CASCADE,
    trip_id                   TEXT NOT NULL,
    stop_sequence              INTEGER NOT NULL,
    service_date               DATE NOT NULL,
    stop_id                    TEXT,      -- = to_stop_id: the stop this observation is for
    from_stop_id               TEXT,      -- previous stop, kept for context/debugging
    schedule_relationship       TEXT,      -- SCHEDULED / SKIPPED / NO_DATA / UNSCHEDULED / CANCELED / DUPLICATED / MODIFIED
    vehicle_id                  TEXT,
    scheduled_arrival_sec        INTEGER,
    observed_arrival_sec         INTEGER,
    scheduled_departure_sec     INTEGER,
    observed_departure_sec      INTEGER,
    dwell_time_secs              INTEGER,
    scheduled_dwell_time_secs    INTEGER,
    uncertainty                  INTEGER,
    PRIMARY KEY (feed_version_id, trip_id, stop_sequence, service_date)
);

CREATE INDEX IF NOT EXISTS idx_stop_obs_stop_date
    ON stop_observations(feed_version_id, stop_id, service_date);
CREATE INDEX IF NOT EXISTS idx_stop_obs_trip_date
    ON stop_observations(feed_version_id, trip_id, service_date);

-- Per-stop-visit delay, computed directly from the file's own
-- scheduled_*/observed_* columns (no join to stop_times needed -- see
-- schema note above). delay_sec > 0 means late, < 0 means early.
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

-- Route-level on-time-performance rollup. "On time" here uses the common
-- transit-industry convention of [-60s, +300s] around scheduled arrival;
-- treat this as a starting default, not a fixed truth -- revisit once you've
-- looked at the actual delay distribution, since conventions vary by agency
-- and mode (rail vs. bus tolerances differ in practice).
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
