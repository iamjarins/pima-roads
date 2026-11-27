"""Steps 4 to 6. Attach each source record to the base segments it covers, by geometry, then confirm by name."""
from .config import MATCH_BUFFER_M

MATCH_SQL = """
TRUNCATE segment_event;

CREATE TEMP TABLE ev ON COMMIT DROP AS
SELECT id, source, geom_m, ST_Length(geom_m) AS len_m,
       ST_Buffer(geom_m, {buf}, 'endcap=round') AS buf,
       -- The county names the street outright. For the city the street is somewhere in the location and project text.
       upper(regexp_replace(coalesce(street_raw, coalesce(location_raw, '') || ' ' || coalesce(raw->>'ProjectName', '')),
                            '[^A-Za-z0-9 ]', '', 'g')) AS name_text,
       street_raw IS NOT NULL AS has_street
FROM work_event WHERE geom_m IS NOT NULL AND status <> 'dropped';
CREATE INDEX ON ev USING gist (geom_m);
ANALYZE ev;

CREATE TEMP TABLE cand ON COMMIT DROP AS
SELECT s.id AS segment_id, e.id AS work_event_id, e.source, e.len_m, s.length_m,
       ST_Length(ST_Intersection(s.geom_m, e.buf)) AS overlap_m,
       CASE WHEN s.name_base IS NULL THEN NULL
            ELSE (' ' || e.name_text || ' ') LIKE ('% ' || s.name_base || ' %') END AS name_match,
       e.has_street
FROM ev e JOIN segment s ON ST_DWithin(s.geom_m, e.geom_m, {buf});

-- A block belongs to a record when the record runs along most of it.
INSERT INTO segment_event (segment_id, work_event_id, overlap_m, segment_fraction, name_match, match_confidence)
SELECT segment_id, work_event_id, overlap_m, frac, name_match,
       CASE WHEN name_match AND frac >= 0.8 THEN 'high'
            WHEN name_match THEN 'medium'
            WHEN has_street THEN 'low'          -- county record names a different street than the block
            ELSE 'medium' END                   -- city package with no street name to compare
FROM (SELECT *, least(overlap_m / nullif(length_m, 0), 1) AS frac FROM cand) c
WHERE (name_match AND frac >= 0.5)
   OR (name_match IS NOT TRUE AND frac >= 0.9 AND overlap_m >= 30);

-- A record shorter than its block would otherwise vanish: attach it to the block it sits on, marked partial.
INSERT INTO segment_event (segment_id, work_event_id, overlap_m, segment_fraction, name_match, match_confidence)
SELECT DISTINCT ON (work_event_id) segment_id, work_event_id, overlap_m, least(overlap_m / nullif(length_m, 0), 1), name_match, 'partial'
FROM cand c
WHERE NOT EXISTS (SELECT 1 FROM segment_event x WHERE x.work_event_id = c.work_event_id)
  AND overlap_m >= 0.5 * least(len_m, length_m) AND overlap_m >= 10
ORDER BY work_event_id, (name_match IS TRUE) DESC, overlap_m DESC;
"""

SUMMARY_SQL = """
UPDATE segment s SET last_work_year = x.yr, completed_count = coalesce(x.done, 0), upcoming_count = coalesce(x.upcoming, 0)
FROM (SELECT s.id,
             max(e.work_year) FILTER (WHERE e.status = 'completed' AND se.match_confidence <> 'low') AS yr,
             count(*) FILTER (WHERE e.status = 'completed') AS done,
             count(*) FILTER (WHERE e.status IN ('planned', 'in_progress')) AS upcoming
      FROM segment s LEFT JOIN segment_event se ON se.segment_id = s.id
      LEFT JOIN work_event e ON e.id = se.work_event_id GROUP BY s.id) x
WHERE x.id = s.id;
"""

REPORT_SQL = """
SELECT e.source,
       count(*) AS events,
       count(*) FILTER (WHERE e.status = 'dropped') AS dropped_by_agency,
       count(*) FILTER (WHERE e.geom IS NULL) AS no_geometry,
       count(*) FILTER (WHERE m.n > 0) AS matched,
       count(*) FILTER (WHERE m.best = 'high') AS best_high,
       count(*) FILTER (WHERE m.best = 'medium') AS best_medium,
       count(*) FILTER (WHERE m.best = 'low') AS best_low,
       count(*) FILTER (WHERE m.best = 'partial') AS best_partial,
       count(*) FILTER (WHERE m.n IS NULL AND e.status <> 'dropped' AND e.geom IS NOT NULL) AS unmatched,
       round(avg(m.covered)::numeric, 3) AS avg_length_covered
FROM work_event e
LEFT JOIN LATERAL (
    SELECT count(*) AS n,
           (array_agg(se.match_confidence ORDER BY array_position(ARRAY['high','medium','low','partial'], se.match_confidence)))[1] AS best,
           least(sum(se.overlap_m) / nullif(ST_Length(e.geom_m), 0), 1) AS covered
    FROM segment_event se WHERE se.work_event_id = e.id HAVING count(*) > 0) m ON true
GROUP BY e.source ORDER BY e.source;
"""


def match(conn):
    cur = conn.cursor()
    cur.execute(MATCH_SQL.format(buf=float(MATCH_BUFFER_M)))
    cur.execute(SUMMARY_SQL)


def report(conn):
    cur = conn.cursor()
    cur.execute(REPORT_SQL)
    cols = [d.name for d in cur.description]
    out = {}
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        r["avg_length_covered"] = float(r["avg_length_covered"]) if r["avg_length_covered"] is not None else None
        usable = r["events"] - r["dropped_by_agency"]
        r["match_rate"] = round(r["matched"] / usable, 4) if usable else None
        out[r.pop("source")] = r
    cur.execute("SELECT owner, count(*), round(sum(length_m)::numeric / 1609.344, 1) FROM segment GROUP BY owner ORDER BY 2 DESC")
    out["segments_by_owner"] = {o: {"segments": n, "miles": float(mi)} for o, n, mi in cur.fetchall()}
    cur.execute("SELECT count(*), count(*) FILTER (WHERE completed_count > 0), count(*) FILTER (WHERE upcoming_count > 0) FROM segment")
    total, with_work, with_upcoming = cur.fetchone()
    out["segments"] = {"total": total, "with_completed_work": with_work, "with_upcoming_work": with_upcoming}
    return out
