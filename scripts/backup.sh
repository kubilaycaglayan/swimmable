#!/usr/bin/env bash
set -euo pipefail
backup_dir=${1:?usage: scripts/backup.sh BACKUP_DIRECTORY}
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
docker compose exec -T db pg_dump -U swimmable -Fc swimmable > "$backup_dir/database.dump"
docker compose exec -T api tar -C /private -cf - . > "$backup_dir/photos.tar"
(cd "$backup_dir" && sha256sum database.dump photos.tar > SHA256SUMS)
chmod 600 "$backup_dir"/*
