"""Step 1. Page through each layer's REST query endpoint and keep the raw response."""
import gzip
import hashlib
import json
import time

import requests

from .config import SOURCES

PAGE = 1000
HEADERS = {"User-Agent": "road-work-history-map/1.0"}


def _get(url, params, tries=5):
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"{url}: {data['error']}")
            return data
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def fetch_layer(url):
    """Return (features, expected_count). Features are GeoJSON in EPSG:4326."""
    expected = _get(f"{url}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    features, offset = [], 0
    while True:
        page = _get(f"{url}/query", {
            "where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326,
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE,
        })
        got = page.get("features", [])
        features.extend(got)
        offset += len(got)
        if not got or (len(got) < PAGE and not page.get("exceededTransferLimit")
                       and not page.get("properties", {}).get("exceededTransferLimit")):
            break
    if len(features) != expected:
        raise RuntimeError(f"{url}: pulled {len(features)} features but the layer reports {expected}")
    return features, expected


def canonical_bytes(features):
    return json.dumps({"type": "FeatureCollection", "features": features},
                      sort_keys=True, separators=(",", ":")).encode()


def pull(conn, source, features=None):
    """Pull one source and store a snapshot row. Returns (snapshot_id, features, changed).

    Identical payloads are not stored twice: the new row points at the earlier one.
    """
    if features is None:
        features, _ = fetch_layer(SOURCES[source]["url"])
    raw = canonical_bytes(features)
    digest = hashlib.sha256(raw).hexdigest()
    with conn.cursor() as cur:
        cur.execute("""SELECT coalesce(same_as, id) FROM source_snapshot
                       WHERE source = %s AND sha256 = %s ORDER BY id LIMIT 1""", (source, digest))
        prior = cur.fetchone()
        cur.execute("""INSERT INTO source_snapshot (source, source_url, row_count, sha256, same_as, payload_gz)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (source, SOURCES[source]["url"], len(features), digest,
                     prior[0] if prior else None, None if prior else gzip.compress(raw, 6)))
        snapshot_id = cur.fetchone()[0]
    return snapshot_id, features, prior is None


def load_snapshot(conn, snapshot_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.payload_gz FROM source_snapshot s
                       JOIN source_snapshot p ON p.id = coalesce(s.same_as, s.id) WHERE s.id = %s""", (snapshot_id,))
        row = cur.fetchone()
    return json.loads(gzip.decompress(row[0]))["features"] if row else None
