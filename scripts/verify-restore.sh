#!/usr/bin/env bash
set -euo pipefail
backup_dir=${1:?usage: scripts/verify-restore.sh BACKUP_DIRECTORY}
(cd "$backup_dir" && sha256sum -c SHA256SUMS)
restore_db=swimmable_restore_check
scratch=$(mktemp -d)
trap 'docker compose exec -T db dropdb -U swimmable --if-exists "$restore_db" >/dev/null 2>&1 || true; rm -rf "$scratch"' EXIT
docker compose exec -T db dropdb -U swimmable --if-exists "$restore_db" >/dev/null
docker compose exec -T db createdb -U swimmable "$restore_db"
docker compose exec -T db pg_restore -U swimmable -d "$restore_db" --no-owner < "$backup_dir/database.dump"
docker compose exec -T db psql -U swimmable -d "$restore_db" -Atc 'SELECT count(*) FROM photos; SELECT count(*) FROM source_observations; SELECT count(*) FROM predictions;'
tar -C "$scratch" -xf "$backup_dir/photos.tar"
tar -C "$scratch" -cf /dev/null .
