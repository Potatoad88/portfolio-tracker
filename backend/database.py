import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from models import Funding, HistoryPoint, Snapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_transactions (
 transaction_id TEXT PRIMARY KEY, type TEXT NOT NULL, currency TEXT NOT NULL, amount TEXT NOT NULL,
 business_date TEXT NOT NULL, completed INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 direction TEXT NOT NULL DEFAULT '', settlement_date TEXT, remark TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
 id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, reporting_currency TEXT NOT NULL, total_equity TEXT NOT NULL,
 cash TEXT NOT NULL, holdings_value TEXT NOT NULL, unrealized_pnl TEXT NOT NULL, realized_pnl TEXT NOT NULL,
 sgd_to_usd TEXT NOT NULL DEFAULT '1');
CREATE TABLE IF NOT EXISTS positions (
 id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshots(id) ON DELETE CASCADE,
 symbol TEXT NOT NULL, name TEXT NOT NULL, market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
 average_cost TEXT NOT NULL, market_price TEXT NOT NULL, market_value TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
 asset_type TEXT NOT NULL DEFAULT 'STK');
CREATE TABLE IF NOT EXISTS history (captured_at TEXT PRIMARY KEY, total_equity TEXT NOT NULL, sgd_to_usd TEXT NOT NULL DEFAULT '1');
CREATE TABLE IF NOT EXISTS sync_state (id INTEGER PRIMARY KEY CHECK(id=1), last_success TEXT, last_error TEXT,
 components TEXT NOT NULL DEFAULT '{}', cash_flow_checked_through TEXT, cash_flow_last_success TEXT,
 cash_flow_complete_since TEXT);
INSERT OR IGNORE INTO sync_state(id) VALUES(1);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(portfolio_snapshots)")}
            if "sgd_to_usd" not in columns:
                db.execute("ALTER TABLE portfolio_snapshots ADD COLUMN sgd_to_usd TEXT NOT NULL DEFAULT '1'")
            position_columns = {row[1] for row in db.execute("PRAGMA table_info(positions)")}
            if "asset_type" not in position_columns:
                db.execute("ALTER TABLE positions ADD COLUMN asset_type TEXT NOT NULL DEFAULT 'STK'")
            history_columns = {row[1] for row in db.execute("PRAGMA table_info(history)")}
            if "sgd_to_usd" not in history_columns:
                db.execute("ALTER TABLE history ADD COLUMN sgd_to_usd TEXT NOT NULL DEFAULT '1'")
            state_columns = {row[1] for row in db.execute("PRAGMA table_info(sync_state)")}
            if "components" not in state_columns:
                db.execute("ALTER TABLE sync_state ADD COLUMN components TEXT NOT NULL DEFAULT '{}'")
            if "cash_flow_checked_through" not in state_columns:
                db.execute("ALTER TABLE sync_state ADD COLUMN cash_flow_checked_through TEXT")
            if "cash_flow_last_success" not in state_columns:
                db.execute("ALTER TABLE sync_state ADD COLUMN cash_flow_last_success TEXT")
            if "cash_flow_complete_since" not in state_columns:
                db.execute("ALTER TABLE sync_state ADD COLUMN cash_flow_complete_since TEXT")
            db.execute("""UPDATE sync_state SET cash_flow_checked_through=substr(last_success,1,10)
                       WHERE cash_flow_checked_through IS NULL AND last_success IS NOT NULL""")
            funding_columns = {row[1] for row in db.execute("PRAGMA table_info(funding_transactions)")}
            for column, definition in (("direction", "TEXT NOT NULL DEFAULT ''"),
                                       ("settlement_date", "TEXT"),
                                       ("remark", "TEXT NOT NULL DEFAULT ''")):
                if column not in funding_columns:
                    db.execute(f"ALTER TABLE funding_transactions ADD COLUMN {column} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def sync(self, snapshot: Snapshot, funding: list[Funding], history: list[HistoryPoint], components: dict[str, str] | None = None,
             cash_flow_checked_through: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for f in funding:
                db.execute("""INSERT INTO funding_transactions(transaction_id,type,currency,amount,business_date,completed,created_at,updated_at,direction,settlement_date,remark)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(transaction_id) DO UPDATE SET type=excluded.type,currency=excluded.currency,amount=excluded.amount,
                    business_date=excluded.business_date,completed=excluded.completed,updated_at=excluded.updated_at,
                    direction=excluded.direction,settlement_date=excluded.settlement_date,remark=excluded.remark""",
                    (f.transaction_id, f.type, f.currency, str(f.amount), f.business_date.isoformat(), int(f.completed), now, now,
                     f.direction, f.settlement_date.isoformat() if f.settlement_date else None, f.remark))
            if self._snapshot_changed(db, snapshot):
                cur = db.execute("INSERT INTO portfolio_snapshots(captured_at,reporting_currency,total_equity,cash,holdings_value,unrealized_pnl,realized_pnl,sgd_to_usd) VALUES(?,?,?,?,?,?,?,?)",
                                 (snapshot.captured_at.isoformat(), snapshot.reporting_currency, str(snapshot.total_equity), str(snapshot.cash), str(snapshot.holdings_value), str(snapshot.unrealized_pnl), str(snapshot.realized_pnl), str(snapshot.sgd_to_usd)))
                for p in snapshot.positions:
                    db.execute("INSERT INTO positions(snapshot_id,symbol,name,market,currency,quantity,average_cost,market_price,market_value,unrealized_pnl,asset_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (cur.lastrowid, p.symbol, p.name, p.market, p.currency, str(p.quantity), str(p.average_cost), str(p.market_price), str(p.market_value), str(p.unrealized_pnl), p.asset_type))
            for h in history:
                db.execute("INSERT INTO history(captured_at,total_equity,sgd_to_usd) VALUES(?,?,?) ON CONFLICT(captured_at) DO UPDATE SET total_equity=excluded.total_equity,sgd_to_usd=excluded.sgd_to_usd", (h.captured_at.isoformat(), str(h.total_equity), str(h.sgd_to_usd)))
            db.execute("""UPDATE sync_state SET last_success=?,last_error=NULL,components=?,
                       cash_flow_checked_through=COALESCE(?,cash_flow_checked_through) WHERE id=1""",
                       (now, json.dumps(components or {}), cash_flow_checked_through))

    @staticmethod
    def _snapshot_changed(db: sqlite3.Connection, snapshot: Snapshot) -> bool:
        latest = db.execute("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not latest:
            return True
        fields = ("total_equity", "cash", "holdings_value", "unrealized_pnl", "realized_pnl", "sgd_to_usd")
        if latest["reporting_currency"] != snapshot.reporting_currency or any(Decimal(latest[field]) != getattr(snapshot, field) for field in fields):
            return True
        stored = db.execute("SELECT symbol,name,market,currency,quantity,average_cost,market_price,market_value,unrealized_pnl,asset_type FROM positions WHERE snapshot_id=? ORDER BY asset_type,market,symbol", (latest["id"],)).fetchall()
        expected = sorted(snapshot.positions, key=lambda p: (p.asset_type, p.market, p.symbol))
        if len(stored) != len(expected):
            return True
        numeric = ("quantity", "average_cost", "market_price", "market_value", "unrealized_pnl")
        text = ("symbol", "name", "market", "currency", "asset_type")
        return any(any(row[field] != getattr(position, field) for field in text) or any(Decimal(row[field]) != getattr(position, field) for field in numeric) for row, position in zip(stored, expected))

    def record_error(self, message: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE sync_state SET last_error=? WHERE id=1", (message,))

    def sync_cash_flows(self, funding: list[Funding], complete_since: str | date | None = None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        if isinstance(complete_since, date):
            complete_since = complete_since.isoformat()
        with self.connect() as db:
            for item in funding:
                db.execute("""INSERT INTO funding_transactions(transaction_id,type,currency,amount,business_date,completed,created_at,updated_at,direction,settlement_date,remark)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(transaction_id) DO UPDATE SET type=excluded.type,currency=excluded.currency,amount=excluded.amount,
                    business_date=excluded.business_date,completed=excluded.completed,updated_at=excluded.updated_at,
                    direction=excluded.direction,settlement_date=excluded.settlement_date,remark=excluded.remark""",
                    (item.transaction_id, item.type, item.currency, str(item.amount), item.business_date.isoformat(),
                     int(item.completed), now, now, item.direction,
                     item.settlement_date.isoformat() if item.settlement_date else None, item.remark))
            db.execute("""UPDATE sync_state SET cash_flow_last_success=?,
                       cash_flow_complete_since=CASE
                         WHEN ? IS NULL THEN cash_flow_complete_since
                         WHEN cash_flow_complete_since IS NULL OR ? < cash_flow_complete_since THEN ?
                         ELSE cash_flow_complete_since END
                       WHERE id=1""", (now, complete_since, complete_since, complete_since))
        return now

    def rows(self, sql: str, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None
