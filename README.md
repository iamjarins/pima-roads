# Road Work History Map

Click any road in Pima County or Tucson and see the paving work on record for it: what was done, when,
by which contractor, under which project. Built from the engineering spec dated Nov 27, 2026.

## What is here

| Path | What it does |
| --- | --- |
| `pipeline/` | The nightly job: pull, snapshot, diff, normalize, match, build tiles |
| `db/schema.sql` | PostGIS schema (segment, work_event, project, contract, vendor, snapshots) |
| `api/` | Read-only history API; also serves the front end and tiles |
| `pipeline/export.py` | Static build of the whole site, for hosting without a server |
| `.github/workflows/publish.yml` | Nightly refresh and publish to GitHub Pages |
| `web/` | The one-screen map (MapLibre GL JS, no build step) |
| `tests/` | Unit tests and the spec's acceptance checks |

## Run it

Needs Python 3.11+, PostgreSQL with PostGIS, and outbound access to `gisdata.pima.gov` and `utility.arcgis.com`.

```bash
createdb roadwork && export DATABASE_URL=postgresql://USER:PASS@localhost/roadwork
make install
make refresh     # about 3 minutes: pulls 8 layers, matches, writes web/tiles
make test
make serve       # http://localhost:8000
```

Nightly refresh, for example in cron: `15 9 * * * cd /srv/roadwork && make refresh` (2:15 am Arizona).
A failed pull exits non-zero and leaves yesterday's data in place. `make report` prints match rates and
any record that matched no segment.

## Host it as a static site

`make site` (after `make refresh`) writes the whole site to `./site`: the front end, the tiles, and pre-built
history and search files. It needs no server and no database, so any static host works. The history files
are built by the same code as the API (`api/history.py`), so both modes show the same thing.

`.github/workflows/publish.yml` does this every night in GitHub Actions with a throwaway PostGIS container
and publishes to GitHub Pages. To turn it on: repo Settings → Pages → Source: GitHub Actions, then run the
"Refresh and publish" workflow once from the Actions tab. GitHub Pages on a free account needs a public repo.

Because the nightly database is thrown away, the static route does not keep the change log between pulls
(`record_change`); that needs a persistent database or saved snapshots.

## Decisions the spec left open

- **Base street network.** Pima County GIS "Street Network" (Transportation layer 11): 61,844 blocks with
  intersection ids and address ranges. Segment ids are the county `ROADID`, so shared links survive refreshes.
- **Who maintains a road**, strongest evidence first: road category (interstates and state routes are ADOT),
  the county Street Maintenance layer, the city pavement inventory custodian, tribal land, then town limits.
  `segment.owner_basis` records which rule fired, and the panel shows it. Roads in no public inventory read
  "not confirmed" rather than guessing.
- **A source record can cover many blocks**, so `work_event` holds one row per source record and
  `segment_event` links it to blocks with a confidence. This keeps the county event count equal to the
  source row count.
- **Matching.** A block belongs to a record when the record runs along at least half of it inside a 15 m
  buffer and the street name agrees (`high` at 80% or more, else `medium`). City neighbourhood packages name
  no street, so they match on geometry alone at 90% (`medium`). A county record that names a different
  street than the block is kept but flagged (`low`) and does not colour the map. A record shorter than its
  block is attached as `partial`.
- **City records with status Dropped** are stored but not shown.
- **Search** uses the county address ranges, so no outside geocoder is called.
- **Background map** is OpenFreeMap by default (`web/config.js`). The roads draw first and work without it.
- **Cost.** The contract tables exist and the two umbrella agreements from the spec are seeded, but nothing
  is linked, so every event reads "Cost not yet linked". Adding `job_order` or `project_contract` rows makes
  the panel show cost with its confidence.

## Not done

- Phase 3 (contracts, job orders, dollars) and phase 4.
- Terms of use for both GIS portals are still unchecked.
- Record edits are captured in `record_change` on every refresh but the panel does not show them yet.
- The meanings of JOC, HB, MP, ST and CS are inferred and labelled as such.
