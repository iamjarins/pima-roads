"""Step 2. Turn source attributes into consistent values. Raw values are always kept beside them."""
import re
from datetime import date, datetime, timezone

# ---------------------------------------------------------------- text helpers

EMPTY_WORDS = {"", "undefined", "unidentified", "tbd", "none", "n/a", "na", "null"}


def clean(value):
    """Strip a source string; return None when it is blank."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def blank_if_placeholder(value):
    """'Undefined', 'Unidentified' and 'TBD' mean the source has no value."""
    s = clean(value)
    return None if s is None or s.lower() in EMPTY_WORDS else s


_DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_TYPES = {
    "AV", "AVE", "AVENUE", "BL", "BLVD", "BOULEVARD", "CI", "CIR", "CIRCLE", "CT", "COURT", "DR", "DRIVE",
    "HY", "HWY", "HIGHWAY", "LN", "LANE", "LP", "LOOP", "PL", "PLACE", "PW", "PKWY", "PARKWAY", "RD", "ROAD",
    "ST", "STREET", "TR", "TRL", "TRAIL", "WY", "WAY", "TE", "TER", "TERRACE", "SV", "PS", "PH", "SQ",
    "STRA", "STRAV",  # county "stravenue"
}


def name_tokens(name):
    return re.sub(r"[^A-Z0-9 ]", "", (name or "").upper().replace("-", " ")).split()


def base_name(name):
    """'W SPEEDWAY BL' -> 'SPEEDWAY'. Used only to confirm a geometry match."""
    t = name_tokens(name)
    if len(t) > 1 and t[0] in _DIRS:
        t = t[1:]
    if len(t) > 1 and t[-1] in _DIRS:
        t = t[:-1]
    if len(t) > 1 and t[-1] in _TYPES:
        t = t[:-1]
    return " ".join(t) or None


_SMALL = {"De", "Del", "Of", "The", "And", "Y"}
_ARTICLES = {"La", "Las", "Los", "El"}


def display_name(name):
    """'W SPEEDWAY BL' -> 'W Speedway Bl'."""
    s = clean(name)
    if not s:
        return "Unnamed road"
    words = []
    for i, w in enumerate(s.split()):
        if re.fullmatch(r"\d+(ST|ND|RD|TH)", w, re.I):
            words.append(w.lower())
        elif re.fullmatch(r"I-?\d+", w, re.I):
            words.append(w.upper())
        else:
            t = w.capitalize()
            if t.startswith("Mc") and len(t) > 2:
                t = "Mc" + t[2:].capitalize()
            if i and (t in _SMALL or (t in _ARTICLES and words[-1] in ("de", "del"))):
                t = t.lower()
            words.append(t)
    return " ".join(words)


# ---------------------------------------------------------------- treatments

# category -> (plain name, one-line explanation). Shown beside every event.
TREATMENTS = {
    "crack_seal": ("Crack seal", "Cracks are filled with sealant to keep water out. Light upkeep; the surface itself is not renewed."),
    "fog_seal": ("Fog seal", "A thin spray of asphalt emulsion that refreshes and seals the existing surface. No new pavement is added."),
    "surface_seal": ("Surface seal", "A thin new wearing layer (chip, slurry, micro or cape seal) spread over the existing pavement."),
    "overlay": ("Overlay", "A new layer of asphalt paved on top of the existing road."),
    "mill_overlay": ("Mill and overlay", "The top layer of old asphalt is ground off and replaced with new asphalt."),
    "reconstruction": ("Reconstruction", "The road is rebuilt: old pavement is removed down to the base and replaced."),
    "new_road": ("New road or widening", "A new roadway is built or an existing one is widened."),
    "resurfacing": ("Resurfacing", "The road gets a new surface. The source does not say which method."),
    "preservation": ("Preservation", "Upkeep to extend the life of the pavement. The source does not say which method."),
    "other": ("Other road work", "The source does not describe this work in more detail."),
}

_TREATMENT_RULES = [  # first match wins, tested against the upper-cased raw value
    (r"CRACK", "crack_seal"),
    (r"FOG", "fog_seal"),
    (r"CHIP|SLURRY|MICRO|CAPE|SEAL COAT", "surface_seal"),
    (r"MILL", "mill_overlay"),
    (r"OVERLAY", "overlay"),
    (r"RECONSTRUCT", "reconstruction"),
    (r"NEW ROADWAY|WIDENING", "new_road"),
    (r"RESURFAC", "resurfacing"),
    (r"PRESERV", "preservation"),
]

_LABEL_OVERRIDES = {"SEAL CRACKS": "Crack seal", "VARIOUS": "Various treatments"}


def treatment(raw, description=None):
    """Return (category, label). The label stays close to the source's own words."""
    s = clean(raw)
    category = "other"
    for text in (s, clean(description)):
        if not text:
            continue
        for pattern, cat in _TREATMENT_RULES:
            if re.search(pattern, text.upper()):
                category = cat
                break
        if category != "other":
            break
    if not s:
        return category, "Treatment not recorded" if category == "other" else TREATMENTS[category][0]
    label = _LABEL_OVERRIDES.get(s.upper()) or (s[0].upper() + s[1:].lower())
    return category, label


# ---------------------------------------------------------------- vendors

# canonical name -> spellings seen in the two layers (compared after _vendor_key).
VENDOR_ALIASES = {
    "Granite Construction": ["Granite", "Granite Construction"],
    "VSS International": ["VSS", "VSSI", "VSS Package A"],
    "Borderland Construction": ["Borderland", "Borderlands", "Borderland Construction", "Borderland Construction Company"],
    "Sunland Asphalt & Construction": ["Sunland", "Sunland Asphalt", "Sunland Apshalt", "Sunland Asphalt & Construction", "JOC/Sunland"],
    "Southern Arizona Paving & Construction": ["Southern Arizona Paving", "Southern Arizona Paving & Construction",
                                                "Southern Arizona Paving & Construction Co.", "SAP"],
    "KE&G Construction": ["KE&G", "KE & G", "K&G", "KE&G Construction"],
    "Cactus Asphalt": ["Cactus", "Cactus Asphalt"],
    "Tucson Asphalt Contractors": ["Tucson Asphalt", "Tucson Asphalt Contractors"],
    "iSX Construction": ["ISX", "iSX Construction", "ALB iSX"],
    "Whelcon Contractors": ["Whelcon", "Whelco", "Whelcon Contractors", "Whelcon Contractors, LLC"],
    "American Road Maintenance": ["American Road Maintenance"],
    "Markham Contracting": ["Markham Contracting"],
    "Pavement Restoration Inc": ["Pavement Restoration Inc"],
    "City of Tucson": ["COT"],
}


def _vendor_key(name):
    return re.sub(r"[^a-z0-9&]", "", name.lower())


_ALIAS_INDEX = {_vendor_key(a): canon for canon, aliases in VENDOR_ALIASES.items() for a in aliases + [canon]}


def primary_contractor(raw):
    """Pick the paving firm out of strings like 'Pavement (VSSI) / Curb Ramps (Tucson Asphalt)'."""
    s = blank_if_placeholder(raw)
    if not s:
        return None
    m = re.search(r"(?:Pavement|MLST)\s*[:(]\s*([^/;,)]+)", s, re.I)
    if m:
        return m.group(1).strip()
    first = re.split(r"\s*[/;,]\s*", s)[0]
    return re.sub(r"\s*\(.*?\)\s*$", "", first).strip() or s


def vendor_name(raw):
    """Canonical vendor for a raw contractor string, or None when the source has no contractor."""
    p = primary_contractor(raw)
    if not p:
        return None
    return _ALIAS_INDEX.get(_vendor_key(p), p)


# ---------------------------------------------------------------- dates

_SEASON_MONTH = {"winter": 1, "spring": 4, "summer": 7, "fall": 10, "autumn": 10}


def parse_text_date(raw):
    """Parse the city's free-text dates. Returns (date, precision) or (None, None).

    '04/28/20' -> day; 'FY2019' -> fiscal_year; 'Fall 2026' -> season; '2024' -> year; 'TBD' -> nothing.
    A fiscal year N runs July 1 of N-1 to June 30 of N; it is stored as June 30 of N.
    """
    s = clean(raw)
    if not s or s.lower() in EMPTY_WORDS:
        return None, None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", s)
    if m:
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        try:
            return date(y, mo, d), "day"
        except ValueError:
            return None, None
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m and 1 <= int(m[1]) <= 12:
        return date(int(m[2]), int(m[1]), 1), "month"
    m = re.fullmatch(r"FY\s*'?(\d{2}|\d{4})", s, re.I)
    if m:
        y = int(m[1])
        return date(y + 2000 if y < 100 else y, 6, 30), "fiscal_year"
    m = re.fullmatch(r"(winter|spring|summer|fall|autumn)\s+(\d{4})", s, re.I)
    if m:
        return date(int(m[2]), _SEASON_MONTH[m[1].lower()], 1), "season"
    m = re.fullmatch(r"(19|20)\d{2}", s)
    if m:
        return date(int(s), 1, 1), "year"
    return None, None


def epoch_ms_date(ms):
    """County dates are epoch milliseconds at midnight, either UTC or Arizona time. The UTC date is right for both."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def epoch_ms_ts(ms):
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fiscal_year(raw):
    """'2019 Supplemental' -> 2019; '28' -> 2028; 'TBD' -> None."""
    m = re.search(r"\d{4}|\d{2}", clean(raw) or "")
    if not m:
        return None
    y = int(m[0])
    return y + 2000 if y < 100 else y


# ---------------------------------------------------------------- status

def county_status(raw, end_date, fy, today):
    s = (clean(raw) or "").lower()
    if s.startswith("complete"):
        return "completed"
    if s == "active construction":
        return "in_progress"
    if s == "planned":
        return "planned"
    # Status missing: fall back on the dates.
    if (end_date and end_date <= today) or (fy and fy < today.year):
        return "completed"
    return "planned"


def city_status(status, project_status):
    s = (clean(status) or clean(project_status) or "").lower()
    if s == "complete":
        return "completed"
    if s == "construction":
        return "in_progress"
    if s == "dropped":
        return "dropped"
    return "planned"  # Planned, Design, Pre-Construction, On Hold


# ---------------------------------------------------------------- projects

_COUNTY_CODE = re.compile(r"^\s*(\d{2})\s*(JOC|HB)(?:\s*&\s*JOCs?)?\s*-?\s*(?:([A-Z]{2})(\d{2}))?", re.I)
_DELIVERY = {"JOC": "Job order contract", "HB": "Hard bid"}
_PROGRAM = {"MP": "Mill and pave", "ST": "Surface treatment", "CS": "Crack seal"}


def parse_county_project(name):
    """'22JOC-MP07-CARDINAL ESTATES' -> fiscal year 2022, job order contract, MP, job 07.

    The meanings of JOC, HB, MP, ST and CS are inferred from the data and are not confirmed by the county.
    """
    m = _COUNTY_CODE.match(name or "")
    if not m:
        return None
    delivery, program = m[2].upper(), (m[3] or "").upper() or None
    return {
        "fiscal_year": 2000 + int(m[1]),
        "delivery_code": delivery,
        "delivery": _DELIVERY.get(delivery),
        "program_code": program,
        "program": _PROGRAM.get(program),
        "job_number": m[4],
        "inferred": True,
    }


# ---------------------------------------------------------------- owners

MAINT_CODES = {  # Pima County Street Maintenance MNT_PRI
    "PC": ("county", "Pima County"),
    "COT": ("city", "City of Tucson"),
    "ADOT": ("state", "Arizona Department of Transportation"),
    "MAR": ("town", "Town of Marana"),
    "PY": ("tribal", "Pascua Yaqui Tribe"),
    "TOH": ("tribal", "Tohono O'odham Nation"),
    "PIN": ("other", "Pinal County"),
    "SC": ("other", "Santa Cruz County"),
    "SNP": ("other", "Saguaro National Park"),
}

CUSTODIANS = {  # City of Tucson pavement layers, Custodian
    "City of Tucson": ("city", "City of Tucson"),
    "Pima County": ("county", "Pima County"),
    "ADOT": ("state", "Arizona Department of Transportation"),
    "Private": ("private", "Private"),
    "U of A": ("other", "University of Arizona"),
    "Air Force": ("other", "U.S. Air Force"),
    "COT Parks & Rec": ("city", "City of Tucson Parks and Recreation"),
    "Tucson International Airport": ("other", "Tucson Airport Authority"),
    "City of South Tucson": ("town", "City of South Tucson"),
}

TRIBAL_NAMES = {
    "TOHONO O ODHAM NATION": "Tohono O'odham Nation",
    "TOHONO O ODHAM NATION SAN XAVIER DISTRICT": "Tohono O'odham Nation, San Xavier District",
    "PASCUA YAQUI NATION": "Pascua Yaqui Tribe",
}

TOWNS = {
    "TUCSON": ("city", "City of Tucson"),
    "MARANA": ("town", "Town of Marana"),
    "ORO VALLEY": ("town", "Town of Oro Valley"),
    "SAHUARITA": ("town", "Town of Sahuarita"),
    "SOUTH TUCSON": ("town", "City of South Tucson"),
}
