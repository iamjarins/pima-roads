"""Step 8. Build static vector tiles straight from the database. No tile server is needed at this size."""
import math
import os
import shutil

from .config import TILE_DIR, TILE_MAX_ZOOM, TILE_MIN_ZOOM

TILE_SQL = """
WITH b AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS env),
m AS (
    SELECT s.id, s.owner, s.last_work_year AS yr,
           (s.upcoming_count > 0)::int AS up, s.is_major::int AS mj,
           ST_AsMVTGeom(CASE WHEN %(z)s < 13 THEN ST_Simplify(s.geom_3857, %(tol)s) ELSE s.geom_3857 END,
                        b.env, 4096, 64, true) AS g
    FROM segment s, b
    WHERE s.geom_3857 && ST_Expand(b.env, %(pad)s)
      -- zoomed out: major roads, then roads with work. Every road from zoom 12.
      AND (%(z)s >= 12 OR s.is_major OR (%(z)s >= 10 AND (s.completed_count > 0 OR s.upcoming_count > 0)))
)
SELECT ST_AsMVT(m, 'roads', 4096, 'g', 'id') FROM m WHERE g IS NOT NULL
"""

WORLD = 40075016.68557849


def _tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def build_tiles(conn, out_dir=TILE_DIR):
    """Write {z}/{x}/{y}.pbf for every tile that has a road in it, then swap the directory in one move."""
    out_dir = os.path.abspath(out_dir)
    tmp = out_dir + ".building"
    shutil.rmtree(tmp, ignore_errors=True)
    cur = conn.cursor()
    cur.execute("SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom) FROM segment")
    boxes = cur.fetchall()
    written = size = 0
    for z in range(TILE_MIN_ZOOM, TILE_MAX_ZOOM + 1):
        wanted = set()
        for x0, y0, x1, y1 in boxes:
            (ax, ay), (bx, by) = _tile(x0, y1, z), _tile(x1, y0, z)
            for x in range(ax, bx + 1):
                for y in range(ay, by + 1):
                    wanted.add((x, y))
        res = WORLD / (256 * 2 ** z)  # metres per pixel
        for x, y in sorted(wanted):
            cur.execute(TILE_SQL, {"z": z, "x": x, "y": y, "tol": res * 1.5, "pad": res * 4})
            data = cur.fetchone()[0]
            if not data:
                continue
            path = os.path.join(tmp, str(z), str(x))
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, f"{y}.pbf"), "wb") as f:
                f.write(bytes(data))
            written += 1
            size += len(data)
    old = out_dir + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(out_dir):
        os.rename(out_dir, old)
    os.rename(tmp, out_dir)
    shutil.rmtree(old, ignore_errors=True)
    return {"tiles": written, "bytes": size, "min_zoom": TILE_MIN_ZOOM, "max_zoom": TILE_MAX_ZOOM}
