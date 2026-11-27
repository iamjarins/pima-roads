"""The acceptance checks from the engineering spec, run against the loaded database through the API.

They use real records as pulled on Nov 27, 2026. If an agency edits one of these records the matching
check will fail, and that is the point: look at record_change to see what moved.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import app, pool
from pipeline.config import COVERAGE_NOTE


@pytest.fixture(scope="module")
def client():
    try:
        with TestClient(app) as c:
            with pool.connection() as conn:
                if conn.execute("SELECT count(*) AS n FROM segment").fetchone()["n"] == 0:
                    pytest.skip("database is empty; run `make refresh` first")
            yield c
    except Exception as exc:  # no database at all
        pytest.skip(f"database not available: {exc}")


def one(sql, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def block(name, cross_a, cross_b=None):
    """The block of `name` whose two ends are the given cross streets."""
    row = one("""SELECT id FROM segment WHERE name = %(n)s
                 AND ((from_desc ILIKE %(a)s AND to_desc ILIKE %(b)s) OR (from_desc ILIKE %(b)s AND to_desc ILIKE %(a)s))
                 ORDER BY id LIMIT 1""", {"n": name, "a": f"%{cross_a}%", "b": f"%{cross_b or ''}%"})
    assert row, f"no block of {name} at {cross_a} / {cross_b}"
    return row["id"]


def blocks_of_record(source, record_id):
    with pool.connection() as conn:
        return [r["segment_id"] for r in conn.execute(
            """SELECT se.segment_id FROM segment_event se JOIN work_event e ON e.id = se.work_event_id
               WHERE e.source = %s AND e.source_record_id = %s ORDER BY se.segment_fraction DESC, se.segment_id""",
            (source, record_id))]


def history(client, segment_id):
    seg = client.get(f"/api/segments/{segment_id}").json()
    hist = client.get(f"/api/segments/{segment_id}/events").json()
    return seg, hist


def test_w_speedway_gates_pass_to_camino_de_oeste(client):
    seg, hist = history(client, block("W Speedway Bl", "Gates Pass", "Camino de Oeste"))
    assert seg["owner"] == "county"
    done = hist["completed"]
    assert len(done) == 2 and not hist["upcoming"]
    fog, mill = done  # newest first
    assert fog["treatment"]["label"] == "Fog seal" and fog["when"].endswith("July 23, 2025")
    assert fog["contractor"]["name"] == "Sunland Asphalt"
    assert fog["project"]["name"] == "25JOC-ST05- Tucson Mountains Region"
    assert mill["treatment"]["label"] == "Mill and thick overlay" and mill["when"] == "Finished July 14, 2020"
    assert mill["contractor"]["name"] is None            # "Undefined" in the source reads as not recorded
    assert mill["project"]["name"] == "20HB &JOCs - Pavement Preservation Program"


def test_e_speedway_east_of_houghton(client):
    row = one("SELECT source_record_id, length_mi FROM work_event WHERE source = 'county_work' AND project_id = "
              "(SELECT id FROM project WHERE name = '23HB-MP06-TANQUE VERDE') AND street_raw = 'E SPEEDWAY BL' AND raw->>'From_' = 'N HOUGHTON RD'")
    assert round(row["length_mi"], 1) == 5.0
    segs = blocks_of_record("county_work", row["source_record_id"])
    assert len(segs) > 10                                 # a five-mile record covers many blocks
    # The first block east of Houghton also carries a 2012 city widening record whose line runs past Houghton,
    # so check a block further along the stretch.
    checked = 0
    for sid in segs:
        seg, hist = history(client, sid)
        if seg["owner"] != "county" or len(hist["completed"]) != 1:
            continue
        ev = hist["completed"][0]
        assert ev["treatment"]["label"] == "Mill and thick overlay"
        assert ev["when"].endswith("May 26, 2023")
        assert ev["contractor"]["name"] == "Southern Arizona Paving"
        assert ev["project"]["name"] == "23HB-MP06-TANQUE VERDE"
        checked += 1
    assert checked >= len(segs) - 2


def test_w_anklam_stretch_ending_at_speedway(client):
    row = one("""SELECT source_record_id FROM work_event WHERE source = 'county_work' AND street_raw = 'W ANKLAM RD'
                 AND raw->>'To_' = 'E SPEEDWAY BL' AND treatment_raw = 'FOG SEAL'""")
    for sid in blocks_of_record("county_work", row["source_record_id"]):
        seg, hist = history(client, sid)
        assert seg["owner"] == "county"
        events = [e for e in hist["completed"] if e["fiscal_year"] == 2021]
        assert sorted(e["treatment"]["label"] for e in events) == ["Crack seal", "Fog seal"]   # two events, not one
        assert all(e["contractor"]["name"] == "VSS" for e in events)
        assert len({e["source"]["record_url"] for e in events}) == 2


def test_speedway_painted_hills_to_greasewood(client):
    seg, hist = history(client, block("W Speedway Bl", "Greasewood", "Saddlewood Ranch"))
    assert seg["owner"] == "city"
    (ev,) = hist["completed"]
    assert ev["treatment"]["label"] == "Reconstruction"
    assert ev["contractor"]["name"] == "Granite"
    assert ev["when"] == "Finished May 2, 2019"
    assert ev["funding"]["program"].startswith("Prop 101")


def test_speedway_greasewood_to_silverbell(client):
    seg, hist = history(client, block("W Speedway Bl", "Silverbell", "Camino Santiago"))
    assert seg["owner"] == "city"
    (ev,) = hist["completed"]
    assert ev["treatment"]["label"] == "Fog seal"
    assert ev["contractor"]["name"] == "American Road Maintenance"
    assert ev["when"] == "April 28, 2020 to October 29, 2020"   # construction dates, not FY_Planned 2023
    assert ev["year"] == 2020
    assert ev["funding"]["program"].startswith("Prop 101")


def test_silverbell_st_marys_to_congress(client):
    seg, hist = history(client, block("N Silverbell Rd", "Pennington", "Alameda"))
    assert seg["owner"] == "city"
    (ev,) = hist["completed"]
    assert ev["treatment"]["label"] == "Reconstruction"
    assert "water line replacement" in ev["treatment"]["description"]
    assert ev["contractor"]["name"] == "Borderland"
    assert ev["when"] == "January 15, 2024 to December 31, 2024"
    assert ev["funding"]["program"].startswith("Prop 101")


def test_speedway_silverbell_to_i10_has_no_record(client):
    seg, hist = history(client, block("W Speedway Bl", "El Rio", "Silverbell"))
    assert seg["owner"] == "city"
    assert hist["completed"] == []
    assert hist["empty_message"] == "No work on record."
    assert COVERAGE_NOTE["city"] in hist["coverage_notes"]
    assert "never" not in str(hist).lower()


def test_county_event_count_equals_source_row_count(client):
    events = one("SELECT count(*) AS n FROM work_event WHERE source = 'county_work'")["n"]
    rows = one("SELECT row_count FROM source_snapshot WHERE source = 'county_work' ORDER BY id DESC LIMIT 1")["row_count"]
    assert events == rows


def test_every_event_links_to_its_source_record(client):
    assert one("SELECT count(*) AS n FROM work_event WHERE source_url IS NULL OR source_url !~ ('/' || source_record_id || '(\\?|$)')")["n"] == 0


def test_no_cost_without_a_linked_contract(client):
    assert one("SELECT count(*) AS n FROM job_order")["n"] == 0 and one("SELECT count(*) AS n FROM project_contract")["n"] == 0
    with pool.connection() as conn:
        ids = [r["segment_id"] for r in conn.execute("SELECT DISTINCT segment_id FROM segment_event ORDER BY segment_id LIMIT 300")]
    for sid in ids:
        hist = client.get(f"/api/segments/{sid}/events").json()
        for ev in hist["completed"] + hist["upcoming"]:
            assert ev["cost"] == {"status": "not_linked", "text": "Cost not yet linked"}
            assert "$" not in str(ev)


def test_planned_work_is_listed_apart(client):
    row = one("""SELECT se.segment_id FROM segment_event se JOIN work_event e ON e.id = se.work_event_id
                 WHERE e.status = 'planned' LIMIT 1""")
    hist = client.get(f"/api/segments/{row['segment_id']}/events").json()
    assert hist["upcoming"] and all(e["status"] != "completed" for e in hist["upcoming"])
    assert all(e["status"] == "completed" for e in hist["completed"])


def test_non_paving_city_work_is_left_out(client):
    assert one("""SELECT count(*) AS n FROM work_event WHERE source = 'city_projects'
                  AND raw->>'Type' NOT IN ('Surface Maintenance', 'Roadway')""")["n"] == 0


def test_search_by_street_and_address(client):
    names = [r["label"] for r in client.get("/api/search", params={"q": "speedway blvd"}).json()["results"]]
    assert "W Speedway Bl" in names and "E Speedway Bl" in names
    hit = client.get("/api/search", params={"q": "1500 W Speedway Blvd"}).json()["results"][0]
    assert hit["type"] == "address" and hit["segment_id"]
    assert client.get(f"/api/segments/{hit['segment_id']}").json()["name"] == "W Speedway Bl"


def test_unknown_segment_is_404(client):
    assert client.get("/api/segments/999999999").status_code == 404
