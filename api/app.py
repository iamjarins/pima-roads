"""Read-only history API. It reads the database only; nothing here calls a government server.

    GET /api/meta                    sources, when each was last pulled, treatment glossary
    GET /api/segments/{id}           header for one block of road
    GET /api/segments/{id}/events    its work history
    GET /api/search?q=               street name or address

The same process serves the static front end and the tiles, so one small host is enough.
"""
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from pipeline import normalize as N
from pipeline.config import COVERAGE_NOTE, DATABASE_URL, SOURCES, WORK_SOURCES, layer_url

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})


@asynccontextmanager
async def lifespan(app):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="Road Work History Map", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)


def q(sql, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------- wording

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def say_date(d, precision, raw=None):
    """A date in the precision the source gave it. Never more exact than the record."""
    if d is None:
        return None
    if precision == "day":
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    if precision == "month":
        return f"{MONTHS[d.month - 1]} {d.year}"
    if precision == "season":
        return (raw or str(d.year)).lower()
    if precision == "fiscal_year":
        return f"fiscal year {d.year}"
    return str(d.year)


def say_when(e):
    start = say_date(e["start_date"], e["start_precision"], e["start_date_raw"])
    end = say_date(e["end_date"], e["end_precision"], e["end_date_raw"])
    fy = f"fiscal year {e['fiscal_year']}" if e["fiscal_year"] else None
    exact = lambda p: p in ("day", "month")
    if e["status"] == "completed":
        if end and start and exact(e["start_precision"]) and exact(e["end_precision"]):
            return f"{start} to {end}"
        if end and exact(e["end_precision"]):
            return f"Finished {end}"
        if end or start:
            return f"Done in {end or start}. The source gives no exact date."
        return f"Done in {fy}. The source gives no exact dates." if fy else "Date not recorded"
    if e["status"] == "in_progress":
        parts = [f"Started {start}" if start else None, f"expected to finish {end}" if end else None]
        text = ", ".join(p for p in parts if p)
        return (text[0].upper() + text[1:]) if text else (f"Scheduled for {fy}" if fy else "Dates not recorded")
    if start and end:
        return f"Planned for {start} to {end}"
    if start or end:
        return f"Planned to start {start}" if start else f"Planned to finish {end}"
    return f"Planned for {fy}" if fy else "No date set"


STATUS_LABEL = {"completed": "Completed", "in_progress": "Under construction", "planned": "Planned"}

CONFIDENCE_NOTE = {
    "high": None,
    "medium": None,
    "partial": "This record covers only part of this block.",
    "low": "The record names a different street than this block. It was matched by location only and may not belong here.",
}


def money(amount):
    a = float(amount)
    if a >= 1e6:
        return f"${a / 1e6:.1f} million".replace(".0 million", " million")
    return f"${a:,.0f}"


def cost_for(e, links):
    """Cost is per job or per contract, never per segment, and only when a document links it."""
    job = links["jobs"].get(e["project_id"])
    if job:
        return {
            "status": "linked", "confidence": job["confidence"],
            "text": f"Part of a {money(job['amount'])} job covering {job['segments']} segments and {job['miles']:.1f} miles.",
            "contract_number": job["contract_number"], "document_link": job["document_link"],
        }
    c = links["contracts"].get(e["project_id"])
    if c:
        ceiling = f" with a ceiling of {money(c['ceiling_amount'])}" if c["ceiling_amount"] else ""
        return {
            "status": "contract_only", "confidence": c["confidence"],
            "text": f"Done under contract {c['contract_number']}, a shared agreement{ceiling}. The cost of this job is not yet linked.",
            "contract_number": c["contract_number"], "document_link": c["document_link"],
        }
    return {"status": "not_linked", "text": "Cost not yet linked"}


# ---------------------------------------------------------------- endpoints

@app.get("/api/meta")
def meta():
    pulls = {r["source"]: r for r in q("""
        SELECT DISTINCT ON (source) source, pulled_at, row_count FROM source_snapshot ORDER BY source, pulled_at DESC""")}
    years = q("SELECT min(last_work_year) AS lo, max(last_work_year) AS hi FROM segment")[0]
    return {
        "sources": [{"key": k, "title": SOURCES[k]["title"], "owner": SOURCES[k]["owner"], "url": layer_url(k),
                     "pulled_at": pulls[k]["pulled_at"] if k in pulls else None,
                     "row_count": pulls[k]["row_count"] if k in pulls else None,
                     "carries_work_history": k in WORK_SOURCES} for k in SOURCES],
        "coverage": COVERAGE_NOTE,
        "years": years,
        "treatments": [{"category": k, "name": v[0], "explanation": v[1]} for k, v in N.TREATMENTS.items()],
    }


def _segment(segment_id):
    rows = q("""
        SELECT id, name, from_desc, to_desc, owner, owner_name, owner_basis, road_category,
               round((length_m / 1609.344)::numeric, 2)::float AS length_mi,
               last_work_year, completed_count, upcoming_count,
               ST_AsGeoJSON(geom, 6)::json AS geometry,
               ARRAY[ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)] AS bbox
        FROM segment WHERE id = %s""", (segment_id,))
    if not rows:
        raise HTTPException(404, "No such road segment")
    return rows[0]


@app.get("/api/segments/{segment_id}")
def segment(segment_id: int):
    return _segment(segment_id)


@app.get("/api/segments/{segment_id}/events")
def segment_events(segment_id: int):
    seg = _segment(segment_id)
    events = q("""
        SELECT e.*, se.match_confidence, se.segment_fraction,
               p.name AS project_name, p.parsed_code AS project_code, p.link AS project_link,
               v.canonical_name AS vendor_name, snap.pulled_at,
               (SELECT count(*) FROM record_change rc WHERE rc.source = e.source AND rc.source_record_id = e.source_record_id
                    AND rc.change_type = 'modified') AS times_edited
        FROM segment_event se
        JOIN work_event e ON e.id = se.work_event_id
        JOIN source_snapshot snap ON snap.id = e.snapshot_id
        LEFT JOIN project p ON p.id = e.project_id
        LEFT JOIN vendor v ON v.id = e.vendor_id
        WHERE se.segment_id = %s AND e.status <> 'dropped'
        ORDER BY coalesce(e.end_date, e.start_date, make_date(coalesce(e.fiscal_year, 1900), 6, 30)) DESC, e.id""",
               (segment_id,))
    project_ids = [e["project_id"] for e in events if e["project_id"]]
    links = {"jobs": {}, "contracts": {}}
    if project_ids:
        for r in q("""
            SELECT j.project_id, sum(j.amount) AS amount, min(j.confidence) AS confidence,
                   min(c.contract_number) AS contract_number, min(coalesce(j.document_link, c.document_link)) AS document_link,
                   (SELECT count(DISTINCT se.segment_id) FROM work_event w JOIN segment_event se ON se.work_event_id = w.id
                     WHERE w.project_id = j.project_id) AS segments,
                   (SELECT coalesce(sum(w.length_mi), 0) FROM work_event w WHERE w.project_id = j.project_id)::float AS miles
            FROM job_order j JOIN contract c ON c.id = j.contract_id
            WHERE j.project_id = ANY(%s) GROUP BY j.project_id""", (project_ids,)):
            links["jobs"][r["project_id"]] = r
        for r in q("""
            SELECT DISTINCT ON (pc.project_id) pc.project_id, pc.confidence, c.contract_number, c.ceiling_amount, c.document_link
            FROM project_contract pc JOIN contract c ON c.id = pc.contract_id
            WHERE pc.project_id = ANY(%s) ORDER BY pc.project_id, pc.confidence""", (project_ids,)):
            links["contracts"][r["project_id"]] = r

    completed, upcoming = [], []
    for e in events:
        name, explanation = N.TREATMENTS[e["treatment_category"]]
        src = SOURCES[e["source"]]
        funding = None
        if e["funding_program"] or e["funding_detail"]:
            funding = {"program": e["funding_program"], "detail": e["funding_detail"]}
        stretch = " ".join(x for x in (N.display_name(e["street_raw"]) if e["street_raw"] else None,) if x)
        if e["location_raw"]:
            stretch = f"{stretch}: {e['location_raw']}" if stretch else e["location_raw"]
        item = {
            "id": e["id"],
            "status": e["status"],
            "status_label": STATUS_LABEL[e["status"]],
            "status_raw": e["status_raw"],
            "when": say_when(e),
            "year": e["work_year"] or (e["end_date"] or e["start_date"]).year if (e["work_year"] or e["end_date"] or e["start_date"]) else e["fiscal_year"],
            "fiscal_year": e["fiscal_year"],
            "treatment": {"label": e["treatment_label"], "category": e["treatment_category"], "category_name": name,
                          "explanation": explanation, "raw": e["treatment_raw"], "description": e["description_raw"]},
            "contractor": {"name": e["contractor"], "raw": e["contractor_raw"], "vendor": e["vendor_name"]},
            "project": {"name": e["project_name"], "code": e["project_code"], "link": e["project_link"]},
            "funding": funding,
            "stretch": stretch or None,
            "length_mi": round(e["length_mi"], 2) if e["length_mi"] else None,
            "cost": cost_for(e, links),
            "match": {"confidence": e["match_confidence"], "note": CONFIDENCE_NOTE[e["match_confidence"]]},
            "source": {"key": e["source"], "title": src["title"], "owner": src["owner"], "record_url": e["source_url"],
                       "layer_url": layer_url(e["source"]), "pulled_at": e["pulled_at"], "record_edited_at": e["source_edited_at"],
                       "times_edited_since_first_pull": e["times_edited"]},
        }
        (completed if e["status"] == "completed" else upcoming).append(item)
    upcoming.sort(key=lambda i: (i["status"] != "in_progress", i["year"] or 9999))

    # An empty history never reads "never repaired": say what the records cover instead.
    notes = [COVERAGE_NOTE[k] for k in (("county", "city") if seg["owner"] not in ("county", "city") else (seg["owner"],))]
    if seg["owner"] not in ("county", "city"):
        notes = [f"Only Pima County and City of Tucson records are loaded. This road is listed under: {seg['owner_name']}."] + notes
    pulls = q("""SELECT DISTINCT ON (source) source, pulled_at FROM source_snapshot
                 WHERE source = ANY(%s) ORDER BY source, pulled_at DESC""", (list(WORK_SOURCES),))
    return {
        "segment_id": segment_id,
        "completed": completed,
        "upcoming": upcoming,
        "empty_message": None if completed else "No work on record.",
        "coverage_notes": notes,
        "sources_checked": [{"key": p["source"], "title": SOURCES[p["source"]]["title"], "url": layer_url(p["source"]),
                             "pulled_at": p["pulled_at"]} for p in pulls],
    }


_TYPE_WORDS = {"BLVD": "BL", "BOULEVARD": "BL", "AVE": "AV", "AVENUE": "AV", "STREET": "ST", "ROAD": "RD", "DRIVE": "DR",
               "LANE": "LN", "PLACE": "PL", "WAY": "WY", "TRAIL": "TR", "TRL": "TR", "CIRCLE": "CI", "CIR": "CI",
               "COURT": "CT", "LOOP": "LP", "HIGHWAY": "HY", "HWY": "HY", "PARKWAY": "PW", "PKWY": "PW", "TERRACE": "TE",
               "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "SAINT": "ST"}


@app.get("/api/search")
def search(q_: str = Query("", alias="q", max_length=80), limit: int = Query(8, ge=1, le=20)):
    """Street name ("Speedway", "N Silverbell Rd") or address ("1500 W Speedway Bl")."""
    tokens = [_TYPE_WORDS.get(t, t) for t in re.sub(r"[^A-Za-z0-9 ]", " ", q_.upper()).split()]
    number = None
    if len(tokens) > 1 and tokens[0].isdigit() and not re.fullmatch(r"\d+(ST|ND|RD|TH)", tokens[1]):
        number, tokens = int(tokens[0]), tokens[1:]
    if not tokens or (len(tokens) == 1 and len(tokens[0]) < 2 and not tokens[0].isdigit()):
        return {"results": []}
    # every word must appear as the start of a word in the street name
    pattern = "".join(f"(?=.*\\m{re.escape(t)})" for t in tokens)
    name = " ".join(tokens)
    results = []
    if number is not None:
        for r in q("""
            SELECT id, name, ST_X(pt) AS lon, ST_Y(pt) AS lat FROM (
                SELECT id, name, similarity(upper(name), %(name)s) AS sim,
                       ST_LineInterpolatePoint(geom, 0.5) AS pt
                FROM segment WHERE upper(name) ~ %(pattern)s AND %(number)s BETWEEN add_min AND add_max AND add_max > 0) s
            ORDER BY sim DESC, id LIMIT %(limit)s""", {"name": name, "pattern": pattern, "number": number, "limit": limit}):
            results.append({"type": "address", "label": f"{number} {r['name']}", "segment_id": r["id"],
                            "center": [r["lon"], r["lat"]]})
    for r in q("""
        SELECT name, count(*) AS blocks, similarity(upper(name), %(name)s) AS sim,
               ARRAY[ST_XMin(ST_Extent(geom)), ST_YMin(ST_Extent(geom)), ST_XMax(ST_Extent(geom)), ST_YMax(ST_Extent(geom))] AS bbox,
               (array_agg(id ORDER BY length_m DESC))[1] AS sample_id
        FROM segment WHERE upper(name) ~ %(pattern)s AND name <> 'Unnamed road'
        GROUP BY name ORDER BY sim DESC, blocks DESC LIMIT %(limit)s""", {"name": name, "pattern": pattern, "limit": limit}):
        results.append({"type": "street", "label": r["name"], "blocks": r["blocks"], "bbox": r["bbox"],
                        "segment_id": r["sample_id"] if r["blocks"] == 1 else None})
    return {"results": results[:limit]}


@app.get("/api/health")
def health():
    row = q("SELECT max(finished_at) AS last_refresh FROM pipeline_run")[0]
    return {"ok": True, **row}


# ---------------------------------------------------------------- static files

class Tiles(StaticFiles):
    """Tiles change once a night. A missing tile is an empty tile, not an error."""

    async def get_response(self, path, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.responses import Response
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return Response(status_code=204)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp


if os.path.isdir(os.path.join(WEB_DIR, "tiles")):
    app.mount("/tiles", Tiles(directory=os.path.join(WEB_DIR, "tiles")), name="tiles")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
