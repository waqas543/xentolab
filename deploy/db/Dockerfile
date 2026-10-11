# Postgres 17 + PostGIS for arm64 and amd64 (the postgis/postgis images are amd64-only).
FROM postgres:17-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
# Runs once, when the data volume is first created.
COPY extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
