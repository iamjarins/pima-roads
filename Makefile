# Road Work History Map. See README.md.
PY ?= python3
PORT ?= 8000

install:        ## Python dependencies
	$(PY) -m pip install -r requirements.txt

refresh:        ## The nightly job: pull every source, match, rebuild tiles
	$(PY) -m pipeline.run

serve:          ## History API + front end + tiles on one port
	$(PY) -m uvicorn api.app:app --host 0.0.0.0 --port $(PORT)

test:           ## Unit tests, then the spec's acceptance checks against the loaded database
	$(PY) -m pytest -q

site:           ## Static build of the whole site in ./site (no server or database needed to host it)
	$(PY) -m pipeline.export --out site

report:         ## Match rates from the last refresh
	$(PY) -m pipeline.report

.PHONY: install refresh serve test site report
