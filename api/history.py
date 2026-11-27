"""How a segment's history is put together. Shared by the live API and the static export, so both say the same thing."""
from pipeline import normalize as N
from pipeline.config import COVERAGE_NOTE, SOURCES, WORK_SOURCES, layer_url

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



# ---------------------------------------------------------------- queries
# {where} is filled with a filter on one segment (API) or left open for every segment (export).

SEGMENT_SQL = """
    SELECT id, name, from_desc, to_desc, owner, owner_name, owner_basis, road_category,
           round((length_m / 1609.344)::numeric, 2)::float AS length_mi,
           last_work_year, completed_count, upcoming_count,
           ST_AsGeoJSON(geom, 6)::json AS geometry,
           ARRAY[ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)] AS bbox
    FROM segment {where}"""

EVENTS_SQL = """
    SELECT se.segment_id, e.id, e.source, e.source_record_id, e.source_url, e.street_raw, e.location_raw,
           e.treatment_raw, e.treatment_category, e.treatment_label, e.description_raw, e.status_raw, e.status,
           e.fiscal_year, e.start_date, e.start_date_raw, e.start_precision, e.end_date, e.end_date_raw, e.end_precision,
           e.work_year, e.contractor_raw, e.contractor, e.project_id, e.funding_program, e.funding_detail,
           e.length_mi, e.source_edited_at,
           se.match_confidence, se.segment_fraction,
           p.name AS project_name, p.parsed_code AS project_code, p.link AS project_link,
           v.canonical_name AS vendor_name, snap.pulled_at,
           (SELECT count(*) FROM record_change rc WHERE rc.source = e.source AND rc.source_record_id = e.source_record_id
                AND rc.change_type = 'modified') AS times_edited
    FROM segment_event se
    JOIN work_event e ON e.id = se.work_event_id
    JOIN source_snapshot snap ON snap.id = e.snapshot_id
    LEFT JOIN project p ON p.id = e.project_id
    LEFT JOIN vendor v ON v.id = e.vendor_id
    WHERE e.status <> 'dropped' {where}
    ORDER BY se.segment_id, coalesce(e.end_date, e.start_date, make_date(coalesce(e.fiscal_year, 1900), 6, 30)) DESC, e.id"""


def cost_links(q):
    """Every project that has a job order or a contract linked to it. Empty until phase 3 data is added."""
    links = {"jobs": {}, "contracts": {}}
    for r in q("""
        SELECT j.project_id, sum(j.amount) AS amount, min(j.confidence) AS confidence,
               min(c.contract_number) AS contract_number, min(coalesce(j.document_link, c.document_link)) AS document_link,
               (SELECT count(DISTINCT se.segment_id) FROM work_event w JOIN segment_event se ON se.work_event_id = w.id
                 WHERE w.project_id = j.project_id) AS segments,
               (SELECT coalesce(sum(w.length_mi), 0) FROM work_event w WHERE w.project_id = j.project_id)::float AS miles
        FROM job_order j JOIN contract c ON c.id = j.contract_id GROUP BY j.project_id"""):
        links["jobs"][r["project_id"]] = r
    for r in q("""
        SELECT DISTINCT ON (pc.project_id) pc.project_id, pc.confidence, c.contract_number, c.ceiling_amount, c.document_link
        FROM project_contract pc JOIN contract c ON c.id = pc.contract_id ORDER BY pc.project_id, pc.confidence"""):
        links["contracts"][r["project_id"]] = r
    return links


def last_pulls(q):
    return q("""SELECT DISTINCT ON (source) source, pulled_at FROM source_snapshot
                WHERE source = ANY(%s) ORDER BY source, pulled_at DESC""", (list(WORK_SOURCES),))


def build_meta(q):
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


def build_history(seg, events, links, pulls):
    """The history panel for one segment. `events` are EVENTS_SQL rows for it, newest first."""
    segment_id = seg["id"]
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
    return {
        "segment_id": segment_id,
        "completed": completed,
        "upcoming": upcoming,
        "empty_message": None if completed else "No work on record.",
        "coverage_notes": notes,
        "sources_checked": [{"key": p["source"], "title": SOURCES[p["source"]]["title"], "url": layer_url(p["source"]),
                             "pulled_at": p["pulled_at"]} for p in pulls],
    }

