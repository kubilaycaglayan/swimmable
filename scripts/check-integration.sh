#!/usr/bin/env bash
set -euo pipefail

run_case() {
  local database_name=$1
  local test_file=$2
  docker compose exec -T db createdb -U swimmable "$database_name"
  if ! docker compose exec -T api python - < "$test_file"; then
    docker compose exec -T db dropdb -U swimmable "$database_name"
    return 1
  fi
  docker compose exec -T db dropdb -U swimmable "$database_name"
}

run_case swimmable_selection_test tests/integration_db.py
run_case swimmable_budget_test tests/integration_budget.py
run_case swimmable_retention_test tests/integration_retention.py
run_case swimmable_recovery_test tests/integration_recovery.py
run_case swimmable_upload_test tests/integration_upload_race.py
