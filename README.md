# Tiger Portfolio Tracker

A local, read-only dashboard for a Tiger Brokers Prime account. It displays current equity, cash, stocks, ETFs, funds, funding history, and contribution-adjusted performance in SGD or USD.

The browser talks only to the local FastAPI backend. Tiger credentials, the private key, and Tiger API calls stay in the backend process.

## What it does

- Fetches Prime assets, stock positions, fund positions, funding transactions, and analytics through the official `tigeropen` SDK.
- Stores successful results in a local SQLite database so a failed refresh cannot erase the last good data.
- Converts the entire dashboard using Tiger's account-level SGD/USD rates. Historical chart points use their own historical rates when Tiger supplies them.
- Separates stocks and ETFs from money-market and other fund positions.
- Exports positions, funding, and history as CSV without making another Tiger request.
- Remembers the selected currency and light/dark theme in browser storage.

It does not place orders, stream quotes, or expose a public server.

## Setup

Requires Python 3.10+ and Node.js 18+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
npm --prefix frontend install
cp .env.example .env
```

Fill in `.env`:

```dotenv
TIGER_ID=your_tiger_id
TIGER_ACCOUNT=your_account_number
TIGER_PRIVATE_KEY_PATH=/absolute/path/to/your/private_key.pem
TIGER_DB_PATH=backend/portfolio.db
TIGER_STALE_MINUTES=60
```

Use the private key format accepted by the Tiger SDK. Keep the PEM outside the repository if practical. The PEM, `.env`, databases, and backups are ignored by Git.

## Run

Start the backend and frontend together:

```sh
./start.sh
```

Open <http://127.0.0.1:5173>. Press **Refresh** to request fresh Tiger data. Press `Ctrl+C` in the terminal to stop both servers.

The backend is bound to `127.0.0.1:8000`; the frontend development server is bound to `127.0.0.1:5173`. `start.sh` sets Python's certificate bundle to Certifi's maintained CA store before launching the backend.

## How data moves

1. Loading the page reads the latest values from the local backend and SQLite database. It does not sync with Tiger.
2. Pressing **Refresh** sends `POST /api/sync`.
3. The backend signs read-only Tiger requests with the private key and verifies Tiger's HTTPS certificate using the Certifi CA bundle.
4. The adapter normalizes Tiger's response shapes and converts position values to the SGD storage currency.
5. All funding, history, snapshot, position, and sync-status changes commit in one SQLite transaction. On failure, the transaction rolls back and the previous data remains available.
6. After the first full history fetch, later syncs request only the latest history window. An unchanged account does not create a duplicate snapshot.
7. Changing chart range, currency, theme, or collapsed sections uses already-fetched local data and does not call Tiger.

## Calculations

All backend money calculations use Python `Decimal`; values are serialized as strings to avoid binary floating-point drift.

- **Net contributions** = completed deposits − withdrawals − withdrawal fees + withdrawal-related refunds.
- **Overall P&L** = total equity − net contributions.
- **Simple overall return** = overall P&L ÷ net contributions × 100. This is not a time-weighted return or XIRR.
- **Holdings value** = total equity − cash, as reported by Tiger.
- **Reconciliation difference** = total equity − cash − sum of displayed position market values. Tiger's stock-segment equity excludes separately returned fund positions, so the backend adds fund market value once before reconciling. Differences below one displayed currency unit are treated as valuation timing or rounding noise.
- **Chart performance** = historical equity − net contributions accumulated through that date.

Supported funding type codes are `1` deposit, `3` withdrawal, `20` withdrawal fee, `21` withdrawal refund, `22` failed-withdrawal refund, and `23` withdrawal-fee refund. Pending transactions and unrecognized types do not affect contributions. Non-SGD funding is rejected because cross-currency cash-flow accounting is not implemented.

## API

| Method | Path | Purpose | Calls Tiger |
| --- | --- | --- | --- |
| `POST` | `/api/sync` | Fetch and atomically store fresh account data | Yes |
| `GET` | `/api/summary?currency=SGD` | Summary cards and reconciliation | No |
| `GET` | `/api/positions?currency=SGD` | Latest positions | No |
| `GET` | `/api/funding?currency=SGD` | Funding history with labels | No |
| `GET` | `/api/history?currency=SGD` | Chart history and funding markers | No |
| `GET` | `/api/sync/status` | Last success, last error, and component health | No |
| `GET` | `/api/export/{positions|funding|history}` | Download local data as CSV | No |

Only `SGD` and `USD` are accepted as display currencies.

## Codebase map

```text
backend/
  adapter.py       Tiger SDK configuration, read-only requests, normalization
  calculations.py  Contribution and performance formulas
  database.py      SQLite schema, migrations, atomic persistence, deduplication
  main.py          FastAPI endpoints and display-currency conversion
  models.py        Immutable normalized data records
  tests/test_app.py
frontend/src/
  App.tsx           Dashboard, chart, tables, exports, and local preferences
  main.tsx          React entry point and MUI theme
scripts/
  backup_database.py
start.sh            Starts both local servers
backup.sh           Creates a consistent SQLite backup
```

The adapter is the trust boundary for changing Tiger SDK response formats. The database stores all monetary fields as decimal text. The frontend treats API monetary values as display data; financial formulas remain in the backend.

## Export and backup

Use **Export** in the dashboard to download current local data. The selected dashboard currency controls the export currency.

Create a consistent SQLite backup while the app is running or stopped:

```sh
./backup.sh
```

Backups are written to `backups/`. Only the database is copied; `.env` and the private key are not included.

## Tests and build

```sh
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
npm --prefix frontend run build
```

The backend suite covers contribution signs and statuses, currency normalization, fund classification, transactional rollback, snapshot deduplication, changed snapshots, historical FX, reconciliation, and CSV export.

## Troubleshooting

- **`Incorrect padding`**: the private key content or format is malformed. Point `TIGER_PRIVATE_KEY_PATH` at the PEM file rather than pasting an altered one-line value.
- **Certificate verification failed**: reinstall the virtual environment requirements and launch through `./start.sh`, which uses Certifi's CA bundle. Do not disable TLS verification.
- **`502 Bad Gateway` after Refresh**: open Sync health and inspect the displayed error. Previously synced data remains available.
- **Portfolio data may be stale**: the last successful Tiger sync is older than `TIGER_STALE_MINUTES`. Page reloads read local data; use **Refresh** for a new Tiger sync.
- **Reconciliation warning**: Tiger total equity does not equal cash plus the positions returned by the stock and fund endpoints. Refresh once; if it persists, an asset category may not be returned by those endpoints.

Tiger can change SDK response shapes and account capabilities. Validate the figures against Tiger after SDK upgrades before relying on them for financial decisions.
