# Portfolio Tracker

A local, read-only dashboard for Tiger Brokers Prime, Moomoo SG, and IBKR accounts. Broker tabs share one interface while keeping their credentials, refresh operations, sync health, exports, and SQLite data isolated.

The browser talks only to the local FastAPI backend. Tiger credentials remain in the backend process; Moomoo credentials remain inside Moomoo OpenD; IBKR credentials remain inside the local Client Portal Gateway.

## Features

- Tiger assets, stocks, funds, funding transactions, and analytics through the official `tigeropen` SDK.
- Moomoo SG assets and positions through the official `moomoo-api` SDK and local OpenD.
- IBKR balances and positions through the local Client Portal Gateway using Python standard-library HTTPS.
- Dynamic Home, Tiger, Moomoo, and IBKR tabs with persistent broker, currency, and theme preferences.
- SGD/USD display, interactive local history, collapsible holdings, reconciliation warnings, CSV export, and SQLite backups.
- Atomic syncs that preserve the previous broker snapshot when an upstream request fails.

The application has no order, trade-unlock, deal, quote, transfer, statement, Flex, or streaming code and binds only to localhost.

## API cost boundary

Moomoo advertises its developer platform as **“$0 Cost”** and states **“No additional API charges.”** Some real-time quote products require paid quote cards; this project does not create a quote context or request market data. It uses only account-list, account-funds, and cached position reads. Normal account, fund, custody, or executed-trade fees still apply independently of this dashboard.

- [Moomoo Developer Platform](https://open.moomoo.com/)
- [Moomoo OpenAPI fee documentation](https://openapi.moomoo.com/pdfs/moomoo-API-Doc-en-Python.pdf)
- [Moomoo trade API overview](https://openapi.moomoo.com/moomoo-api-doc/en/trade/overview.html)

Tiger likewise advertises free API-service access. The Tiger integration uses only account and history queries. [Tiger Open Platform](https://developer.itigerup.com/?lang=en_US&navType=quant_trading)

IBKR currently describes its trading Web API as free. Paid market-data subscriptions are separate; this integration calls only portfolio accounts, ledger, and positions and never requests market data. Pricing and broker policies can change, so verify the current official terms periodically.

- [IBKR Web API](https://ibkrcampus.com/campus/ibkr-api-page/web-api-account-management/)
- [IBKR market-data subscriptions](https://ibkrcampus.com/docs/general/market-data-subscriptions/introduction)

## Setup

Requires Python 3.10+, Node.js 18+, Moomoo OpenD 10.10+ for Moomoo, and Java plus Client Portal Gateway for IBKR.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
npm --prefix frontend install
cp .env.example .env
```

Configure `.env`:

```dotenv
TIGER_ID=your_tiger_id
TIGER_ACCOUNT=your_account_number
TIGER_PRIVATE_KEY_PATH=/absolute/path/to/your/private_key.pem
TIGER_DB_PATH=backend/portfolio.db
TIGER_STALE_MINUTES=60

MOOMOO_HOST=127.0.0.1
MOOMOO_PORT=11111
MOOMOO_ACCOUNT_ID=your_real_sg_account_id
MOOMOO_DB_PATH=backend/moomoo.db
MOOMOO_MANUAL_WITHDRAWALS=

IBKR_GATEWAY_URL=https://localhost:5000/v1/api
IBKR_ACCOUNT_ID=
IBKR_DB_PATH=backend/ibkr.db
IBKR_VERIFY_SSL=false
```

Keep the Tiger PEM outside the repository if practical. `.env`, PEM/key formats, databases, and backups are ignored by Git.

### Moomoo OpenD

1. Download and install the current visual Moomoo OpenD from the [official OpenD documentation](https://openapi.moomoo.com/moomoo-api-doc/en/opend/opend-intro.html).
2. Log in with the Moomoo account that owns the SG account and complete the API questionnaire/agreement if prompted.
3. Leave OpenD listening on `127.0.0.1:11111`; do not expose it to the network.
4. Copy the real SG universal securities account ID into `MOOMOO_ACCOUNT_ID`.
5. OpenD does not need trading to be unlocked because the tracker performs no trading operation.

### IBKR Client Portal Gateway

1. Install Java and download the current [Client Portal Gateway](https://ibkrcampus.com/docs/web-api/authentication/cpgw/installation-authentication) from IBKR.
2. Extract it and start the Gateway from its directory with `bin/run.sh root/conf.yaml` on macOS/Linux (or `bin\run.bat root\conf.yaml` on Windows).
3. Visit <https://localhost:5000>, accept the expected localhost self-signed-certificate warning, and sign in. IBKR requires browser reauthentication after the session expires, normally at least daily.
4. Set `IBKR_GATEWAY_URL=https://localhost:5000/v1/api`. Leave `IBKR_ACCOUNT_ID` blank only when the Gateway exposes exactly one account; otherwise a failed refresh lists the account IDs you can select.
5. Keep the Gateway on localhost. The tracker stores no IBKR username, password, session token, or trading credential.

`IBKR_VERIFY_SSL=false` is accepted only for a loopback host because the local Gateway uses a self-signed certificate. Remote URLs must use normal certificate verification.

## Run

Start and log in to OpenD before refreshing Moomoo, and start/authenticate Client Portal Gateway before refreshing IBKR. Then run:

```sh
./start.sh
```

Open <http://127.0.0.1:5173>. Press **Refresh** to sync only the selected broker. Switching tabs, changing chart range, changing currency, exporting, or reloading the webpage reads local data and does not contact any broker. On the Moomoo tab, use **Fetch cash flow** under Deposits & withdrawals and add up to 20 known transaction dates; ordinary Refresh does not fetch cash flow.

Press `Ctrl+C` to stop the tracker. OpenD and Client Portal Gateway are separate applications and must be stopped separately.

## Broker behavior

### Tiger

- Fetches account assets in SGD/USD, stock/fund positions, funding history, and analytics.
- Backfills portfolio analytics and then syncs history incrementally.
- Shows net contributions, overall P&L, simple return, funding history, and contribution-adjusted performance.

### Moomoo

- Targets one real Moomoo SG universal securities account through `SecurityFirm.FUTUSG` and `TrdEnv.REAL`.
- Reads OpenD's position cache and account assets in the currencies needed to normalize values to SGD; no quote API is used.
- Shows current total equity, cash, holdings, listed-position unrealized P&L, and an aggregate fund-assets row when Moomoo reports funds outside its position list.
- Builds equity history locally from changed snapshots beginning with the first successful sync.
- Stores raw cash-flow records locally for auditability, while the dashboard and funding CSV show only verified deposits and withdrawals.
- Contribution P&L includes only SGD DDI-tagged deposits, explicit `Bank Transfer Withdrawals`, and locally confirmed `date:amount` entries in `MOOMOO_MANUAL_WITHDRAWALS`. Fund activity, trades, conversions, dividends, interest, and every other cash flow remain excluded.
- Normal Moomoo Refresh updates portfolio assets and positions only; it does not request cash flow.
- **Fetch cash flow** accepts up to 20 known clearing dates, queries each date once, records the last successful cash-flow sync, and deduplicates returned rows by Moomoo cash-flow ID.

Moomoo documents a limit of 10 account-funds requests and 10 position requests per 30 seconds per account, but applies those limits only when `refresh_cache=True`. This tracker always uses `refresh_cache=False`, so refreshes read OpenD's locally synchronized cache. Cash flow is limited separately to 20 daily requests per 30 seconds, so each explicit cash-flow fetch accepts at most 20 selected dates. See the official [account-funds](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-funds.html), [positions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html), and [cash-flow](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-cash-flow.html) documentation.

### IBKR

- Supports one real account with SGD as its base currency.
- Refresh calls exactly `GET /portfolio/accounts`, `GET /portfolio/{accountId}/ledger`, and `GET /portfolio2/{accountId}/positions` through the authenticated local Gateway.
- Shows equity, cash, holdings, account and position unrealized P&L, locally accumulated equity history, and stocks/ETFs. Unexpected asset classes remain visible under Other holdings.
- Makes no quote, order, trade, transfer, statement, or Flex request.
- Has no contribution, return, performance, or funding-history view in v1. Deposits and withdrawals are intentionally deferred to a future Flex integration.
- Loading the page and switching tabs use only `backend/ibkr.db`; only Refresh contacts the Gateway.

Official endpoint references: [accounts](https://ibkrcampus.com/docs/web-api/api-reference/trading/trading-portfolio/get-all-accounts), [account ledger](https://ibkrcampus.com/docs/web-api/api-reference/trading/trading-portfolio/get-portfolio-ledger), and [positions](https://ibkrcampus.com/docs/web-api/api-reference/trading/trading-portfolio/get-uncached-positions).


## Calculations

The backend uses Python `Decimal` and serializes money as strings.

- **Tiger net contributions** = completed deposits − withdrawals − withdrawal fees + applicable refunds.
- **Tiger overall P&L** = total equity − net contributions.
- **Tiger simple return** = overall P&L ÷ net contributions × 100; it is not TWR or XIRR.
- **Moomoo net contributions** = verified SGD DDI deposits − explicit and locally confirmed bank withdrawals. Unclassified raw cash flows contribute zero.
- **Moomoo overall P&L and simple return** use the same formulas as Tiger after that conservative classification.
- **Home overall P&L** = combined cached equity − combined net contributions, converted with each broker’s stored FX rate. It is unavailable whenever a configured broker lacks cached data or a represented broker, including IBKR v1, lacks contribution history.
- **Holdings value** = total equity − cash.
- **Reconciliation difference** = total equity − cash − displayed positions.
- **Moomoo unrealized P&L** = sum of unrealized P&L returned for listed positions; aggregate fund P&L is unavailable.
- **IBKR unrealized and realized P&L** come from the SGD-base `BASE` ledger; listed position unrealized P&L is normalized to SGD with ledger exchange rates.

Differences below one displayed currency unit are treated as valuation timing or rounding noise.

## Local API

Broker-aware endpoints accept any ID returned by `GET /api/brokers`; omitting `broker` preserves the original Tiger behavior.

| Method | Path | Purpose | Broker request |
| --- | --- | --- | --- |
| `GET` | `/api/brokers` | Supported brokers, configuration state, and capabilities | No |
| `POST` | `/api/sync?broker=ibkr` | Fetch and atomically store current portfolio data | Yes |
| `POST` | `/api/cash-flow/sync?broker=moomoo` | Fetch up to 20 explicitly selected cash-flow dates | Yes |
| `GET` | `/api/overview?currency=SGD` | Cached cross-broker totals, allocation, and health | No |
| `GET` | `/api/summary?currency=SGD&broker=ibkr` | Summary and capabilities | No |
| `GET` | `/api/positions?currency=SGD&broker=ibkr` | Latest positions | No |
| `GET` | `/api/funding?currency=SGD&broker=tiger` | Tiger funding history | No |
| `GET` | `/api/history?currency=SGD&broker=ibkr` | Local chart history | No |
| `GET` | `/api/sync/status?broker=ibkr` | Broker-specific sync health | No |
| `GET` | `/api/export/{dataset}?broker=ibkr` | Broker-specific CSV | No |

Only SGD and USD are accepted as display currencies.

## Codebase map

```text
backend/
  adapter.py          Tiger read-only normalization
  moomoo_adapter.py   Moomoo/OpenD read-only normalization
  ibkr_adapter.py     IBKR Gateway read-only normalization
  brokers.py          Broker registry, capabilities, and contribution rules
  calculations.py     Contribution and performance formulas
  database.py         SQLite schema, transactions, and deduplication
  main.py             Registry-driven FastAPI orchestration
  models.py           Immutable normalized records
frontend/src/
  App.tsx              Broker discovery and selected-view coordination
  api.ts               Shared local API and money formatting
  types.ts             Broker and portfolio response types
  components/          Shared header, chart, tables, sections, and dialogs
  pages/               Home and broker portfolio pages
  main.tsx             Theme and React entry point
scripts/
  backup_database.py   Registry-driven database backups
```

Adapters are the trust boundaries for broker response formats. Tiger uses `backend/portfolio.db`; Moomoo uses `backend/moomoo.db`; IBKR uses `backend/ibkr.db`. Shared storage, aggregation, exports, health, and navigation consume only normalized models and broker capabilities.

To add another broker, implement its read-only adapter, register its metadata and capabilities in `backend/brokers.py`, add its environment variables, and add fake-response normalization tests. Navigation, overview aggregation, configuration state, and backups then include it automatically.

## Export and backup

Dashboard exports use only the selected broker's local database. Moomoo's funding export contains only the verified deposits and withdrawals used for contribution calculations.

```sh
./backup.sh
```

The command backs up every broker database that exists into the ignored `backups/` directory. It never copies `.env` or the Tiger private key.

## Tests and build

```sh
npm --prefix frontend run format:check
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
npm --prefix frontend run build
```

Tests cover the broker registry, configuration and capabilities, dynamic overview iteration, financial signs, Tiger, Moomoo, and IBKR normalization, mixed-currency conversion, aggregate funds, context closure, broker isolation, rollback, deduplication, migrations, reconciliation, history, and CSV export. Moomoo and IBKR tests use fake upstream responses and make no broker request.

GitHub Actions runs these checks on every push and pull request. Use `npm --prefix frontend run format` to apply frontend formatting locally.

## Troubleshooting

- **IBKR Gateway unavailable or login required**: start Client Portal Gateway, visit <https://localhost:5000>, authenticate, and retry. A failed refresh preserves cached data.
- **Several IBKR accounts are visible**: set `IBKR_ACCOUNT_ID` to one of the IDs listed by the refresh error.
- **Moomoo OpenD connection failed**: start OpenD, log in, verify port `11111`, and keep it on localhost.
- **Account ID is not available**: confirm `MOOMOO_ACCOUNT_ID` is the real SG securities account exposed by the logged-in OpenD user.
- **Moomoo data appears unchanged**: position and account reads intentionally use OpenD's synchronized cache. Restart or refresh OpenD if its cache is stale.
- **Tiger `Incorrect padding`**: point `TIGER_PRIVATE_KEY_PATH` at the original PEM rather than pasting an altered one-line key.
- **Tiger certificate verification failed**: reinstall requirements and use `./start.sh`, which supplies Certifi's CA bundle; do not disable verification for Tiger.
- **Stale warning**: the selected broker's last successful sync is older than `TIGER_STALE_MINUTES`.
- **Reconciliation warning**: refresh once; if it persists, the broker may report an unsupported or in-transit asset outside returned positions.

Broker SDKs and account capabilities can change. Compare results with the official apps after dependency upgrades before relying on the figures for financial decisions.
