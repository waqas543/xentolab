#!/usr/bin/env sh
# Load an app dump (from `make app-dump`) into the db container, replacing the current data.
# All or nothing: if anything fails, the previous data stays live.
set -eu
file=${1:?usage: ./restore.sh path/to/app-YYYY-MM-DD.dump}
cd "$(dirname "$0")"
docker compose exec -T db pg_restore --clean --if-exists --single-transaction --no-owner \
  -U postgres -d karobar < "$file"
docker compose exec -T db psql -U postgres -d karobar -Atc \
  "SELECT 'live dataset ' || version || ': ' || row_counts::text FROM app.dataset_info"
