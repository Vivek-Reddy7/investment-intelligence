# Commands, in one place.
#
# The README describes what these do; this file is what actually runs, so the
# two cannot drift into describing different things. A doc that says
# `PYTHONPATH=src python -m ...` goes stale the first time an argument changes;
# `make backfill` does not.
#
# Every target is safe to run twice. Migrations are recorded, seeding is
# idempotent, and a backfill re-run is a no-op — so there is no order you can
# get wrong badly enough to need a reset.

PG_BIN      := /opt/homebrew/opt/postgresql@17/bin
PY          := .venv/bin/python
PIP         := .venv/bin/pip
export PYTHONPATH := src
DEV_DB      ?= ii_dev
TEST_DB     ?= ii_test
export DATABASE_URL      ?= postgresql:///$(DEV_DB)
export TEST_DATABASE_URL ?= postgresql:///$(TEST_DB)

.PHONY: help setup db-start db-create migrate seed backfill incremental \
        schedule status quality health gaps test test-cov web web-check \
        audit clean-db doctor

help:
	@echo "Setup:     make setup       — venv, dependencies, databases, migrations"
	@echo "           make doctor      — check the toolchain before anything else"
	@echo "Data:      make seed        — register the source and the tracked companies"
	@echo "           make backfill    — load history from SEC EDGAR (~10s, hits the network)"
	@echo "           make incremental — the daily cycle (cheap no-op when nothing is new)"
	@echo "Operate:   make status      — the operational snapshot"
	@echo "           make quality     — data quality report"
	@echo "           make health      — is ingestion current?"
	@echo "Develop:   make test        — the full suite"
	@echo "           make test-cov    — with a coverage report"
	@echo "           make web         — the site on http://localhost:3100"
	@echo "           make audit       — dependency vulnerability scan"
	@echo "Factors:   make prices      — backfill prices, LOCAL RESEARCH ONLY (§03-data-sources.md §7)"
	@echo "           make factors     — compute today's factor score"
	@echo "           make technicals  — compute today's technical indicators"
	@echo "           make combined    — blend fundamentals + technicals into one score"
	@echo "           make sectors     — fetch SIC/sector classification (not local-only)"
	@echo "           make marketcap   — compute market cap (no cap-tier label, see migration 028)"
	@echo "           make backtest    — validate the combined score against actual forward returns"
	@echo "           make size AMOUNT=5000 — split an amount across the top-ranked names"
	@echo "           make factor-report — regenerate and open the local-only viewer"

# ---------------------------------------------------------------------------

doctor:
	@echo "postgres:  $$( [ -x $(PG_BIN)/psql ] && $(PG_BIN)/psql --version || echo 'MISSING — brew install postgresql@17' )"
	@echo "python:    $$(python3 --version 2>&1)"
	@echo "node:      $$(node --version 2>/dev/null || echo 'MISSING — needed only for make web')"
	@echo "venv:      $$( [ -x $(PY) ] && echo present || echo 'MISSING — run make setup' )"
	@echo "server up: $$( $(PG_BIN)/pg_isready -q 2>/dev/null && echo yes || echo 'no — run make db-start' )"

setup: db-start db-create
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt pytest-cov
	$(MAKE) migrate
	@echo
	@echo "Ready. Next: make seed && make backfill"

# LC_ALL is not optional. Without it PostgreSQL 17 on macOS fails to start
# with "postmaster became multithreaded during startup", which names the
# symptom and not the cause.
db-start:
	@$(PG_BIN)/pg_isready -q || \
	  LC_ALL="en_US.UTF-8" $(PG_BIN)/pg_ctl -D /opt/homebrew/var/postgresql@17 \
	    -l /tmp/pg17.log start

db-create:
	@$(PG_BIN)/createdb $(DEV_DB) 2>/dev/null || true
	@$(PG_BIN)/createdb $(TEST_DB) 2>/dev/null || true
	@echo "databases: $(DEV_DB), $(TEST_DB)"

migrate:
	@$(PY) -c "from investment_intelligence.db import connect, migrate; \
	  c = connect(); applied = migrate(c); c.commit(); \
	  print('migrations applied:', applied or 'none pending')"

# ---------------------------------------------------------------------------

seed:
	$(PY) -m investment_intelligence.cli seed
	$(PY) -m investment_intelligence.cli schedule --max-age-days 3

backfill:
	$(PY) -m investment_intelligence.cli backfill --since 2015-01-01

incremental:
	$(PY) -m investment_intelligence.cli incremental

schedule:
	$(PY) -m investment_intelligence.cli schedule --max-age-days 3

# These exit non-zero when something is wrong, which is the point — they are
# meant to be usable as a check, not just read.
status:
	-@$(PY) -m investment_intelligence.cli status

quality:
	-@$(PY) -m investment_intelligence.cli quality

health:
	-@$(PY) -m investment_intelligence.cli health

gaps:
	-@$(PY) -m investment_intelligence.cli gaps

# ---------------------------------------------------------------------------

# LOCAL RESEARCH ONLY. Yahoo's terms restrict redistributing yfinance-sourced
# data for a public, customer-facing product — see docs/03-data-sources.md
# §7. Nothing under this heading is wired into `make web` or the deployed
# app, and factor-report writes to local-only/, which is gitignored.
prices:
	$(PY) -m investment_intelligence.cli prices --since 2015-01-01

factors:
	$(PY) -m investment_intelligence.cli factors --as-of today

technicals:
	$(PY) -m investment_intelligence.cli technicals --as-of today

combined:
	$(PY) -m investment_intelligence.cli combined --as-of today

# Not local-research-only -- see migration 027 -- but grouped here anyway
# since every command in this section is part of the same pipeline.
sectors:
	$(PY) -m investment_intelligence.cli sectors

marketcap:
	$(PY) -m investment_intelligence.cli marketcap --as-of today

# Recomputes combined scores at ~19 historical checkpoints (2017-2026) and
# checks them against actual subsequent price moves. Takes longer than the
# other targets -- it is re-running the full scoring pipeline once per
# checkpoint, not one now-only pass.
backtest:
	$(PY) -m investment_intelligence.cli backtest

# AMOUNT and CURRENCY are make variables, not shell args: `make size AMOUNT=500`.
AMOUNT   ?= 5000
CURRENCY ?= USD
size:
	$(PY) -m investment_intelligence.cli size --amount $(AMOUNT) --currency $(CURRENCY) --top 5

factor-report:
	$(PY) scripts/local_factor_report.py
	@open local-only/factor_report.html 2>/dev/null || \
	  echo "open local-only/factor_report.html in a browser"

# ---------------------------------------------------------------------------

test:
	$(PY) -m pytest -q

test-cov:
	$(PY) -m pytest -q --cov=investment_intelligence --cov-report=term-missing

audit:
	-$(PIP) install -q pip-audit && .venv/bin/pip-audit --strict --desc
	-cd web && npm audit --audit-level=high

# ---------------------------------------------------------------------------

# The web app needs a role that inherits ii_app: SELECT on market data, full
# CRUD on the user schema, and no ability to rewrite history. Created here
# rather than in a migration because it needs a password, and a password in a
# migration is a password in git.
web-role:
	@$(PG_BIN)/psql -q -d $(DEV_DB) -v ON_ERROR_STOP=1 -c \
	  "DO \$$\$$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ii_web') \
	   THEN CREATE ROLE ii_web LOGIN PASSWORD 'devonly'; END IF; END \$$\$$; \
	   GRANT ii_app TO ii_web;"
	@echo "role ii_web ready (local password: devonly — see web/env.example)"

# The port check is not fussiness. A dev server left running from an earlier
# session keeps serving from its own stale build, so the site returns 500s
# that look like an application bug while `next dev` reports EADDRINUSE
# somewhere further up the scrollback. Found by running this from clean.
web: web-role
	@lsof -ti:3100 >/dev/null 2>&1 && { \
	  echo "Port 3100 is already in use — an old dev server is probably still"; \
	  echo "running. It will keep serving stale content. Free it with:"; \
	  echo "    lsof -ti:3100 | xargs kill"; \
	  exit 1; } || true
	@[ -f web/.env.local ] || (cp web/env.example web/.env.local && \
	  sed -i '' 's/REPLACE_ME/devonly/' web/.env.local && \
	  echo "created web/.env.local from the example")
	cd web && npm install --no-fund --no-audit && npm run dev

web-check:
	cd web && npx tsc --noEmit && npm run build

# ---------------------------------------------------------------------------

# Deliberately not part of any other target. Dropping the dev database throws
# away the ingested history, and while a backfill can rebuild it, that spends
# ten seconds of somebody else's rate limit for no reason.
clean-db:
	@echo "This drops $(DEV_DB) and $(TEST_DB). Ctrl-C to stop." && sleep 3
	-$(PG_BIN)/dropdb --if-exists $(DEV_DB)
	-$(PG_BIN)/dropdb --if-exists $(TEST_DB)
	$(MAKE) db-create migrate
