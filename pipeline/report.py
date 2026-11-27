"""Print the match-rate report from the last refresh, and list the records that matched no segment."""
import json

from . import db


def main():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT started_at, finished_at, report FROM pipeline_run WHERE report IS NOT NULL ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("No refresh has run yet. Run: python -m pipeline.run")
        return
    print(f"Last refresh finished {row[1]:%Y-%m-%d %H:%M %Z}")
    print(json.dumps(row[2], indent=2))
    cur.execute("""SELECT e.source, e.source_record_id, coalesce(e.street_raw, ''), coalesce(e.location_raw, ''),
                          round(ST_Length(e.geom_m)) FROM work_event e
                   WHERE e.status <> 'dropped' AND NOT EXISTS (SELECT 1 FROM segment_event se WHERE se.work_event_id = e.id)
                   ORDER BY 1, 2""")
    rows = cur.fetchall()
    print(f"\nRecords that matched no segment: {len(rows)}")
    for r in rows:
        print(f"  {r[0]} {r[1]}: {r[2]} {r[3]} ({r[4]} m)")
    cur.execute("""SELECT se.match_confidence, count(*) FROM segment_event se GROUP BY 1 ORDER BY 2 DESC""")
    print("\nSegment links by confidence:", dict(cur.fetchall()))


if __name__ == "__main__":
    main()
