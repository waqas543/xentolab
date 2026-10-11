# Production (mobile app API + database)

Production runs two containers from [`deploy/compose.yaml`](../deploy/compose.yaml):

| Service | What | Port |
|---|---|---|
| `api` | `xento_api`: only the mobile app endpoints (`/api/app/...`, `/api/health`) | `API_PORT` (default 8001) |
| `db` | PostgreSQL 17 + PostGIS, database `karobar`, data in the `pgdata` volume | not published |

The database holds only the `app` schema: places, companies, cities and websites as the app shows them, built
from Gold by [`scripts/app_schema.sql`](../scripts/app_schema.sql). It leaves out everything about where the data came
from (source names, lineage, match confidence, Wikidata / gazetteer ids, crawl statistics, links to the data
providers) and refuses to publish if such a column or link appears.

Never expose `app.main` (the development API): it also serves the data-platform endpoints and the SQL query runner.

## First deployment

On the server (Docker with the compose plugin installed):

```bash
git clone https://github.com/waqas543/xentolab.git ~/apps/xentolab
cd ~/apps/xentolab/deploy
cp .env.example .env            # set a long random POSTGRES_PASSWORD
docker compose up -d --build    # builds both images; the db creates postgis + pg_trgm on first start
```

Then load the data (next section) and open the API port in the firewall and the cloud security list.

## Each data release

On the Mac, after the pipelines have refreshed Gold:

```bash
make app-dump                   # rebuilds the app schema, writes data/exports/app.dump (~31 MB)
scp -i <ssh-key> data/exports/app.dump <user>@<server>:~/app-$(date +%F).dump
```

On the server:

```bash
~/apps/xentolab/deploy/restore.sh ~/app-YYYY-MM-DD.dump
```

`restore.sh` replaces the previous data in one transaction (all or nothing) and prints the live dataset version.
The API notices the new version on the next request. Roll back by restoring an older dump; keep the last two or three.

## Code updates

```bash
cd ~/apps/xentolab && git pull
cd deploy && docker compose up -d --build
```

## Checks

```bash
curl -s http://localhost:8001/api/health
curl -s http://localhost:8001/api/app/home | head -c 300
docker compose logs --tail 50 api
docker compose exec db psql -U postgres -d karobar     # SQL shell
```

## Versions

The dump comes from the local PostgreSQL 15 (Postgres.app's `pg_dump`) and restores into 15 or anything newer;
the db container runs 17. Production must never run an older major version than local.
