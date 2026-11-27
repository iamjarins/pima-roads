"""Build the whole site as plain files, so it can be hosted anywhere with no server and no database.

    python -m pipeline.export --out site

Writes the front end, the tiles, and under data/:
    meta.json            sources, pull dates, treatment glossary
    seg/{n}.json         segment header + history for every segment whose id % SHARDS == n
    streets.json         every street name with its extent, for search
    addr/{n}.json        address ranges per street, for address search

The history files are built by the same code as the live API, so both show the same thing.
"""
import argparse
import json
import os
import shutil
import zlib
from collections import defaultdict
from datetime import date, datetime

from psycopg.rows import dict_row

from api import history as H

from . import db
from .config import TILE_DIR

SHARDS = 1024
ADDR_SHARDS = 64
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")

STATIC_CONFIG = """// Written by pipeline.export. Static hosting: history and search are read from pre-built files.
window.ROADWORK_CONFIG = {
  staticData: "data",
  segShards: %d,
  addrShards: %d,
  tiles: "tiles/{z}/{x}/{y}.pbf",
  tileMaxZoom: 13,
  basemapStyle: "https://tiles.openfreemap.org/styles/positron",
  center: [-110.97, 32.24],
  zoom: 11.2,
  bounds: [[-113.6, 31.2], [-110.2, 32.7]],
};
"""


def _json(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "__float__"):
        return float(o)
    raise TypeError(type(o))


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, default=_json, separators=(",", ":"), ensure_ascii=False)


def addr_shard(name):
    """Must match addrShard() in web/app.js."""
    return zlib.crc32(name.encode("utf-8")) % ADDR_SHARDS


def export(out):
    out = os.path.abspath(out)
    conn = db.connect()
    conn.row_factory = dict_row

    def q(sql, params=None):
        return conn.execute(sql, params).fetchall()

    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(WEB_DIR, out, ignore=shutil.ignore_patterns("tiles", "tiles.*", "config.js"))
    shutil.copytree(os.path.abspath(TILE_DIR), os.path.join(out, "tiles"))
    with open(os.path.join(out, "config.js"), "w") as f:
        f.write(STATIC_CONFIG % (SHARDS, ADDR_SHARDS))
    open(os.path.join(out, ".nojekyll"), "w").close()
    data = os.path.join(out, "data")

    _write(os.path.join(data, "meta.json"), H.build_meta(q))

    links, pulls = H.cost_links(q), H.last_pulls(q)
    events = defaultdict(list)
    for e in q(H.EVENTS_SQL.format(where="")):
        events[e["segment_id"]].append(e)
    shards = defaultdict(dict)
    segments = q(H.SEGMENT_SQL.format(where=""))
    for seg in segments:
        shards[seg["id"] % SHARDS][str(seg["id"])] = [seg, H.build_history(seg, events.get(seg["id"], []), links, pulls)]
    for n in range(SHARDS):
        _write(os.path.join(data, "seg", f"{n}.json"), shards.get(n, {}))

    streets = q("""
        SELECT name, count(*) AS blocks,
               round(ST_XMin(ST_Extent(geom))::numeric, 5)::float AS x0, round(ST_YMin(ST_Extent(geom))::numeric, 5)::float AS y0,
               round(ST_XMax(ST_Extent(geom))::numeric, 5)::float AS x1, round(ST_YMax(ST_Extent(geom))::numeric, 5)::float AS y1,
               (array_agg(id ORDER BY length_m DESC))[1] AS sample_id
        FROM segment WHERE name <> 'Unnamed road' GROUP BY name ORDER BY name""")
    _write(os.path.join(data, "streets.json"),
           [[s["name"], s["blocks"], s["x0"], s["y0"], s["x1"], s["y1"], s["sample_id"] if s["blocks"] == 1 else None]
            for s in streets])

    addr = defaultdict(lambda: defaultdict(list))
    for r in q("""SELECT name, add_min, add_max, id,
                         round(ST_X(ST_LineInterpolatePoint(geom, 0.5))::numeric, 5)::float AS lon,
                         round(ST_Y(ST_LineInterpolatePoint(geom, 0.5))::numeric, 5)::float AS lat
                  FROM segment WHERE add_max > 0 AND name <> 'Unnamed road' ORDER BY name, add_min"""):
        addr[addr_shard(r["name"])][r["name"]].append([r["add_min"], r["add_max"], r["id"], r["lon"], r["lat"]])
    for n in range(ADDR_SHARDS):
        _write(os.path.join(data, "addr", f"{n}.json"), addr.get(n, {}))

    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    files = sum(len(fs) for _, _, fs in os.walk(out))
    return {"out": out, "segments": len(segments), "streets": len(streets), "files": files, "megabytes": round(size / 1e6, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site")
    print(json.dumps(export(ap.parse_args().out), indent=2))
