# Production data (mobile app)

Production holds only the `app` schema: places, companies, cities and websites as the mobile app shows them.
It is built from Gold by [`scripts/app_schema.sql`](../scripts/app_schema.sql), which leaves out everything about
where the data came from (source names, lineage, match confidence, Wikidata / gazetteer ids, crawl statistics,
links to the data providers). The script refuses to publish if such a column or link appears.

## Each release

On your Mac, after the pipelines have refreshed Gold:

```bash
make app-dump                       # rebuilds the app schema, writes data/exports/app.dump (~31 MB)
scp -i <ssh-key> data/exports/app.dump <user>@<server>:~/app-$(date +%F).dump
```

On the server:

```bash
pg_restore --clean --if-exists --single-transaction --no-owner \
  -h localhost -U postgres -d karobar ~/app-YYYY-MM-DD.dump
psql -h localhost -U postgres -d karobar -c "SELECT version, row_counts FROM app.dataset_info"
```

- `--clean --if-exists` drops the previous `app` tables first, so only the new data remains (no duplicates).
- `--single-transaction`: all or nothing. If anything fails, the previous data stays live.
- Takes a few seconds; queries wait during that time.
- Roll back by restoring an older `app-*.dump`. Keep the last two or three.

## Server (one-time setup, done)

Oracle Linux 9 (aarch64) on OCI, PostgreSQL 17 + PostGIS 3.5 from the PGDG repo, database `karobar` with the
`postgis` and `pg_trgm` extensions. Port 5432 stays closed (OCI security list and firewalld); connect via SSH,
or from the Mac with a tunnel: `ssh -i <ssh-key> -N -L 5433:localhost:5432 <user>@<server>`.

PGDG's repo file uses `$releasever_minor`, which is empty on Oracle Linux. If `dnf` reports 404s for
`rhel-9.-aarch64` after a repo package update, re-apply:
`sudo sed -i 's/\$releasever_major\.\$releasever_minor/9/g' /etc/yum.repos.d/pgdg-redhat-all.repo`.

## Versions

The dump comes from the local PostgreSQL 15 (Postgres.app's `pg_dump`). It restores into 15 or anything newer.
Production must never run an older major version than local.
