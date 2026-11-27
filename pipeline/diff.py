"""Step 7. Compare a pull with the one before it and record what changed in the public record."""
from psycopg.types.json import Jsonb

from .config import SOURCES
from .pull import load_snapshot

IGNORED = {"Shape.STLength()", "SHAPE.STLength()"}


def _index(features, id_field):
    return {str(f["properties"][id_field]): f for f in features}


def record_changes(conn, source, snapshot_id, features):
    """Returns {'added': n, 'removed': n, 'modified': n}. The first pull of a source records nothing."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id FROM source_snapshot WHERE source = %s AND id < %s ORDER BY id DESC LIMIT 1""",
                    (source, snapshot_id))
        row = cur.fetchone()
    counts = {"added": 0, "removed": 0, "modified": 0}
    if not row:
        return counts
    prev_id = row[0]
    old_features = load_snapshot(conn, prev_id)
    if old_features is None:
        return counts
    id_field = SOURCES[source]["id_field"]
    old, new = _index(old_features, id_field), _index(features, id_field)
    rows = []
    for rid in new.keys() - old.keys():
        rows.append((rid, "added", None))
    for rid in old.keys() - new.keys():
        rows.append((rid, "removed", None))
    for rid in new.keys() & old.keys():
        a, b = old[rid], new[rid]
        changes = {k: [a["properties"].get(k), b["properties"].get(k)]
                   for k in (a["properties"].keys() | b["properties"].keys()) - IGNORED
                   if a["properties"].get(k) != b["properties"].get(k)}
        if a.get("geometry") != b.get("geometry"):
            changes["geometry"] = ["changed", "changed"]
        if changes:
            rows.append((rid, "modified", changes))
    with conn.cursor() as cur:
        for rid, kind, changes in rows:
            counts[kind] += 1
            cur.execute("""INSERT INTO record_change (source, source_record_id, snapshot_id, prev_snapshot_id, change_type, changes)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (source, rid, snapshot_id, prev_id, kind, Jsonb(changes) if changes else None))
    return counts
