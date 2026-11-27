"""Sources and settings. Everything a deployer may need to change lives here or in env vars."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://roadwork:roadwork@localhost:5432/roadwork")

_PIMA = "https://gisdata.pima.gov/arcgis1/rest/services/GISOpenData"
_COT = "https://utility.arcgis.com/usrsvcs/servers"

# key -> ArcGIS layer. `id_field` is the stable key used to diff one pull against the next.
SOURCES = {
    "county_work": {
        "title": "Pima County Road Repair and Preservation Projects",
        "owner": "Pima County Department of Transportation",
        "url": f"{_PIMA}/Transportation/MapServer/18",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "city_projects": {
        "title": "City of Tucson DTM CIP Locations",
        "owner": "City of Tucson Department of Transportation and Mobility",
        "url": f"{_COT}/f14af047d8634d1484d18626601305eb/rest/services/PublicMaps/Transportation/MapServer/75",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "county_streets": {
        "title": "Pima County Street Network",
        "owner": "Pima County GIS",
        "url": f"{_PIMA}/Transportation/MapServer/11",
        "id_field": "ROADID",
        "geometry": True,
    },
    "county_maintenance": {
        "title": "Pima County Street Maintenance",
        "owner": "Pima County GIS",
        "url": f"{_PIMA}/Transportation/MapServer/15",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "jurisdictions": {
        "title": "Jurisdictional Boundaries",
        "owner": "Pima County GIS",
        "url": f"{_PIMA}/Boundaries/MapServer/11",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "indian_nations": {
        "title": "Indian Nations",
        "owner": "Pima County GIS",
        "url": f"{_PIMA}/Boundaries/MapServer/10",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "city_pavement_major": {
        "title": "City of Tucson Pavement Conditions, Major Streets",
        "owner": "City of Tucson Department of Transportation and Mobility",
        "url": f"{_COT}/00a290def9cf4c16a0d5b87f2b282c67/rest/services/PublicMaps/RoadsPavement/MapServer/48",
        "id_field": "OBJECTID",
        "geometry": True,
    },
    "city_pavement_minor": {
        "title": "City of Tucson Pavement Conditions, Minor Streets",
        "owner": "City of Tucson Department of Transportation and Mobility",
        "url": f"{_COT}/02bfd7ae183c4c8ebb2d74c02984ce35/rest/services/PublicMaps/RoadsPavement/MapServer/49",
        "id_field": "OBJECTID",
        "geometry": True,
    },
}

# The city server has its HTML pages switched off, so city links open the JSON view of the record.
SOURCES["city_projects"]["link_suffix"] = "?f=pjson"


def record_url(source, record_id):
    return f"{SOURCES[source]['url']}/{record_id}{SOURCES[source].get('link_suffix', '')}"


def layer_url(source):
    return SOURCES[source]["url"] + SOURCES[source].get("link_suffix", "")


# The two layers that carry work history. The rest build the base network.
WORK_SOURCES = ("county_work", "city_projects")

# Matching (metres, EPSG:26912 = UTM 12N, which covers Pima County).
METRIC_SRID = 26912
MATCH_BUFFER_M = 15.0

# Static vector tiles.
TILE_DIR = os.environ.get("TILE_DIR", os.path.join(os.path.dirname(__file__), "..", "web", "tiles"))
TILE_MIN_ZOOM = 7
TILE_MAX_ZOOM = 13

# Shown to users when a segment has no history.
COVERAGE_NOTE = {
    "county": "County records start in fiscal 2017.",
    "city": "City records go back to about 2011 for some major projects.",
}
