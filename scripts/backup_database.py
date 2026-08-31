from datetime import datetime
from pathlib import Path
import sqlite3


root = Path(__file__).resolve().parents[1]
database_path = "backend/portfolio.db"
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("TIGER_DB_PATH="):
            database_path = line.split("=", 1)[1].strip().strip('"\'')
            break

source = Path(database_path).expanduser()
if not source.is_absolute():
    source = root / source
if not source.is_file():
    raise SystemExit(f"Database not found: {source}")

destination_dir = root / "backups"
destination_dir.mkdir(exist_ok=True)
destination = destination_dir / f"portfolio-{datetime.now():%Y%m%d-%H%M%S}.db"
with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
    original.backup(backup)
print(f"Backup created: {destination}")
