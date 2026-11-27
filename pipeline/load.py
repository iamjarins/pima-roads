"""Steps 2 and 3. Normalize the pulled features and load the base network and the work events."""
import json
from datetime import date

from psycopg.types.json import Jsonb

from . import normalize as N
from .config import METRIC_SRID, record_url

SRID = METRIC_SRID


def _geojson(feature):
    g = feature.get("geometry")
    return json.dumps(g) if g and g.get("coordinates") else None


# ---------------------------------------------------------------- base network

def load_base_network(conn, feats):
    """feats: {source: features}. Rebuilds segment and jurisdiction from the four base layers."""
    cur = conn.cursor()
    cur.execute("TRUNCATE segment CASCADE; TRUNCATE jurisdiction;")

    # Jurisdiction polygons.
    for kind, key in (("municipal", "jurisdictions"), ("tribal", "indian_nations")):
        for f in feats[key]:
            g = _geojson(f)
            if g:
                cur.execute(f"""INSERT INTO jurisdiction (kind, name, geom_m) VALUES
                    (%s, %s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {SRID})), 3)))""",
                            (kind, N.TRIBAL_NAMES.get(f["properties"]["NAME"], f["properties"]["NAME"]), g))
    # Big polygons make point-in-polygon slow; cut them up once.
    cur.execute("""CREATE TEMP TABLE jur ON COMMIT DROP AS
                   SELECT kind, name, ST_Subdivide(geom_m, 256) AS geom_m FROM jurisdiction;
                   CREATE INDEX ON jur USING gist (geom_m);""")

    # Street centerlines, one row per block between intersections.
    rows = []
    for f in feats["county_streets"]:
        p, g = f["properties"], _geojson(f)
        if not g:
            continue
        street = N.clean(p.get("STREET"))
        rows.append((p["ROADID"], N.display_name(street), street, N.base_name(p.get("STNAMETYPE") or street),
                     p.get("ROADCAT_CD"), p.get("ROADCAT_DS"), p.get("ADD_MIN") or None, p.get("ADD_MAX") or None,
                     p.get("FINT"), p.get("TINT"), g))
    cur.execute("""CREATE TEMP TABLE stg_street (id int, name text, name_raw text, name_base text, cat_cd text, cat text,
                   add_min int, add_max int, fint int, tint int, gj text) ON COMMIT DROP""")
    with cur.copy("COPY stg_street FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    cur.execute(f"""
        INSERT INTO segment (id, name, name_raw, name_base, owner, owner_name, owner_basis, road_category, is_major,
                             add_min, add_max, from_node, to_node, geom, geom_m, geom_3857, length_m)
        SELECT id, name, name_raw, name_base, 'unknown', 'Not known', 'none', cat,
               cat_cd IN ('LRD_MJ', 'INTERSTATE', 'ST_RT', 'FRNT_RD', 'RMP_INT', 'RMP_STRT', 'RMP_RDMJ'),
               add_min, add_max, fint, tint, g, ST_Transform(g, {SRID}), ST_Transform(g, 3857),
               ST_Length(ST_Transform(g, {SRID}))
        FROM (SELECT *, ST_SetSRID(ST_GeomFromGeoJSON(gj), 4326) AS g FROM stg_street) s
        WHERE ST_GeometryType(g) = 'ST_LineString'""")

    # Cross streets: the other street names meeting at each end of the block.
    cur.execute("""
        WITH ends AS (
            SELECT from_node AS node, id, name FROM segment UNION ALL SELECT to_node, id, name FROM segment),
        node AS (SELECT node, array_agg(DISTINCT name) AS names, count(DISTINCT id) AS degree FROM ends GROUP BY node),
        d AS (
            SELECT s.id,
                   (SELECT string_agg(n, ' / ' ORDER BY n) FROM unnest(f.names) n WHERE n <> s.name AND n <> 'Unnamed road') AS f_names, f.degree AS f_deg,
                   (SELECT string_agg(n, ' / ' ORDER BY n) FROM unnest(t.names) n WHERE n <> s.name AND n <> 'Unnamed road') AS t_names, t.degree AS t_deg
            FROM segment s LEFT JOIN node f ON f.node = s.from_node LEFT JOIN node t ON t.node = s.to_node)
        UPDATE segment s SET
            from_desc = coalesce(d.f_names, CASE WHEN d.f_deg <= 1 THEN 'End of road' END),
            to_desc   = coalesce(d.t_names, CASE WHEN d.t_deg <= 1 THEN 'End of road' END)
        FROM d WHERE d.id = s.id""")

    _tag_owners(cur, feats)
    cur.execute("ANALYZE segment")


def _stage_lines(cur, table, feats, value_of):
    cur.execute(f"CREATE TEMP TABLE {table} (val text, name text, gj text) ON COMMIT DROP")
    with cur.copy(f"COPY {table} FROM STDIN") as cp:
        for f in feats:
            g, (val, name) = _geojson(f), value_of(f["properties"])
            if g and val:
                cp.write_row((val, name, g))
    cur.execute(f"""ALTER TABLE {table} ADD COLUMN geom_m geometry;
                    UPDATE {table} SET geom_m = ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(gj), 4326), {SRID});
                    CREATE INDEX ON {table} USING gist (geom_m); ANALYZE {table};""")


def _apply_overlap(cur, table, mapping, basis):
    """Give each still-unknown segment the value of the staged lines that cover at least half of it."""
    cur.execute(f"""
        CREATE TEMP TABLE own_{table} ON COMMIT DROP AS
        SELECT DISTINCT ON (id) id, val FROM (
            SELECT s.id, m.val, sum(ST_Length(ST_Intersection(s.geom_m, ST_Buffer(m.geom_m, 12, 'endcap=flat')))) / nullif(s.length_m, 0) AS frac
            FROM segment s JOIN {table} m ON ST_DWithin(s.geom_m, m.geom_m, 12)
            WHERE s.owner = 'unknown'
            GROUP BY s.id, m.val, s.length_m) x
        WHERE frac >= 0.5 ORDER BY id, frac DESC""")
    for code, (owner, name) in mapping.items():
        cur.execute(f"""UPDATE segment s SET owner = %s, owner_name = %s, owner_basis = %s
                        FROM own_{table} o WHERE o.id = s.id AND o.val = %s AND s.owner = 'unknown'""",
                    (owner, name, basis, code))


def _tag_owners(cur, feats):
    """Who maintains each block. Strongest evidence first; owner_basis records which rule fired."""
    # 1. Interstates, state routes and their ramps belong to the state.
    cur.execute("""UPDATE segment SET owner = 'state', owner_name = 'Arizona Department of Transportation',
                   owner_basis = 'County street network lists this as ' || lower(road_category)
                   WHERE road_category IN ('Interstate highway', 'Interstate ramp', 'State route', 'State route ramp')""")
    # 2. County maintenance records name the maintaining agency.
    _stage_lines(cur, "stg_maint", feats["county_maintenance"],
                 lambda p: (N.clean(p.get("MNT_PRI")), p.get("RouteId")))
    _apply_overlap(cur, "stg_maint", N.MAINT_CODES, "Pima County Street Maintenance layer")
    # 3. City pavement inventory names the custodian.
    _stage_lines(cur, "stg_pave", feats["city_pavement_major"] + feats["city_pavement_minor"],
                 lambda p: (N.clean(p.get("Custodian")) if p.get("Status") in (None, "Active") else None, p.get("StreetName")))
    _apply_overlap(cur, "stg_pave", N.CUSTODIANS, "City of Tucson pavement inventory")
    # 4. Tribal land.
    cur.execute("""UPDATE segment s SET owner = 'tribal', owner_name = j.name,
                   owner_basis = 'Located on tribal land; maintenance not confirmed'
                   FROM jur j WHERE s.owner = 'unknown' AND j.kind = 'tribal'
                     AND ST_Intersects(j.geom_m, ST_LineInterpolatePoint(s.geom_m, 0.5))""")
    cur.execute("""UPDATE segment SET owner = 'tribal', owner_name = 'Tribal route',
                   owner_basis = 'County street network lists this as indian route'
                   WHERE owner = 'unknown' AND road_category = 'Indian route'""")
    # 5. Other towns: no inventory is loaded for them, so the label comes from location alone.
    for name, (owner, label) in N.TOWNS.items():
        if owner == "town":
            cur.execute("""UPDATE segment s SET owner = 'town', owner_name = %s,
                           owner_basis = 'Located inside the town limits; maintenance not confirmed'
                           FROM jur j WHERE s.owner = 'unknown' AND j.kind = 'municipal' AND j.name = %s
                             AND ST_Intersects(j.geom_m, ST_LineInterpolatePoint(s.geom_m, 0.5))""", (label, name))
    # 6. What is left appears in no public maintenance inventory.
    cur.execute("""UPDATE segment s SET owner_name = 'Not in a public maintenance inventory',
                   owner_basis = CASE WHEN j.name = 'TUCSON'
                       THEN 'Inside Tucson city limits but not in the city pavement inventory. It may be private or state-maintained.'
                       ELSE 'In unincorporated Pima County but not listed as county-maintained. It may be private.' END
                   FROM jur j WHERE s.owner = 'unknown' AND j.kind = 'municipal'
                     AND ST_Intersects(j.geom_m, ST_LineInterpolatePoint(s.geom_m, 0.5))""")
    cur.execute("UPDATE segment SET owner_basis = 'No maintenance record found' WHERE owner_basis = 'none'")


# ---------------------------------------------------------------- work events

def _vendor_id(cur, cache, raw):
    name = N.vendor_name(raw)
    if not name:
        return None
    if name not in cache:
        aliases = sorted(set(N.VENDOR_ALIASES.get(name, [])) - {name})
        cur.execute("""INSERT INTO vendor (canonical_name, aliases) VALUES (%s, %s)
                       ON CONFLICT (canonical_name) DO UPDATE SET aliases = EXCLUDED.aliases RETURNING id""",
                    (name, aliases))
        cache[name] = cur.fetchone()[0]
    return cache[name]


def _project_id(cur, cache, source, name, parsed=None, category=None, funding=None, link=None):
    name = N.clean(name)
    if not name:
        return None
    key = (source, name)
    if key not in cache:
        cur.execute("""INSERT INTO project (source, name, parsed_code, category, funding_source, link)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (source, name) DO UPDATE SET parsed_code = EXCLUDED.parsed_code,
                           category = EXCLUDED.category, funding_source = EXCLUDED.funding_source, link = EXCLUDED.link
                       RETURNING id""",
                    (source, name, Jsonb(parsed) if parsed else None, category, funding, link))
        cache[key] = cur.fetchone()[0]
    return cache[key]


def county_event(p, today):
    fy = N.fiscal_year(p.get("Fiscal_Year"))
    start, end = N.epoch_ms_date(p.get("Start_Date")), N.epoch_ms_date(p.get("End_Date"))
    category, label = N.treatment(p.get("Treatment"))
    status = N.county_status(p.get("Status"), end, fy, today)
    frm, to = N.clean(p.get("From_")), N.clean(p.get("To_"))
    return dict(
        street_raw=N.clean(p.get("Street_Name")),
        location_raw=" to ".join(x for x in (frm, to) if x) or None,
        treatment_raw=p.get("Treatment"), treatment_category=category, treatment_label=label,
        description_raw=None, status_raw=p.get("Status"), status=status,
        fiscal_year=fy, fiscal_year_raw=p.get("Fiscal_Year"),
        start_date=start, start_date_raw=None if p.get("Start_Date") is None else str(p["Start_Date"]),
        start_precision="day" if start else None,
        end_date=end, end_date_raw=None if p.get("End_Date") is None else str(p["End_Date"]),
        end_precision="day" if end else None,
        work_year=(end.year if end else fy) if status == "completed" else None,
        contractor_raw=p.get("Contractor"), contractor=N.blank_if_placeholder(p.get("Contractor")),
        funding_program=None, funding_detail=None, record_link=None,
        length_mi=p.get("Length_mi"), source_edited_at=N.epoch_ms_ts(p.get("last_edited_date")),
    )


CITY_TYPES = ("Surface Maintenance", "Roadway")  # road surface work only


def city_event(p):
    def when(text, epoch):
        d, prec = N.parse_text_date(text)
        if d is None and epoch is not None:
            d, prec = N.epoch_ms_date(epoch), "day"
        return d, prec

    # Construction dates win over FY_Planned, which can disagree with them.
    start, sp = when(p.get("ConstructionBegin"), p.get("Con_Begin_Date"))
    end, ep = when(p.get("ConstructionFinish"), p.get("Con_Finish_Date"))
    fy = N.fiscal_year(p.get("FY_Planned"))
    category, label = N.treatment(p.get("Treatment"), p.get("Description"))
    status = N.city_status(p.get("Status"), p.get("ProjectStatus"))
    year = (end or start).year if (end or start) else fy
    fund = N.blank_if_placeholder(p.get("Fund"))
    return dict(
        street_raw=None, location_raw=N.clean(p.get("Location")),
        treatment_raw=p.get("Treatment"), treatment_category=category, treatment_label=label,
        description_raw=N.blank_if_placeholder(p.get("Description")),
        status_raw=p.get("Status"), status=status,
        fiscal_year=fy, fiscal_year_raw=p.get("FY_Planned"),
        start_date=start, start_date_raw=N.clean(p.get("ConstructionBegin")), start_precision=sp,
        end_date=end, end_date_raw=N.clean(p.get("ConstructionFinish")), end_precision=ep,
        work_year=year if status == "completed" else None,
        contractor_raw=p.get("Contractor"), contractor=N.blank_if_placeholder(p.get("Contractor")),
        funding_program=N.blank_if_placeholder(p.get("Category")),
        funding_detail=None if fund in (None, "No Fund Indicated") else fund,
        record_link=N.clean(p.get("URL")),
        length_mi=(p["Length_ft"] / 5280.0) if p.get("Length_ft") else None,
        source_edited_at=None,
    )


COLUMNS = ["source", "source_record_id", "source_url", "snapshot_id", "street_raw", "location_raw", "treatment_raw",
           "treatment_category", "treatment_label", "description_raw", "status_raw", "status", "fiscal_year",
           "fiscal_year_raw", "start_date", "start_date_raw", "start_precision", "end_date", "end_date_raw",
           "end_precision", "work_year", "contractor_raw", "contractor", "vendor_id", "project_id", "funding_program",
           "funding_detail", "record_link", "length_mi", "source_edited_at", "raw"]


def load_events(conn, feats, snapshots, today=None):
    """One work_event per source record. Rows are only dropped when they repeat the same source record."""
    today = today or date.today()
    cur = conn.cursor()
    cur.execute("TRUNCATE work_event CASCADE")
    vendors, projects, seen, counts = {}, {}, set(), {}
    for source in ("county_work", "city_projects"):
        loaded = skipped = 0
        for f in feats[source]:
            p = f["properties"]
            if source == "city_projects" and p.get("Type") not in CITY_TYPES:
                skipped += 1
                continue
            rid = str(p["OBJECTID"])
            if (source, rid) in seen:
                continue
            seen.add((source, rid))
            if source == "county_work":
                e = county_event(p, today)
                pid = _project_id(cur, projects, source, p.get("Project_Name"), N.parse_county_project(p.get("Project_Name")))
            else:
                e = city_event(p)
                pid = _project_id(cur, projects, source, p.get("ProjectName"), None, e["funding_program"],
                                  e["funding_detail"], e["record_link"])
            e.update(source=source, source_record_id=rid, source_url=record_url(source, rid), snapshot_id=snapshots[source],
                     vendor_id=_vendor_id(cur, vendors, p.get("Contractor")), project_id=pid, raw=Jsonb(p))
            cur.execute(
                f"""INSERT INTO work_event ({', '.join(COLUMNS)}, geom)
                    VALUES ({', '.join(['%s'] * len(COLUMNS))},
                            ST_Multi(ST_CollectionExtract(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 2)))""",
                [e[c] for c in COLUMNS] + [_geojson(f)])
            loaded += 1
        counts[source] = {"source_rows": len(feats[source]), "loaded": loaded, "left_out_non_paving": skipped}
    cur.execute(f"UPDATE work_event SET geom_m = ST_Transform(geom, {SRID}) WHERE geom IS NOT NULL")
    cur.execute("ANALYZE work_event")
    return counts
