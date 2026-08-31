#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$project_dir/.venv/bin/python" "$project_dir/scripts/backup_database.py"
