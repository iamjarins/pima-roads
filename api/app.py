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

from pipeline.config import DATABASE_URL

from . import history as H

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


# ---------------------------------------------------------------- endpoints

@app.get("/api/meta")
def meta():
    return H.build_meta(q)


def _segment(segment_id):
    rows = q(H.SEGMENT_SQL.format(where="WHERE id = %s"), (segment_id,))
    if not rows:
        raise HTTPException(404, "No such road segment")
    return rows[0]


@app.get("/api/segments/{segment_id}")
def segment(segment_id: int):
    return _segment(segment_id)


@app.get("/api/segments/{segment_id}/events")
def segment_events(segment_id: int):
    seg = _segment(segment_id)
    events = q(H.EVENTS_SQL.format(where="AND se.segment_id = %s"), (segment_id,))
    return H.build_history(seg, events, H.cost_links(q), H.last_pulls(q))



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
