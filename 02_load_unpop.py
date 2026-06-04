"""
Load UN World Population Prospects data into the db-pop database.
Requires 01_create_schema.py to have been run first.

Data source: UN Population Division Data Portal API
  https://population.un.org/dataportalapi/api/v1/

Resumable: progress is tracked in the load_queue table. Re-running picks up
where it left off (tried = 0). Failed indicators are marked tried = 1 with
last_error set; successful loads have last_error = NULL.
"""

import logging
import os
import time

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
BASE_URL = "https://population.un.org/dataportalapi/api/v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("progress.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def api_get(path, params=None):
    """GET from UN API, returning parsed JSON. Retries once on transient failure."""
    url = f"{BASE_URL}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            log.warning(f"  Retry {attempt + 1}/3 for {url}: {e}")
            time.sleep(5 * (attempt + 1))


def paginate(path, params=None):
    """Yield all items across paginated API responses."""
    params = dict(params or {})
    params.setdefault("format", "json")
    params.setdefault("pageSize", 500)
    page = 1
    total_pages = None
    while total_pages is None or page <= total_pages:
        params["page"] = page
        data = api_get(path, params)
        if total_pages is None:
            total_pages = data.get("totalPages", 1)
            log.info(f"  {path}: page {page}/{total_pages}")
        elif page % 10 == 0:
            log.info(f"  {path}: page {page}/{total_pages}")
        yield from (data.get("data") or [])
        page += 1


# ---------------------------------------------------------------------------
# Phase 1: Reference tables — locations, variants
# ---------------------------------------------------------------------------

def load_locations(con):
    log.info("Loading locations from UN API...")
    items = list(paginate("/locations/"))
    cur = con.cursor()
    inserted = 0
    for loc in items:
        un_id = loc.get("id")
        name = (loc.get("name") or "").strip()
        if not un_id or not name:
            continue
        cur.execute(
            """
            INSERT INTO locations (un_id, name, loc_type_id, loc_type, iso2_code, iso3_code, un_region_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (un_id) DO UPDATE SET
                name         = EXCLUDED.name,
                loc_type_id  = EXCLUDED.loc_type_id,
                loc_type     = EXCLUDED.loc_type,
                iso2_code    = EXCLUDED.iso2_code,
                iso3_code    = EXCLUDED.iso3_code,
                un_region_id = EXCLUDED.un_region_id;
            """,
            (
                un_id,
                name,
                loc.get("locTypeId"),
                (loc.get("locTypeName") or "").strip() or None,
                (loc.get("iso2") or "").strip() or None,
                (loc.get("iso3") or "").strip() or None,
                loc.get("parentId"),
            ),
        )
        inserted += 1
    con.commit()
    cur.close()
    log.info(f"  {inserted} locations loaded.")


def load_variants(con):
    log.info("Loading variants from UN API...")
    data = api_get("/variants/", {"format": "json"})
    items = data.get("data") or []
    cur = con.cursor()
    for v in items:
        un_id = v.get("id")
        name = (v.get("name") or "").strip()
        short_name = (v.get("shortName") or name[:50]).strip()
        if not un_id or not name:
            continue
        cur.execute(
            """
            INSERT INTO variants (un_id, short_name, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (un_id) DO UPDATE SET
                short_name = EXCLUDED.short_name,
                name       = EXCLUDED.name;
            """,
            (un_id, short_name, name),
        )
    con.commit()
    cur.close()
    log.info(f"  {len(items)} variants loaded.")


# ---------------------------------------------------------------------------
# Phase 2: Indicator catalog → indicators + load_queue
# ---------------------------------------------------------------------------

def fetch_indicator_catalog(con):
    """
    Pages through the UN indicators API. Populates indicators and load_queue.
    Safe to re-run: uses ON CONFLICT DO NOTHING for load_queue so existing
    entries (including already-loaded ones) are not reset.
    """
    log.info("Fetching indicator catalog from UN API...")
    items = list(paginate("/indicators/"))
    cur = con.cursor()
    queued = 0
    for ind in items:
        un_id = ind.get("id")
        name = (ind.get("name") or "").strip()
        display_name = (ind.get("displayName") or name).strip()
        unit = (ind.get("unitOfMeasure") or "").strip() or None
        if not un_id or not name:
            continue
        cur.execute(
            """
            INSERT INTO indicators (un_id, name, display_name, unit)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (un_id) DO UPDATE SET
                name         = EXCLUDED.name,
                display_name = EXCLUDED.display_name,
                unit         = EXCLUDED.unit
            RETURNING indicator_id;
            """,
            (un_id, name, display_name, unit),
        )
        cur.execute(
            """
            INSERT INTO load_queue (un_id, name)
            VALUES (%s, %s)
            ON CONFLICT (un_id) DO NOTHING;
            """,
            (un_id, name),
        )
        queued += 1
    con.commit()
    cur.close()
    log.info(f"  Catalog loaded: {queued} indicators.")


# ---------------------------------------------------------------------------
# Phase 3: Data load, driven by load_queue
# ---------------------------------------------------------------------------

def load_indicator(con, queue_id, un_id, name):
    log.info(f"Loading '{name}' (UN indicator {un_id})")
    cur = con.cursor()

    cur.execute("SELECT location_id, un_id FROM locations;")
    loc_id_map = {row[1]: row[0] for row in cur.fetchall()}

    cur.execute("SELECT variant_id, un_id FROM variants;")
    var_id_map = {row[1]: row[0] for row in cur.fetchall()}

    cur.execute("SELECT indicator_id FROM indicators WHERE un_id = %s;", (un_id,))
    row = cur.fetchone()
    if row is None:
        log.error(f"  Indicator {un_id} not in indicators table — skipping.")
        cur.close()
        return False
    indicator_id = row[0]

    # Build comma-separated list of all location un_ids for bulk fetch
    loc_ids_str = ",".join(str(k) for k in loc_id_map.keys())

    try:
        all_rows = list(paginate(
            f"/data/indicators/{un_id}/locations/{loc_ids_str}/start/1950/end/2100",
        ))
    except Exception as e:
        log.error(f"  Fetch failed for indicator {un_id}: {e}")
        cur.execute(
            "UPDATE load_queue SET tried = 1, last_error = %s WHERE queue_id = %s;",
            (str(e)[:500], queue_id),
        )
        con.commit()
        cur.close()
        return False

    if not all_rows:
        log.warning(f"  Indicator {un_id}: no data returned.")
        cur.execute(
            "UPDATE load_queue SET tried = 1, last_loaded = now() WHERE queue_id = %s;",
            (queue_id,),
        )
        con.commit()
        cur.close()
        return True

    cur.execute("INSERT INTO updates DEFAULT VALUES RETURNING update_id;")
    update_id = cur.fetchone()[0]

    rows = []
    skipped = 0
    for rec in all_rows:
        loc_un_id = rec.get("locationId")
        var_un_id = rec.get("variantId")
        time_label = str(rec.get("timeLabel") or "").strip()
        sex = (rec.get("sex") or "").strip()
        age_label = (rec.get("ageLabel") or "").strip()
        value = rec.get("value")

        location_id = loc_id_map.get(loc_un_id)
        variant_id = var_id_map.get(var_un_id)

        if location_id is None or variant_id is None or not time_label:
            skipped += 1
            continue

        rows.append((location_id, indicator_id, variant_id, time_label, sex, age_label, value, update_id))

    if skipped:
        log.warning(f"  Skipped {skipped} rows with missing location/variant/time.")

    if rows:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO data
                (location_id, indicator_id, variant_id, time_label, sex, age_label, value, update_id)
            VALUES %s
            ON CONFLICT (location_id, indicator_id, variant_id, time_label, sex, age_label)
            DO UPDATE SET value = EXCLUDED.value, update_id = EXCLUDED.update_id;
            """,
            rows,
            page_size=1000,
        )

    cur.execute(
        "UPDATE load_queue SET tried = 1, last_loaded = now(), last_error = NULL WHERE queue_id = %s;",
        (queue_id,),
    )
    con.commit()
    cur.close()
    log.info(f"  Indicator {un_id}: {len(rows)} rows inserted.")
    return True


def refresh_metadata_view(con):
    cur = con.cursor()
    cur.execute("REFRESH MATERIALIZED VIEW metadata_view;")
    con.commit()
    cur.close()
    log.info("Metadata view refreshed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    con = get_conn()

    load_locations(con)
    load_variants(con)
    fetch_indicator_catalog(con)

    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM load_queue WHERE tried = 0 ORDER BY queue_id;")
    pending = cur.fetchall()
    cur.close()

    if not pending:
        log.info("load_queue: nothing pending.")
    else:
        log.info(f"load_queue: {len(pending)} indicator(s) pending.")
        for item in pending:
            load_indicator(con, item["queue_id"], item["un_id"], item["name"])

    refresh_metadata_view(con)
    con.close()
    log.info("Done.")
