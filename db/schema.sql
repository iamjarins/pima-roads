-- Road Work History Map schema. Safe to run repeatedly.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Every pull is kept, so changes to a public record can be shown later.
-- An unchanged pull stores no payload; same_as points at the identical earlier snapshot.
CREATE TABLE IF NOT EXISTS source_snapshot (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,
    source_url  text NOT NULL,
    pulled_at   timestamptz NOT NULL DEFAULT now(),
    row_count   integer NOT NULL,
    sha256      text NOT NULL,
    same_as     bigint REFERENCES source_snapshot(id),
    payload_gz  bytea                       -- gzip of the raw GeoJSON FeatureCollection
);
CREATE INDEX IF NOT EXISTS source_snapshot_source_idx ON source_snapshot (source, pulled_at DESC);

-- What changed between one pull and the next, per source record.
CREATE TABLE IF NOT EXISTS record_change (
    id               bigserial PRIMARY KEY,
    source           text NOT NULL,
    source_record_id text NOT NULL,
    snapshot_id      bigint NOT NULL REFERENCES source_snapshot(id),
    prev_snapshot_id bigint REFERENCES source_snapshot(id),
    detected_at      timestamptz NOT NULL DEFAULT now(),
    change_type      text NOT NULL CHECK (change_type IN ('added', 'removed', 'modified')),
    changes          jsonb                  -- {field: [old, new]}; "geometry" when the line moved
);
CREATE INDEX IF NOT EXISTS record_change_record_idx ON record_change (source, source_record_id);

CREATE TABLE IF NOT EXISTS vendor (
    id             serial PRIMARY KEY,
    canonical_name text NOT NULL UNIQUE,
    aliases        text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project (
    id             serial PRIMARY KEY,
    source         text NOT NULL,
    name           text NOT NULL,           -- exactly as the source wrote it
    parsed_code    jsonb,                   -- county: {fiscal_year, delivery, program, job_number, inferred: true}
    category       text,                    -- city: funding program (Prop 101 Arterial, ...)
    funding_source text,
    link           text,
    UNIQUE (source, name)
);

-- One row per block of road between intersections.
CREATE TABLE IF NOT EXISTS segment (
    id            integer PRIMARY KEY,      -- county ROADID, stable across pulls
    name          text NOT NULL,            -- display name, e.g. "W Speedway Bl"
    name_raw      text,
    name_base     text,                     -- name without direction and type, for matching
    from_desc     text,
    to_desc       text,
    owner         text NOT NULL,            -- county | city | state | town | tribal | private | other | unknown
    owner_name    text NOT NULL,            -- e.g. "Pima County", "Town of Marana"
    owner_basis   text NOT NULL,            -- how we know
    road_category text,
    is_major      boolean NOT NULL DEFAULT false,
    add_min       integer,
    add_max       integer,
    length_m      double precision,
    from_node     integer,
    to_node       integer,
    geom          geometry(LineString, 4326) NOT NULL,
    geom_m        geometry(LineString, 26912) NOT NULL,
    geom_3857     geometry(LineString, 3857) NOT NULL,
    -- filled by the publish step
    last_work_year   integer,
    completed_count  integer NOT NULL DEFAULT 0,
    upcoming_count   integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS segment_geom_idx ON segment USING gist (geom);
CREATE INDEX IF NOT EXISTS segment_geom_3857_idx ON segment USING gist (geom_3857);
CREATE INDEX IF NOT EXISTS segment_geom_m_idx ON segment USING gist (geom_m);
CREATE INDEX IF NOT EXISTS segment_name_trgm_idx ON segment USING gin (upper(name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS segment_name_base_idx ON segment (name_base);

-- One row per source record: one treatment on one stretch of road.
-- A stretch can cover several blocks, so the link to segments is segment_event.
CREATE TABLE IF NOT EXISTS work_event (
    id                bigserial PRIMARY KEY,
    source            text NOT NULL,
    source_record_id  text NOT NULL,
    source_url        text NOT NULL,        -- link to the public record
    snapshot_id       bigint NOT NULL REFERENCES source_snapshot(id),
    street_raw        text,
    location_raw      text,                 -- from/to as the source wrote it
    treatment_raw     text,
    treatment_category text NOT NULL,
    treatment_label   text NOT NULL,
    description_raw   text,
    status_raw        text,
    status            text NOT NULL CHECK (status IN ('completed', 'in_progress', 'planned', 'dropped')),
    fiscal_year       integer,
    fiscal_year_raw   text,
    start_date        date,
    start_date_raw    text,
    start_precision   text,                 -- day | month | season | year | fiscal_year
    end_date          date,
    end_date_raw      text,
    end_precision     text,
    work_year         integer,              -- year used for map colour
    contractor_raw    text,
    contractor        text,                 -- raw, with Undefined/Unidentified/TBD treated as empty
    vendor_id         integer REFERENCES vendor(id),
    project_id        integer REFERENCES project(id),
    funding_program   text,                 -- city Category, e.g. "Prop 101 Arterial"
    funding_detail    text,                 -- city Fund
    record_link       text,                 -- city project page, when the source gives one
    length_mi         double precision,
    source_edited_at  timestamptz,
    raw               jsonb NOT NULL,       -- every attribute the source sent
    geom              geometry(MultiLineString, 4326),
    geom_m            geometry(MultiLineString, 26912),
    UNIQUE (source, source_record_id)
);
CREATE INDEX IF NOT EXISTS work_event_geom_m_idx ON work_event USING gist (geom_m);

CREATE TABLE IF NOT EXISTS segment_event (
    segment_id       integer NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
    work_event_id    bigint NOT NULL REFERENCES work_event(id) ON DELETE CASCADE,
    overlap_m        double precision NOT NULL,
    segment_fraction double precision NOT NULL,  -- share of the block the record covers
    name_match       boolean,                    -- null when the source names no street
    match_confidence text NOT NULL CHECK (match_confidence IN ('high', 'medium', 'low', 'partial')),
    PRIMARY KEY (segment_id, work_event_id)
);
CREATE INDEX IF NOT EXISTS segment_event_event_idx ON segment_event (work_event_id);

-- Phase 3. Dollars live per contract and per job order, never per segment.
CREATE TABLE IF NOT EXISTS contract (
    id              serial PRIMARY KEY,
    agency          text NOT NULL,
    contract_number text NOT NULL,
    title           text,
    ceiling_amount  numeric(14, 2),
    term_start      date,
    term_end        date,
    document_link   text,
    board_item_link text,
    UNIQUE (agency, contract_number)
);

CREATE TABLE IF NOT EXISTS project_contract (
    project_id  integer NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    contract_id integer NOT NULL REFERENCES contract(id) ON DELETE CASCADE,
    confidence  text NOT NULL CHECK (confidence IN ('confirmed', 'inferred', 'unknown')),
    evidence    text,
    PRIMARY KEY (project_id, contract_id)
);

CREATE TABLE IF NOT EXISTS job_order (
    id            serial PRIMARY KEY,
    contract_id   integer NOT NULL REFERENCES contract(id) ON DELETE CASCADE,
    project_id    integer NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    vendor_id     integer REFERENCES vendor(id),
    amount        numeric(14, 2) NOT NULL,
    scope         text,
    document_link text,
    confidence    text NOT NULL DEFAULT 'confirmed' CHECK (confidence IN ('confirmed', 'inferred', 'unknown'))
);

-- Polygons used only to label who looks after a road.
CREATE TABLE IF NOT EXISTS jurisdiction (
    id    serial PRIMARY KEY,
    kind  text NOT NULL,                    -- municipal | tribal
    name  text NOT NULL,
    geom_m geometry(MultiPolygon, 26912) NOT NULL
);
CREATE INDEX IF NOT EXISTS jurisdiction_geom_idx ON jurisdiction USING gist (geom_m);

-- One row per pipeline run, with the match-rate report.
CREATE TABLE IF NOT EXISTS pipeline_run (
    id          bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    report      jsonb
);

-- The two umbrella paving agreements named in the spec. Not yet linked to any project.
INSERT INTO contract (agency, contract_number, title, ceiling_amount, term_end) VALUES
    ('Pima County', 'SC2400001678', 'Pavement job order contract', 35000000, '2027-09-04'),
    ('Pima County', 'MA-PO-20-41', 'Pavement job order contract (five firms)', 5000000, NULL)
ON CONFLICT (agency, contract_number) DO NOTHING;
