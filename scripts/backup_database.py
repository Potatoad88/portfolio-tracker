from datetime import datetime
from pathlib import Path
import sqlite3
import sys


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from brokers import BROKERS  # noqa: E402

paths = {broker.id: broker.database_default for broker in BROKERS.values()}
env_path = root / ".env"
if env_path.exists():
    settings = dict(line.split("=", 1) for line in env_path.read_text().splitlines()
                    if line and not line.lstrip().startswith("#") and "=" in line)
    for broker in BROKERS.values():
        if settings.get(broker.database_env):
            paths[broker.id] = settings[broker.database_env].strip().strip('"\'')

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
