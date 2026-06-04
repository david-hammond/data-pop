"""
Create the db-pop schema on your local PostgreSQL server.
Assumes the database already exists: createdb db-pop

Re-running drops and recreates all tables. Safe to run again from scratch.
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

DDL = """
DROP TABLE IF EXISTS data           CASCADE;
DROP TABLE IF EXISTS load_queue     CASCADE;
DROP TABLE IF EXISTS indicators     CASCADE;
DROP TABLE IF EXISTS variants       CASCADE;
DROP TABLE IF EXISTS locations      CASCADE;
DROP TABLE IF EXISTS updates        CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metadata_view;

CREATE TABLE locations (
    location_id  SERIAL  PRIMARY KEY,
    un_id        INTEGER NOT NULL UNIQUE,
    name         VARCHAR(200) NOT NULL,
    loc_type_id  INTEGER,
    loc_type     VARCHAR(100),
    iso2_code    CHAR(2),
    iso3_code    CHAR(3),
    un_region_id INTEGER
);

CREATE INDEX idx_locations_un_id ON locations(un_id);

CREATE TABLE variants (
    variant_id SERIAL  PRIMARY KEY,
    un_id      INTEGER NOT NULL UNIQUE,
    short_name VARCHAR(50)  NOT NULL,
    name       VARCHAR(200) NOT NULL
);

CREATE TABLE indicators (
    indicator_id SERIAL  PRIMARY KEY,
    un_id        INTEGER NOT NULL UNIQUE,
    name         VARCHAR(500) NOT NULL,
    display_name VARCHAR(500),
    unit         VARCHAR(200)
);

CREATE TABLE updates (
    update_id  SERIAL PRIMARY KEY,
    updated_at TIMESTAMP DEFAULT now()
);

-- sex and age_label are empty string when not applicable (aggregates)
CREATE TABLE data (
    location_id  INTEGER NOT NULL REFERENCES locations(location_id),
    indicator_id INTEGER NOT NULL REFERENCES indicators(indicator_id),
    variant_id   INTEGER NOT NULL REFERENCES variants(variant_id),
    time_label   VARCHAR(20)   NOT NULL,
    sex          VARCHAR(20)   NOT NULL DEFAULT '',
    age_label    VARCHAR(20)   NOT NULL DEFAULT '',
    value        DECIMAL(20, 4),
    update_id    INTEGER REFERENCES updates(update_id),
    PRIMARY KEY (location_id, indicator_id, variant_id, time_label, sex, age_label)
);

CREATE INDEX idx_data_indicator ON data(indicator_id);
CREATE INDEX idx_data_location  ON data(location_id);
CREATE INDEX idx_data_variant   ON data(variant_id);

CREATE TABLE load_queue (
    queue_id    SERIAL  PRIMARY KEY,
    un_id       INTEGER NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    tried       INTEGER DEFAULT 0,
    last_loaded TIMESTAMP,
    last_error  TEXT
);

CREATE MATERIALIZED VIEW metadata_view AS
SELECT
    i.indicator_id,
    i.un_id,
    i.name,
    i.unit,
    COUNT(DISTINCT d.location_id) AS num_locations,
    COUNT(DISTINCT d.variant_id)  AS num_variants,
    MIN(d.time_label)             AS earliest,
    MAX(d.time_label)             AS latest
FROM indicators i
LEFT JOIN data d ON i.indicator_id = d.indicator_id
GROUP BY i.indicator_id, i.un_id, i.name, i.unit;
"""


if __name__ == "__main__":
    con = psycopg2.connect(DATABASE_URL)
    cur = con.cursor()
    cur.execute(DDL)
    con.commit()
    cur.close()
    con.close()
    print("Schema created. Run 02_load_unpop.py to populate.")
