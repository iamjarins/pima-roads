"""The nightly job: pull, snapshot, diff, normalize, match, publish.

    python -m pipeline.run                 # full refresh from the public servers
    python -m pipeline.run --from-dir DIR  # reuse features saved earlier with --save-dir (no network)
    python -m pipeline.run --no-tiles      # skip the tile rebuild

The database part runs in one transaction, so the public map never sees a half-loaded state.
Exits non-zero, changing nothing, if a source cannot be pulled completely.
"""
import argparse
import gzip
import json
import os
import sys
import time

from psycopg.types.json import Jsonb

from . import db, diff, load, match, pull, tiles
from .config import SOURCES, WORK_SOURCES


def _fetch_all(from_dir, save_dir):
    feats = {}
    for key, src in SOURCES.items():
        if from_dir:
            with gzip.open(os.path.join(from_dir, f"{key}.json.gz"), "rt") as f:
                feats[key] = json.load(f)
        else:
            feats[key], _ = pull.fetch_layer(src["url"])
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            with gzip.open(os.path.join(save_dir, f"{key}.json.gz"), "wt") as f:
                json.dump(feats[key], f)
        print(f"  pulled {key}: {len(feats[key])} rows", flush=True)
    return feats


def run(from_dir=None, save_dir=None, build_tiles=True):
    started = time.time()
    print("1. Pull", flush=True)
    feats = _fetch_all(from_dir, save_dir)  # all sources first: a failed pull must not leave a partial refresh

    conn = db.connect()
    db.ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO pipeline_run DEFAULT VALUES RETURNING id")
    run_id = cur.fetchone()[0]

    print("   Snapshot and compare with the last pull", flush=True)
    snapshots, changes = {}, {}
    for key in SOURCES:
        snapshots[key], _, changed = pull.pull(conn, key, feats[key])
        if changed and key in WORK_SOURCES:
            changes[key] = diff.record_changes(conn, key, snapshots[key], feats[key])
        elif key in WORK_SOURCES:
            changes[key] = {"added": 0, "removed": 0, "modified": 0}

    print("2-3. Normalize and build the base network", flush=True)
    load.load_base_network(conn, feats)
    counts = load.load_events(conn, feats, snapshots)
    print("4-6. Match records to segments", flush=True)
    match.match(conn)
    report = match.report(conn)
    for key in WORK_SOURCES:
        report[key].update(counts[key], changes_since_last_pull=changes[key])
    cur.execute("UPDATE pipeline_run SET finished_at = now(), report = %s WHERE id = %s", (Jsonb(report), run_id))
    conn.commit()

    if build_tiles:
        print("8. Publish tiles", flush=True)
        report["tiles"] = tiles.build_tiles(conn)
        cur.execute("UPDATE pipeline_run SET report = %s WHERE id = %s", (Jsonb(report), run_id))
        conn.commit()
    report["seconds"] = round(time.time() - started, 1)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", help="load features from this directory instead of the network")
    ap.add_argument("--save-dir", help="also save the pulled features here")
    ap.add_argument("--no-tiles", action="store_true")
    a = ap.parse_args()
    try:
        run(a.from_dir, a.save_dir, not a.no_tiles)
    except Exception as exc:  # leave yesterday's data in place
        print(f"Refresh failed, nothing was changed: {exc}", file=sys.stderr)
        raise
