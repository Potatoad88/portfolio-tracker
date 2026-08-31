from datetime import datetime
from pathlib import Path
import sqlite3


root = Path(__file__).resolve().parents[1]
paths = {"tiger": "backend/portfolio.db", "moomoo": "backend/moomoo.db"}
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        for broker, variable in (("tiger", "TIGER_DB_PATH"), ("moomoo", "MOOMOO_DB_PATH")):
            if line.startswith(f"{variable}="):
                paths[broker] = line.split("=", 1)[1].strip().strip('"\'')

destination_dir = root / "backups"
destination_dir.mkdir(exist_ok=True)
created = []
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
for broker, configured_path in paths.items():
    source = Path(configured_path).expanduser()
    if not source.is_absolute():
        source = root / source
    if not source.is_file():
        continue
    destination = destination_dir / f"{broker}-{timestamp}.db"
    with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
        original.backup(backup)
    created.append(destination)

if not created:
    raise SystemExit("No portfolio databases found")
print("Backups created:")
print(*created, sep="\n")
