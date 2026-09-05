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

## Repo layout

```
sql/schema/         DDL, applied in filename order
scripts/            ingestion + loading scripts
notebooks/          exploratory analysis 
data/raw/           downloaded zips/protobuf (gitignored)
data/processed/     any derived parquet/csv to cache (gitignored)
docker-compose.yml  local Postgres+PostGIS        
```

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
