import asyncio
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from calculations import net_contributions, performance
from adapter import LiveTigerAdapter, _funds_are_excluded
from database import Database
from models import Funding, HistoryPoint, Position, Snapshot
import main


FUNDING = [
    Funding("dep", "1", "SGD", Decimal("100"), date(2026, 1, 1), True),
    Funding("withdraw", "3", "SGD", Decimal("10"), date(2026, 1, 2), True),
    Funding("fee", "20", "SGD", Decimal("2"), date(2026, 1, 2), True),
    Funding("refund", "21", "SGD", Decimal("1"), date(2026, 1, 3), True),
]


def snapshot() -> Snapshot:
    position = Position("TEST", "Test holding", "SG", "SGD", Decimal("1"), Decimal("10"), Decimal("12"), Decimal("12"), Decimal("2"))
    return Snapshot(datetime.now(timezone.utc), "SGD", Decimal("100"), Decimal("88"), Decimal("12"), Decimal("2"), Decimal("0"), (position,), Decimal("0.75"))


def history() -> list[HistoryPoint]:
    return [HistoryPoint(datetime.now(timezone.utc), Decimal("100"))]


class CalculationTests(unittest.TestCase):
    def test_deposits_withdrawals_fees_refunds(self):
        self.assertEqual(net_contributions(FUNDING), Decimal("89"))

    def test_zero_contributions(self):
        self.assertEqual(performance(Decimal("12"), Decimal("0")), (Decimal("12"), None))

    def test_pending_transactions_are_ignored(self):
        pending = Funding("pending", "1", "SGD", Decimal("999"), date.today(), False)
        self.assertEqual(net_contributions([*FUNDING, pending]), Decimal("89"))

    def test_signed_refunds_are_normalized(self):
        refund = Funding("refund", "22", "SGD", Decimal("-5"), date.today(), True)
        self.assertEqual(net_contributions([refund]), Decimal("5"))

    def test_signed_tiger_withdrawals_are_not_double_negated(self):
        items = [
            Funding("deposit", "1", "SGD", Decimal("5785.00"), date.today(), True),
            Funding("withdrawal", "3", "SGD", Decimal("-3742.01"), date.today(), True),
        ]
        self.assertEqual(net_contributions(items), Decimal("2042.99"))

    def test_non_sgd_rejected(self):
        with self.assertRaisesRegex(ValueError, "only SGD"):
            net_contributions([Funding("x", "DEPOSIT", "USD", Decimal("1"), date.today(), True)])

    def test_tiger_numeric_funding_fields(self):
        parse = LiveTigerAdapter.__new__(LiveTigerAdapter)._funding
        items = [parse({"id": str(kind), "type": kind, "currency": "SGD", "amount": amount,
                        "business_date": "2026/08/30", "completed_status": True})
                 for kind, amount in [(1, 100), (3, 10), (20, 2), (21, 1)]]
        self.assertEqual(net_contributions(items), Decimal("89"))
        self.assertEqual(items[0].business_date, date(2026, 8, 30))

    def test_position_money_normalized_to_sgd(self):
        parse = LiveTigerAdapter.__new__(LiveTigerAdapter)._position
        position = parse({"symbol": "AAPL", "currency": "USD", "sec_type": "STK", "quantity": 1, "average_cost": 100,
                          "market_price": 110, "market_value": 110, "unrealized_pnl": 10}, {"USD": Decimal("1.3")})
        self.assertEqual(position.market_value, Decimal("143.0"))
        self.assertEqual(position.unrealized_pnl, Decimal("13.0"))
        self.assertEqual(position.asset_type, "STK")

    def test_fund_query_overrides_misleading_contract_type(self):
        parse = LiveTigerAdapter.__new__(LiveTigerAdapter)._position
        position = parse({"symbol": "MMF", "currency": "SGD", "sec_type": "STK"}, {"SGD": Decimal("1")}, "FUND")
        self.assertEqual(position.asset_type, "FUND")

    def test_fund_value_is_added_to_stock_segment_equity(self):
        class Client:
            def get_prime_assets(self, base_currency, **_):
                equity = 75 if base_currency == "USD" else 100
                return {"segments": {"S": {"net_liquidation": equity, "cash_balance": 88,
                                              "unrealized_pl": 2, "realized_pl": 0,
                                              "currency_assets": {"SGD": {"forex_rate": 1}}}}}

            def get_positions(self, sec_type, **_):
                value = 12 if sec_type == "STK" else 5
                return [{"symbol": sec_type, "currency": "SGD", "market_value": value,
                         "unrealized_pnl": 1 if sec_type == "FUND" else 2}]

            def get_funding_history(self):
                return []

            def get_analytics_asset(self, **_):
                return []

        adapter = LiveTigerAdapter.__new__(LiveTigerAdapter)
        adapter.client, adapter.account, adapter.components = Client(), "test", {}
        result, _, _ = adapter.fetch()
        self.assertEqual((result.total_equity, result.holdings_value, result.unrealized_pnl),
                         (Decimal("105"), Decimal("17"), Decimal("3")))
        self.assertEqual(result.sgd_to_usd, Decimal("0.75"))

    def test_already_consolidated_fund_is_not_added_twice(self):
        positions = (
            Position("STK", "Stock", "SG", "SGD", Decimal(1), Decimal(12), Decimal(12), Decimal(12), Decimal(2)),
            Position("FUND", "Fund", "SG", "SGD", Decimal(1), Decimal(5), Decimal(5), Decimal(5), Decimal(1), "FUND"),
        )
        self.assertFalse(_funds_are_excluded(Decimal("105"), Decimal("88"), positions))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = Database(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_idempotent_funding_sync(self):
        self.db.sync(snapshot(), FUNDING, history())
        self.db.sync(snapshot(), FUNDING, history())
        self.assertEqual(self.db.one("SELECT count(*) n FROM funding_transactions")["n"], len(FUNDING))
        self.assertEqual(self.db.one("SELECT count(*) n FROM portfolio_snapshots")["n"], 1)

    def test_changed_position_creates_a_new_snapshot(self):
        first = snapshot()
        changed_position = replace(first.positions[0], market_value=Decimal("13"))
        changed = replace(first, positions=(changed_position,))
        self.db.sync(first, FUNDING, history())
        self.db.sync(changed, FUNDING, history())
        self.assertEqual(self.db.one("SELECT count(*) n FROM portfolio_snapshots")["n"], 2)

    def test_sync_persists_fx_and_component_health(self):
        self.db.sync(snapshot(), FUNDING, [HistoryPoint(datetime.now(timezone.utc), Decimal("100"), Decimal("0.75"))], {"assets": "ok"})
        self.assertEqual(self.db.one("SELECT sgd_to_usd FROM history")["sgd_to_usd"], "0.75")
        self.assertIn('"assets": "ok"', self.db.one("SELECT components FROM sync_state WHERE id=1")["components"])

    def test_legacy_schema_is_upgraded_without_losing_rows(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            with sqlite3.connect(path) as legacy:
                legacy.executescript("""
                    CREATE TABLE portfolio_snapshots (id INTEGER PRIMARY KEY, captured_at TEXT, reporting_currency TEXT, total_equity TEXT, cash TEXT, holdings_value TEXT, unrealized_pnl TEXT, realized_pnl TEXT);
                    CREATE TABLE positions (id INTEGER PRIMARY KEY, snapshot_id INTEGER, symbol TEXT, name TEXT, market TEXT, currency TEXT, quantity TEXT, average_cost TEXT, market_price TEXT, market_value TEXT, unrealized_pnl TEXT);
                    CREATE TABLE history (captured_at TEXT PRIMARY KEY, total_equity TEXT);
                    CREATE TABLE sync_state (id INTEGER PRIMARY KEY, last_success TEXT, last_error TEXT);
                    INSERT INTO sync_state VALUES (1, 'saved', NULL);
                """)
            upgraded = Database(path)
            self.assertEqual(upgraded.one("SELECT last_success,components FROM sync_state WHERE id=1"), {"last_success": "saved", "components": "{}"})
            self.assertIn("sgd_to_usd", {row["name"] for row in upgraded.rows("PRAGMA table_info(history)")})
            self.assertIn("asset_type", {row["name"] for row in upgraded.rows("PRAGMA table_info(positions)")})
        finally:
            os.unlink(path)

    def test_failed_transaction_preserves_previous_snapshot(self):
        self.db.sync(snapshot(), FUNDING, history())
        before = self.db.one("SELECT count(*) n FROM portfolio_snapshots")["n"]
        bad = snapshot().__class__(snapshot().captured_at, "SGD", Decimal("1"), Decimal("0"), Decimal("1"), Decimal("0"), Decimal("0"), (None,))
        with self.assertRaises(Exception):
            self.db.sync(bad, FUNDING, [])
        self.assertEqual(self.db.one("SELECT count(*) n FROM portfolio_snapshots")["n"], before)

    def test_error_preserves_last_success(self):
        self.db.sync(snapshot(), FUNDING, history())
        before = self.db.one("SELECT last_success FROM sync_state WHERE id=1")["last_success"]
        self.db.record_error("network failed")
        state = self.db.one("SELECT last_success,last_error FROM sync_state WHERE id=1")
        self.assertEqual(state, {"last_success": before, "last_error": "network failed"})


class EndpointTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original_db, main.db = main.db, Database(self.path)
        captured = datetime(2026, 1, 3, tzinfo=timezone.utc)
        main.db.sync(snapshot().__class__(captured, "SGD", Decimal("100"), Decimal("88"), Decimal("12"), Decimal("2"), Decimal("0"), snapshot().positions, Decimal("0.75")),
                     FUNDING, [HistoryPoint(datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("100"), Decimal("0.80")),
                               HistoryPoint(datetime(2026, 1, 2, tzinfo=timezone.utc), Decimal("95"), Decimal("0.70"))])

    def tearDown(self):
        main.db = self.original_db
        os.unlink(self.path)

    def test_summary_reconciles_and_converts_currency(self):
        sgd, usd = main.summary("SGD"), main.summary("USD")
        self.assertEqual((sgd["netContributions"], sgd["overallPnl"], sgd["reconciliationDifference"]), ("89", "11", "0"))
        self.assertEqual((usd["totalEquity"], usd["netContributions"], usd["overallPnl"]), ("75.00", "66.75", "8.25"))
        self.assertEqual(usd["overallReturnPct"], sgd["overallReturnPct"])

    def test_history_uses_each_days_fx_and_marks_funding_changes(self):
        rows = main.history("USD")
        self.assertEqual(rows[0]["total_equity"], "80.00")
        self.assertEqual(rows[1]["total_equity"], "66.50")
        self.assertFalse(rows[0]["funding_event"])
        self.assertTrue(rows[1]["funding_event"])

    def test_csv_export_contains_converted_rows(self):
        response = main.export_csv("positions", "USD")

        async def body():
            return "".join([chunk async for chunk in response.body_iterator])

        rows = list(csv.DictReader(io.StringIO(asyncio.run(body()))))
        self.assertEqual(rows[0]["symbol"], "TEST")
        self.assertEqual(rows[0]["market_value"], "9.00")
        self.assertEqual(rows[0]["display_currency"], "USD")

    def test_unknown_export_is_rejected(self):
        with self.assertRaisesRegex(main.HTTPException, "Export must be"):
            main.export_csv("secrets", "SGD")


if __name__ == "__main__":
    unittest.main()
