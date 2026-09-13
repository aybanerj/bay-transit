# Bay Area Transit Reliability

A study on the  reliability of Bay Area public transit using MTC's 511 Open Data.

Status: **investigative / Phase 0-1**. The specific research question is not
locked yet — this repo is deliberately built so that a wide range of downstream
questions (delay prediction, dynamic re-routing, network design) can all be
answered from the same data foundation without re-architecting.

## Data source

[511 SF Bay Open Data Portal](https://511.org/open-data/transit) (managed by
MTC).

Three layers of data are available, and we ingest all three because they serve
different purposes:

| Layer | What it is | Why we want it |
|---|---|---|
| **Static GTFS** | Scheduled routes, stops, trips, timetables | The "ground truth" schedule everything else is measured against |
| **Historic GTFS + `stop_observations.txt`** | Monthly archives of the above *plus* actually-observed arrival/departure times per stop per trip | This is the single most valuable feed for a delay project — MTC has already matched real-world observed times to scheduled times, going back monthly. Investigating this before building any real-time pipeline. |
| **GTFS-Realtime** (TripUpdates / VehiclePositions / ServiceAlerts, protobuf) | Live feed, updated continuously | Will be used for *current/future* prediction (not just historical analysis), and for building a "choose best connection right now" feature later. Will have to run a small poller over time to accumulate my own historical archive, since 511 does not archive this going forward — the monthly `stop_observations.txt` is effectively their pre-archived version of this. |

## Architecture

```
                 ┌─────────────────────┐
 511 API ───────►│ scripts/download_*  │  raw zip/protobuf → data/raw/
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │ scripts/load_*.py     │  parses CSV/protobuf, upserts
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │ Postgres + PostGIS    │  sql/schema/*.sql
                 │  - gtfs_static schema │
                 │  - gtfs_rt schema     │
                 │  - views (delay calc) │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │ notebooks/ (EDA)      │  pandas / geopandas / sql
                 │ future: models/       │  delay prediction, etc.
                 └───────────────────────┘
```

Postgres + PostGIS (not vanilla Postgres) because stops and shapes are
inherently spatial — want `ST_DWithin`, nearest-neighbor queries, and
eventually maybe `pgRouting` once I get to the "optimal new
routes/stops" question.

## Data dictionary

Every table below (except `feed_version`) also carries a `feed_version_id`
column as part of its real primary key, so multiple monthly snapshots can
coexist — omitted from each table here for brevity.

### `feed_version`
Not a GTFS file — bookkeeping so multiple monthly snapshots can coexist.

| Column | Type | Meaning |
|---|---|---|
| `feed_version_id` | `SERIAL` | Primary key, referenced by every other table |
| `source` | `TEXT` | e.g. `'RG'` for the regional consolidated feed |
| `label` | `TEXT` | e.g. `'2025-08'` — the month/vintage |
| `has_stop_observations` | `BOOLEAN` | Whether this vintage includes observed-delay data |
| `loaded_at` | `TIMESTAMPTZ` | When you ran the load |

### `agency` (from `agency.txt`)
One row per transit operator.

| Column | Type | Meaning |
|---|---|---|
| `agency_id` | `TEXT` | Operator identifier, e.g. `SF` |
| `agency_name` | `TEXT` | Human name, e.g. "San Francisco Municipal Transportation Agency" |
| `agency_url` | `TEXT` | Operator's website |
| `agency_timezone` | `TEXT` | e.g. `America/Los_Angeles` |
| `agency_lang` | `TEXT` | Language code |
| `agency_phone` | `TEXT` | Contact number |

### `routes` (from `routes.txt`)
One row per bus/rail line.

| Column | Type | Meaning |
|---|---|---|
| `route_id` | `TEXT` | Unique per operator, e.g. `SF:1` |
| `agency_id` | `TEXT` | Links back to `agency` |
| `route_short_name` | `TEXT` | Rider-facing label, e.g. `"38"` or `"N"` |
| `route_long_name` | `TEXT` | e.g. `"Geary"` |
| `route_desc` | `TEXT` | Free-text description |
| `route_type` | `SMALLINT` | GTFS mode code: `0`=tram/streetcar, `1`=subway/metro, `2`=commuter rail, `3`=bus, `4`=ferry (others exist, rarer). Worth grouping by this — reliability differs a lot by mode. |
| `route_color` | `TEXT` | Hex color, map display only |
| `route_text_color` | `TEXT` | Hex color, map display only |

### `stops` (from `stops.txt`)
One row per physical stop/platform/station.

| Column | Type | Meaning |
|---|---|---|
| `stop_id` | `TEXT` | Unique per operator |
| `stop_code` | `TEXT` | Rider-facing short code (printed on the physical sign) |
| `stop_name` | `TEXT` | e.g. "Market St & 5th St" |
| `stop_desc` | `TEXT` | Free-text description |
| `stop_lat` / `stop_lon` | `DOUBLE PRECISION` | Coordinates |
| `zone_id` | `TEXT` | Fare zone, if used |
| `location_type` | `SMALLINT` | `0`=stop/platform, `1`=station (parent grouping), `2`=entrance/exit |
| `parent_station` | `TEXT` | If this is a platform, the `stop_id` of its parent station |
| `wheelchair_boarding` | `SMALLINT` | Accessibility flag |
| `geom` | `geography(Point,4326)` | PostGIS point generated from lat/lon — enables spatial queries (`ST_DWithin`, nearest-neighbor, etc.) |

### `calendar` (from `calendar.txt`)
One row per named service pattern (e.g. "weekday service"), with day-of-week flags.

| Column | Type | Meaning |
|---|---|---|
| `service_id` | `TEXT` | Referenced by `trips.service_id` |
| `monday` … `sunday` | `SMALLINT` (0/1) | Whether this service runs on that weekday |
| `start_date` / `end_date` | `DATE` | Date range this pattern is valid for |

### `calendar_dates` (from `calendar_dates.txt`)
Exceptions to `calendar` — added/removed service on specific dates (holidays, etc.).

| Column | Type | Meaning |
|---|---|---|
| `service_id` | `TEXT` | Which service pattern |
| `date` | `DATE` | Specific date |
| `exception_type` | `SMALLINT` | `1`=service added, `2`=service removed |

### `trips` (from `trips.txt`)
One row per scheduled vehicle run (e.g. "the 8:03am 38-Geary bus").

| Column | Type | Meaning |
|---|---|---|
| `trip_id` | `TEXT` | Unique identifier for this specific run |
| `route_id` | `TEXT` | Which route |
| `service_id` | `TEXT` | Which calendar pattern (which days it runs) |
| `trip_headsign` | `TEXT` | Destination text shown to riders |
| `trip_short_name` | `TEXT` | Rider-facing short label, if any |
| `direction_id` | `SMALLINT` | `0`/`1` — inbound vs outbound, roughly |
| `block_id` | `TEXT` | Groups trips a single vehicle runs back-to-back |
| `shape_id` | `TEXT` | Links to `shapes` for the physical path this trip follows |
| `wheelchair_accessible` | `SMALLINT` | Accessibility flag |
| `bikes_allowed` | `SMALLINT` | Bike-carriage flag |

### `stop_times` (from `stop_times.txt`)
One row per (trip, stop) — the *scheduled* timetable. Usually the largest static table.

| Column | Type | Meaning |
|---|---|---|
| `trip_id` | `TEXT` | Which trip |
| `stop_id` | `TEXT` | Which stop |
| `stop_sequence` | `INTEGER` | Order of this stop within the trip (1, 2, 3, …) |
| `arrival_sec` / `departure_sec` | `INTEGER` | Seconds since midnight of the service day — not a `TIME` column, deliberately: GTFS allows values past `24:00:00` for trips running past midnight, which a `TIME` type would silently corrupt |
| `stop_headsign` | `TEXT` | Overrides the trip's headsign at this stop, if set |
| `pickup_type` / `drop_off_type` | `SMALLINT` | `0`=regular, `1`=none, `2`=phone ahead, `3`=coordinate with driver |
| `shape_dist_traveled` | `DOUBLE PRECISION` | Distance along the shape at this stop |
| `timepoint` | `SMALLINT` | `1`=exact scheduled time, `0`=approximate |

### `shapes` (from `shapes.txt`)
Raw lat/lon points describing the physical path a trip follows. One row per point.

| Column | Type | Meaning |
|---|---|---|
| `shape_id` | `TEXT` | Groups points into one path |
| `shape_pt_sequence` | `INTEGER` | Order along the path |
| `shape_pt_lat` / `shape_pt_lon` | `DOUBLE PRECISION` | Coordinates |
| `shape_dist_traveled` | `DOUBLE PRECISION` | Cumulative distance at this point |

(The `v_shape_lines` view stitches these into an actual PostGIS `LineString` per `shape_id` on demand — the raw table stores GTFS's native per-point format.)

### `frequencies` (from `frequencies.txt`, optional)
For trips that run on a fixed headway (e.g. "every 3 minutes, 6am-10pm") instead of a fixed schedule.

| Column | Type | Meaning |
|---|---|---|
| `trip_id` | `TEXT` | The template trip this applies to |
| `start_sec` / `end_sec` | `INTEGER` | Time window (seconds since midnight) this headway applies |
| `headway_secs` | `INTEGER` | Seconds between departures |
| `exact_times` | `SMALLINT` | `0`=frequency-based/approximate, `1`=exact times generated at that headway |

### `transfers` (from `transfers.txt`)
Explicit transfer rules between stops (usually rare/special cases).

| Column | Type | Meaning |
|---|---|---|
| `from_stop_id` / `to_stop_id` | `TEXT` | The two stops |
| `transfer_type` | `SMALLINT` | `0`=recommended, `1`=timed transfer, `2`=minimum time required, `3`=not possible |
| `min_transfer_time` | `INTEGER` | Seconds needed, if type `2` |

### `stop_observations` (from `stop_observations.txt`)
Not standard GTFS — 511's own record of what *actually* happened, matched to the schedule. One row per (trip, stop, service date) actually observed.

| Column | Type | Meaning |
|---|---|---|
| `trip_id` | `TEXT` | Which scheduled trip this observation is for |
| `stop_sequence` | `INTEGER` | Which stop in that trip |
| `service_date` | `DATE` | The actual calendar date this run happened |
| `stop_id` | `TEXT` | The stop being arrived at (mapped from the file's `to_stop_id`) |
| `from_stop_id` | `TEXT` | The previous stop, kept for context |
| `schedule_relationship` | `TEXT` | `SCHEDULED`, `SKIPPED`, `CANCELED`, `UNSCHEDULED`, etc. — a skipped stop is a different reliability failure than a late one, worth splitting out |
| `vehicle_id` | `TEXT` | Physical vehicle, if reported |
| `scheduled_arrival_sec` / `observed_arrival_sec` | `INTEGER` | Seconds-since-midnight, same convention as `stop_times` |
| `scheduled_departure_sec` / `observed_departure_sec` | `INTEGER` | Same, for departure |
| `dwell_time_secs` / `scheduled_dwell_time_secs` | `INTEGER` | How long the vehicle actually sat at the stop vs. planned |
| `uncertainty` | `INTEGER` | 511's own confidence measure on the observation |

The two views (`v_stop_delays`, `v_route_otp_daily`) sit on top of this table
and compute `observed - scheduled` for you — see `sql/schema/02_stop_observations.sql`.

## Roadmap / phases

- **Phase 0 — first commit**: repo, schema, docker, ingestion scripts.
- **Phase 1 — historic delay baseline**: load 3-6 months of
  `stop_observations`, build the `v_stop_delays` view, do EDA: distribution of
  delays by route/operator/time-of-day/day-of-week, on-time-performance (OTP)
  by route.
- **Phase 2 — feature engineering**: join weather (NOAA), day-type
  (holidays), road traffic/incidents if available, upstream-stop delay as a
  feature (delay propagation along a trip).
- **Phase 3 — live data**: stand up the GTFS-RT poller
  (`scripts/fetch_realtime.py`) on a cron/small worker to start accumulating
  my own live archive, once I know what I need it for (e.g. real-time
  inference features that historic data can't provide, like "how late is
  this specific vehicle right now").
- **Phase 4 — modeling**: delay prediction (regression/classification per
  trip-stop), route/connection reliability scoring, possibly a simple
  reinforcement-learning or shortest-path-under-uncertainty formulation for
  "best connection given delay risk."
- **Phase 5 — network questions**: route/stop popularity via observed
  ridership proxies (if available) or trip density, candidate new-stop
  siting via spatial gap analysis (population/POI density vs. stop coverage,
  using PostGIS).
