from datetime import date

from pipeline import normalize as N


def test_placeholders_are_empty():
    for v in ("Undefined", "Unidentified", "TBD", " ", None):
        assert N.blank_if_placeholder(v) is None
    assert N.blank_if_placeholder("VSS") == "VSS"


def test_city_free_text_dates():
    assert N.parse_text_date("04/28/20") == (date(2020, 4, 28), "day")
    assert N.parse_text_date("10/5/2020") == (date(2020, 10, 5), "day")
    assert N.parse_text_date("FY2019") == (date(2019, 6, 30), "fiscal_year")
    assert N.parse_text_date("Fall 2026") == (date(2026, 10, 1), "season")
    assert N.parse_text_date("2017") == (date(2017, 1, 1), "year")
    assert N.parse_text_date("TBD") == (None, None)
    assert N.parse_text_date("Winter 99/00") == (None, None)


def test_county_epoch_dates_ignore_timezone_of_midnight():
    assert N.epoch_ms_date(1594684800000) == date(2020, 7, 14)   # midnight UTC
    assert N.epoch_ms_date(1685084400000) == date(2023, 5, 26)   # midnight Arizona


def test_fiscal_year():
    assert N.fiscal_year("2019 Supplemental") == 2019
    assert N.fiscal_year("28") == 2028
    assert N.fiscal_year("TBD") is None


def test_treatments_keep_distinct_jobs_distinct():
    cats = {N.treatment(t)[0] for t in ("FOG SEAL", "SEAL CRACKS", "MILL AND THICK OVERLAY", "Reconstruction")}
    assert cats == {"fog_seal", "crack_seal", "mill_overlay", "reconstruction"}
    assert N.treatment("SEAL CRACKS")[1] == "Crack seal"
    assert N.treatment("MILL AND THICK OVERLAY")[1] == "Mill and thick overlay"
    assert N.treatment(" ", "Type 3 Fiberized Slurry Seal")[0] == "surface_seal"
    assert N.treatment(None) == ("other", "Treatment not recorded")


def test_vendor_spellings_collapse():
    assert N.vendor_name("Granite") == N.vendor_name("Granite Construction") == "Granite Construction"
    assert N.vendor_name("VSS") == N.vendor_name("VSSI")
    assert N.vendor_name("Borderland") == N.vendor_name("Borderland Construction")
    assert N.vendor_name("Pavement (VSSI) / Curb Ramps (Tucson Asphalt)") == N.vendor_name("VSS")
    assert N.vendor_name("Undefined") is None


def test_county_project_code():
    c = N.parse_county_project("22JOC-MP07-CARDINAL ESTATES & MISSION RIDGE")
    assert (c["fiscal_year"], c["delivery_code"], c["program_code"], c["job_number"]) == (2022, "JOC", "MP", "07")
    assert c["inferred"] is True
    assert N.parse_county_project("20HB &JOCs - Pavement Preservation Program")["fiscal_year"] == 2020
    assert N.parse_county_project("Prop 101 Arterial - Preservation Package 3") is None


def test_names():
    assert N.base_name("W SPEEDWAY BL") == "SPEEDWAY"
    assert N.display_name("N CAMINO DE OESTE") == "N Camino de Oeste"
    assert N.display_name("N EL RIO DR") == "N El Rio Dr"
    assert N.display_name("E 22ND ST") == "E 22nd St"


def test_status():
    today = date(2026, 11, 27)
    assert N.county_status("Complete - Under Warranty", None, 2025, today) == "completed"
    assert N.county_status("Active Construction", None, 2027, today) == "in_progress"
    assert N.county_status(None, None, 2019, today) == "completed"
    assert N.city_status("Design", "Design") == "planned"
    assert N.city_status("Dropped", "Dropped") == "dropped"
