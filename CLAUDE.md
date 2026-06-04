# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A normalised PostgreSQL database of UN World Population Prospects (WPP) data, loaded from the UN Population Division Data Portal API. Two Python scripts handle setup and data loading; the database runs on the local postgres server via Unix socket.

## Commands

```bash
createdb db-pop
pip install -r requirements.txt
python3 01_create_schema.py    # drop and recreate all tables
python3 02_load_unpop.py       # run loader (resumable)
bash load-loop.sh              # loop loader until queue is empty (initial full load)
bash update.sh                 # full refresh (resets all indicators, re-runs loader)
```

Python must be run from `.venv`: `source .venv/bin/activate` or use `.venv/bin/python3` directly. There are no tests.

## Architecture

**`01_create_schema.py`** — Pure DDL. Drops and recreates every table and the materialized view. Safe to re-run; wipes all data.

**`02_load_unpop.py`** — Four-phase loader:
1. `load_locations()` — populates `locations` via `GET /locations/`.
2. `load_variants()` — populates `variants` via `GET /variants/`.
3. `fetch_indicator_catalog()` — pages through `GET /indicators/` to populate `indicators` and `load_queue`. Safe to re-run: existing `load_queue` rows are not reset.
4. Processes `load_queue WHERE tried = 0` — for each indicator fetches all location data via `GET /data/indicators/{un_id}/locations/{all_loc_ids}/start/1950/end/2100` and bulk-upserts into `data`.

**Resumability** — `load_queue` tracks state: `tried = 0` (pending), `tried = 1` with `last_loaded` set (success), `tried = 1` with `last_error` set (failed, skipped on retry).

## Data source

UN Population Division Data Portal API:
- Base URL: `https://population.un.org/dataportalapi/api/v1/`
- `/locations/` — countries, regions, continents, world aggregates
- `/variants/` — projection scenarios (Medium, High, Low, Constant fertility, …)
- `/indicators/` — ~100 population indicators
- `/data/indicators/{id}/locations/{ids}/start/{year}/end/{year}` — time series

## Database schema

| Table | Purpose |
|---|---|
| `locations` | UN location lookup (countries + aggregates); `un_id` is the canonical key |
| `variants` | Projection variant lookup (Medium, High, Low, etc.) |
| `indicators` | Indicator catalog; `un_id` is the canonical key |
| `updates` | Audit timestamps for data loads |
| `data` | location_id × indicator_id × variant_id × time_label × sex × age_label → value |
| `load_queue` | One row per indicator: un_id, name, tried, last_loaded, last_error |
| `metadata_view` | Materialized: indicator coverage (num_locations, num_variants, earliest, latest) |

`data` primary key: `(location_id, indicator_id, variant_id, time_label, sex, age_label)`. Re-loading upserts values rather than duplicating. `sex` and `age_label` are empty string for aggregate rows where those dimensions don't apply.

## Environment

Connection via `DATABASE_URL` in `.env` (copy from `.env.example`). Uses the local postgres Unix socket as user `elliptica`, no password. Database name: `db-pop`.

## Cron

Schedule `update.sh` annually (UN WPP is updated every 2 years). Example crontab:
```
0 3 1 1 * /home/elliptica/projects/data-pop/update.sh >> /home/elliptica/projects/data-pop/cron.log 2>&1
```
