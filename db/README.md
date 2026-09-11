# Database

Forward-only SQL migrations, applied in filename order by
`investment_intelligence.db.migrate`.

**There are no down migrations.** This is an append-only store whose value is
that its history is intact; a rollback that drops a column drops facts. If a
migration is wrong, write another one.

**Never edit an applied migration.** The runner records a SHA-256 of each file
and raises `MigrationDrift` if one changes, because an edited migration means
the schema in front of you is not the schema that ran.

## Local setup

```bash
brew install postgresql@17
LC_ALL="en_US.UTF-8" pg_ctl -D /opt/homebrew/var/postgresql@17 -l /tmp/pg17.log start
createdb ii_dev && createdb ii_test

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL=postgresql:///ii_dev PYTHONPATH=src .venv/bin/python -c \
  "from investment_intelligence.db import connect, migrate; \
   c=connect(); print(migrate(c)); c.commit()"
```

## Tests

```bash
TEST_DATABASE_URL=postgresql:///ii_test .venv/bin/python -m pytest
```
