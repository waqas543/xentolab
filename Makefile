# Data platform developer commands. Everything runs locally (no Docker required).
PY      ?= $(CURDIR)/.venv/bin/python
NVM     := source $$HOME/.nvm/nvm.sh >/dev/null && nvm use >/dev/null
PBF     ?=
COUNTRY ?= PK

.PHONY: help up start dev setup test data-platform-setup data-platform-up data-platform-api data-platform-worker \
        data-platform-web pipeline-osm-pakistan pipeline-osm pipeline-overture pipeline-geonames pipeline-wikidata \
        pipeline-commoncrawl pipeline-gdelt pipeline-opencorporates pipeline-all data-platform-test data-platform-migrate \
        data-platform-sync mobile-android app-schema app-dump
.DEFAULT_GOAL := help

help:                           ## list commands
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-24s %s\n", $$1, $$2}'

up:                             ## start everything (API, worker, web) in one terminal; Ctrl+C stops all
	@./scripts/dev.sh

start dev: up                   ## aliases for `make up`

setup: data-platform-setup      ## first-time install: Python + Node deps, migrations, sources

data-platform-setup:            ## install deps, migrate, register adapters
	$(PY) -m pip install -q -r backend/requirements.txt
	$(MAKE) data-platform-migrate data-platform-sync
	cd frontend && $(NVM) && npm install

data-platform-migrate:
	cd backend && $(PY) -m data_platform migrate

data-platform-sync:
	cd backend && $(PY) -m data_platform sync

data-platform-up: up           ## same as `make up`

data-platform-api:              ## API only (:8000)
	cd backend && $(PY) -m uvicorn app.main:app --port 8000 --reload

data-platform-worker:           ## one extra worker (scale out by running more)
	cd backend && $(PY) -m data_platform worker

data-platform-web:              ## web app only (:3000)
	cd frontend && $(NVM) && npm run dev -- --port 3000

pipeline-osm-pakistan:          ## PBF=/path/to/pakistan.osm.pbf to import a local file instead of downloading
	cd backend && $(PY) -m data_platform run osm.extract -p country=PK $(if $(PBF),-p source_path=$(PBF)) --wait

pipeline-osm:                   ## any country: COUNTRY=XX [PBF=...]
	cd backend && $(PY) -m data_platform run osm.extract -p country=$(COUNTRY) $(if $(PBF),-p source_path=$(PBF)) --wait

RUN = cd backend && $(PY) -m data_platform run

pipeline-overture:              ## Overture places for COUNTRY (default PK)
	$(RUN) overture.places -p country=$(COUNTRY) --wait

pipeline-geonames:              ## GeoNames gazetteer (cities, villages, postal codes) for COUNTRY
	$(RUN) geonames.country -p country=$(COUNTRY) --wait

pipeline-wikidata:              ## Wikidata organizations for COUNTRY (incremental after the first run)
	$(RUN) wikidata.organizations -p country=$(COUNTRY) --wait

pipeline-commoncrawl:           ## Common Crawl domain discovery for COUNTRY's ccTLD (+ known business domains)
	$(RUN) commoncrawl.domains -p country=$(COUNTRY) --wait

pipeline-gdelt:                 ## GDELT news mentions since the last run (HOURS=24 on the first run)
	$(RUN) gdelt.mentions -p country=$(COUNTRY) -p hours=$(or $(HOURS),24) --wait

pipeline-opencorporates:        ## OpenCorporates lookups (needs DP_SECRET_OPENCORPORATES_API_TOKEN)
	$(RUN) opencorporates.lookup -p country=$(COUNTRY) --wait

pipeline-all:                   ## queue every keyless source for COUNTRY in a sensible order (worker runs them)
	$(RUN) geonames.country -p country=$(COUNTRY)
	$(RUN) osm.extract -p country=$(COUNTRY) $(if $(PBF),-p source_path=$(PBF))
	$(RUN) overture.places -p country=$(COUNTRY)
	$(RUN) wikidata.organizations -p country=$(COUNTRY)
	$(RUN) commoncrawl.domains -p country=$(COUNTRY)
	$(RUN) gdelt.mentions -p country=$(COUNTRY)

test: data-platform-test        ## backend test suite

data-platform-test:
	cd backend && $(PY) -m pytest tests -q

mobile-android:                 ## build and run the mobile app on an Android emulator / USB phone (start Metro: cd mobile && npm start)
	cd mobile && $(NVM) && npm run android

# ---------------------------------------------------------------- production data (mobile app)
DB      ?= $(or $(DATABASE_URL),dbname=ds_companies)
# pg_dump must not be older than the local server (15); Postgres.app ships a matching one.
PG_BIN  ?= $(firstword $(wildcard /Applications/Postgres.app/Contents/Versions/latest/bin) $(dir $(shell command -v pg_dump)))
APP_DUMP ?= data/exports/app.dump

app-schema:                     ## rebuild the clean `app` schema (what the mobile app serves, no provenance) from gold
	$(PG_BIN)/psql -X -q -v ON_ERROR_STOP=1 --single-transaction -d "$(DB)" -f scripts/app_schema.sql
	@$(PG_BIN)/psql -X -At -d "$(DB)" -c "SELECT 'app schema ' || version || ': ' || row_counts::text FROM app.dataset_info"

app-dump: app-schema            ## dump the `app` schema to data/exports/app.dump for production (see docs/production.md)
	@mkdir -p $(dir $(APP_DUMP))
	$(PG_BIN)/pg_dump -Fc -Z 9 -n app --no-owner --no-privileges -d "$(DB)" -f $(APP_DUMP)
	@ls -lh $(APP_DUMP) | awk '{print "wrote " $$9 " (" $$5 ")"}'
