import asyncio
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from brokers import BROKERS, BrokerCapabilities, BrokerDefinition, definition
from calculations import net_contributions, performance
from adapter import LiveTigerAdapter, _funds_are_excluded
from database import Database
from models import Funding, HistoryPoint, Position, Snapshot
from moomoo_adapter import MoomooAdapter, MoomooError
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
        self.db.sync(snapshot(), FUNDING, [HistoryPoint(datetime.now(timezone.utc), Decimal("100"), Decimal("0.75"))],
                     {"assets": "ok"}, "2026-08-31")
        self.assertEqual(self.db.one("SELECT sgd_to_usd FROM history")["sgd_to_usd"], "0.75")
        state = self.db.one("SELECT components,cash_flow_checked_through FROM sync_state WHERE id=1")
        self.assertIn('"assets": "ok"', state["components"])
        self.assertEqual(state["cash_flow_checked_through"], "2026-08-31")

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
            self.assertEqual(upgraded.one("SELECT last_success,components,cash_flow_checked_through FROM sync_state WHERE id=1"),
                             {"last_success": "saved", "components": "{}", "cash_flow_checked_through": "saved"})
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


class MoomooAdapterTests(unittest.TestCase):
    class Sdk:
        RET_OK = 0

        class TrdEnv:
            REAL = "REAL"

        class Currency:
            SGD = "SGD"
            USD = "USD"

        class CashFlowDirection:
            NONE = "NONE"

    class Client:
        def __init__(self, fail_positions=False):
            self.closed = False
            self.fail_positions = fail_positions
            self.cash_flow_dates = []

        def get_acc_list(self):
            return 0, [{"acc_id": 42, "trd_env": "REAL"}]

        def position_list_query(self, **_):
            if self.fail_positions:
                return -1, "OpenD unavailable"
            return 0, [{"code": "US.TEST", "stock_name": "US holding", "currency": "USD", "qty": 2,
                        "average_cost": 190, "nominal_price": 200, "market_val": 400, "unrealized_pl": 10},
                       {"code": "SG.TEST", "stock_name": "SG holding", "currency": "SGD", "qty": 1,
                        "average_cost": 170, "nominal_price": 175, "market_val": 175, "unrealized_pl": 5}]

        def accinfo_query(self, currency, **_):
            if currency == "SGD":
                return 0, [{"total_assets": 1000, "fund_assets": 100, "sg_cash": 100, "us_cash": 100}]
            return 0, [{"total_assets": 800}]

        def get_acc_cash_flow(self, clearing_date, **_):
            self.cash_flow_dates.append(clearing_date)
            if clearing_date != date.today().isoformat():
                return 0, []
            return 0, [{"cashflow_id": 7, "clearing_date": clearing_date, "settlement_date": clearing_date,
                        "currency": "USD", "cashflow_type": "Fund Redemption", "cashflow_direction": "IN",
                        "cashflow_amount": 12.5, "cashflow_remark": "Raw provider description"}]

        def close(self):
            self.closed = True

    def adapter(self, client):
        with patch.dict(os.environ, {"MOOMOO_ACCOUNT_ID": "42"}):
            return MoomooAdapter(client=client, sdk=self.Sdk)

    def test_mixed_currency_positions_and_aggregate_fund(self):
        client = self.Client()
        result, funding_rows, history_rows = self.adapter(client).fetch()
        self.assertEqual((result.total_equity, result.cash, result.holdings_value),
                         (Decimal("1000"), Decimal("225.00"), Decimal("775.00")))
        self.assertEqual(result.sgd_to_usd, Decimal("0.8"))
        self.assertEqual(result.unrealized_pnl, Decimal("17.50"))
        self.assertEqual(result.positions[0].market_value, Decimal("500.0"))
        self.assertEqual(result.positions[-1].asset_type, "FUND")
        self.assertEqual((funding_rows, history_rows), ([], []))
        self.assertTrue(client.closed)

    def test_explicit_cash_flow_dates_are_queried_once(self):
        client = self.Client()
        self.adapter(client).fetch_cash_flows([date(2024, 3, 26), date.today()])
        self.assertEqual(client.cash_flow_dates, ["2024-03-26", date.today().isoformat()])
        self.assertTrue(client.closed)

    def test_portfolio_fetch_does_not_query_cash_flow(self):
        client = self.Client()
        self.adapter(client).fetch()
        self.assertEqual(client.cash_flow_dates, [])

    def test_query_failure_closes_context_and_preserves_safe_message(self):
        client = self.Client(fail_positions=True)
        with self.assertRaisesRegex(MoomooError, "OpenD unavailable"):
            self.adapter(client).fetch()
        self.assertTrue(client.closed)

    def test_cash_flow_failure_closes_context(self):
        client = self.Client()
        client.get_acc_cash_flow = lambda **kwargs: (-1, "OpenD unavailable")
        with self.assertRaisesRegex(MoomooError, "OpenD unavailable"):
            self.adapter(client).fetch_cash_flows([date.today()])
        self.assertTrue(client.closed)

    def test_invalid_required_number_has_moomoo_error(self):
        client = self.Client()
        original = client.accinfo_query
        client.accinfo_query = lambda currency, **kwargs: (0, [{"total_assets": "NaN"}]) if currency == "SGD" else original(currency, **kwargs)
        with self.assertRaisesRegex(MoomooError, "Moomoo returned an invalid total assets"):
            self.adapter(client).fetch()
        self.assertTrue(client.closed)

    def test_invalid_account_id_has_clear_error(self):
        for value in ("", "not-a-number", "-1"):
            with patch.dict(os.environ, {"MOOMOO_ACCOUNT_ID": value}):
                with self.assertRaisesRegex(MoomooError, "valid numeric"):
                    MoomooAdapter(client=self.Client(), sdk=self.Sdk)

    def test_non_local_opend_is_rejected(self):
        with patch.dict(os.environ, {"MOOMOO_ACCOUNT_ID": "42", "MOOMOO_HOST": "example.com"}):
            with self.assertRaisesRegex(MoomooError, "localhost"):
                MoomooAdapter(client=self.Client(), sdk=self.Sdk)


class BrokerRegistryTests(unittest.TestCase):
    def test_registry_ids_order_paths_and_capabilities(self):
        self.assertEqual(list(BROKERS), ["tiger", "moomoo"])
        self.assertTrue(all(key == broker.id for key, broker in BROKERS.items()))
        self.assertEqual(BROKERS["tiger"].database_default, "backend/portfolio.db")
        self.assertTrue(BROKERS["tiger"].fetches_history)
        self.assertFalse(BROKERS["tiger"].capabilities.cash_flow_sync)
        self.assertTrue(BROKERS["moomoo"].capabilities.cash_flow_sync)

    def test_configuration_is_derived_without_constructing_adapters(self):
        with patch.object(BROKERS["tiger"], "adapter_factory", side_effect=AssertionError("adapter called")), \
             patch.object(BROKERS["moomoo"], "adapter_factory", side_effect=AssertionError("adapter called")), \
             patch.dict(os.environ, {"TIGER_ID": "x", "TIGER_ACCOUNT": "x", "TIGER_PRIVATE_KEY_PATH": "x",
                                     "MOOMOO_ACCOUNT_ID": "42"}, clear=True):
            metadata = main.brokers()
        self.assertTrue(all(item["configured"] for item in metadata))
        self.assertTrue(metadata[1]["capabilities"]["cashFlowSync"])

    def test_unknown_broker_definition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Broker must be one of"):
            definition("unknown")


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.broker_env = patch.dict(os.environ, {
            "TIGER_ID": "test", "TIGER_ACCOUNT": "test", "TIGER_PRIVATE_KEY_PATH": "test.pem",
            "MOOMOO_ACCOUNT_ID": "42",
        })
        self.broker_env.start()
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        second_handle, self.moomoo_path = tempfile.mkstemp(suffix=".db")
        os.close(second_handle)
        self.original_db, self.original_moomoo_db = main.DATABASES["tiger"], main.DATABASES["moomoo"]
        main.DATABASES["tiger"], main.DATABASES["moomoo"] = Database(self.path), Database(self.moomoo_path)
        captured = datetime(2026, 1, 3, tzinfo=timezone.utc)
        main.DATABASES["tiger"].sync(snapshot().__class__(captured, "SGD", Decimal("100"), Decimal("88"), Decimal("12"), Decimal("2"), Decimal("0"), snapshot().positions, Decimal("0.75")),
                     FUNDING, [HistoryPoint(datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("100"), Decimal("0.80")),
                               HistoryPoint(datetime(2026, 1, 2, tzinfo=timezone.utc), Decimal("95"), Decimal("0.70"))])
        main.DATABASES["moomoo"].sync(replace(snapshot(), total_equity=Decimal("200"), cash=Decimal("50"),
                                    holdings_value=Decimal("150"), positions=()), [], [])

    def tearDown(self):
        main.DATABASES["tiger"], main.DATABASES["moomoo"] = self.original_db, self.original_moomoo_db
        os.unlink(self.path)
        os.unlink(self.moomoo_path)
        self.broker_env.stop()

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

    def test_broker_data_and_capabilities_are_isolated(self):
        tiger, moomoo = main.summary("SGD"), main.summary("SGD", "moomoo")
        self.assertEqual(tiger["totalEquity"], "100")
        self.assertTrue(tiger["supportsContributions"])
        self.assertEqual(moomoo["totalEquity"], "200")
        self.assertTrue(moomoo["supportsContributions"])
        self.assertEqual(moomoo["netContributions"], "0")

    def test_moomoo_funding_hides_non_contribution_cash_flows(self):
        flows = [Funding("fund", "Fund Redemption", "USD", Decimal("12.5"), date(2026, 1, 2), True, "IN"),
                 Funding("deposit", "Others", "SGD", Decimal("500"), date(2026, 1, 3), True,
                         "IN", remark="DDIIRGPC123")]
        main.DATABASES["moomoo"].sync(replace(snapshot(), positions=()), flows, [])
        rows = main.funding("SGD", "moomoo")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["type_label"], rows[0]["amount"], rows[0]["display_currency"]),
                         ("Deposit", "500", "SGD"))
        self.assertEqual(main.summary("SGD", "moomoo")["netContributions"], "500")

    def test_moomoo_contributions_include_only_verified_transfers(self):
        flows = [
            Funding("deposit", "Others", "SGD", Decimal("500"), date(2026, 1, 1), True, "IN", remark="DDIIRGPC123"),
            Funding("withdrawal", "Bank Transfer Withdrawals", "SGD", Decimal("-100"), date(2026, 1, 2), True, "OUT"),
            Funding("legacy", "Others", "SGD", Decimal("-50"), date(2026, 1, 3), True, "OUT", remark="legacy"),
            Funding("fund", "Fund Redemption", "SGD", Decimal("999"), date(2026, 1, 3), True, "IN"),
        ]
        main.DATABASES["moomoo"].sync(replace(snapshot(), positions=()), flows, [])
        with patch.dict(os.environ, {"MOOMOO_MANUAL_WITHDRAWALS": "2026-01-03:50"}):
            result = main.summary("SGD", "moomoo")
            overview = main.overview("SGD")
        self.assertEqual(result["netContributions"], "350")
        self.assertEqual((overview["netContributions"], overview["overallPnl"]), ("439", "-239"))

    def test_tiger_portfolio_sync_receives_incremental_history_start(self):
        calls = []

        class Client:
            components = {"assets": "ok"}

            def fetch(self, start):
                calls.append(start)
                return snapshot(), [], []

        with patch.object(BROKERS["tiger"], "adapter_factory", return_value=Client()):
            main.sync("tiger")
        self.assertEqual(calls, ["2026-01-01"])

    def test_moomoo_portfolio_sync_does_not_fetch_cash_flow(self):
        calls = []

        class Client:
            components = {"positions": "ok"}

            def fetch(self, *args):
                calls.append(args)
                return replace(snapshot(), positions=()), [], []

        with patch.object(BROKERS["moomoo"], "adapter_factory", return_value=Client()):
            main.sync("moomoo")
        self.assertEqual(calls, [()])

    def test_cash_flow_sync_validates_dates(self):
        invalid = [main.CashFlowRequest(dates=[]),
                   main.CashFlowRequest(dates=[date.today()] * 2),
                   main.CashFlowRequest(dates=[date.today() + timedelta(days=1)]),
                   main.CashFlowRequest(dates=[date.today() - timedelta(days=value) for value in range(21)])]
        for request in invalid:
            with self.assertRaises(main.HTTPException):
                main.sync_cash_flow(request)
        with self.assertRaises(Exception):
            main.CashFlowRequest(dates=["not-a-date"])
        with self.assertRaises(main.HTTPException):
            main.sync_cash_flow(main.CashFlowRequest(dates=[date.today()]), "tiger")

    def test_cash_flow_sync_stores_raw_rows_and_counts_verified(self):
        selected = [date(2026, 1, 3), date(2026, 1, 1)]
        flows = [Funding("deposit", "Others", "SGD", Decimal("500"), date(2026, 1, 1), True, "IN", remark="DDIIRGPC123"),
                 Funding("fund", "Fund Redemption", "SGD", Decimal("20"), date(2026, 1, 3), True, "IN")]
        calls = []

        class Client:
            def fetch_cash_flows(self, dates):
                calls.append(dates)
                return flows

        with patch.object(BROKERS["moomoo"], "adapter_factory", return_value=Client()):
            result = main.sync_cash_flow(main.CashFlowRequest(dates=selected))
            main.sync_cash_flow(main.CashFlowRequest(dates=selected))
        self.assertEqual(calls[0], sorted(selected))
        self.assertEqual((result["rawCount"], result["verifiedCount"]), (2, 1))
        self.assertEqual(len(main.DATABASES["moomoo"].rows("SELECT * FROM funding_transactions")), 2)
        self.assertEqual(len(main.funding("SGD", "moomoo")), 1)
        self.assertIsNotNone(main.status("moomoo")["cashFlowLastSuccess"])

    def test_cash_flow_failure_preserves_existing_rows(self):
        main.DATABASES["moomoo"].sync_cash_flows([Funding("existing", "Others", "SGD", Decimal("1"), date.today(), True, "IN")])
        before = main.DATABASES["moomoo"].one("SELECT cash_flow_last_success FROM sync_state WHERE id=1")["cash_flow_last_success"]

        class Client:
            def fetch_cash_flows(self, dates):
                raise MoomooError("OpenD unavailable")

        with patch.object(BROKERS["moomoo"], "adapter_factory", return_value=Client()), self.assertRaises(main.HTTPException):
            main.sync_cash_flow(main.CashFlowRequest(dates=[date.today()]))
        self.assertEqual(len(main.DATABASES["moomoo"].rows("SELECT * FROM funding_transactions")), 1)
        self.assertEqual(main.DATABASES["moomoo"].one("SELECT cash_flow_last_success FROM sync_state WHERE id=1")["cash_flow_last_success"], before)

    def test_empty_cash_flow_sync_updates_timestamp(self):
        class Client:
            def fetch_cash_flows(self, dates):
                return []

        with patch.object(BROKERS["moomoo"], "adapter_factory", return_value=Client()):
            result = main.sync_cash_flow(main.CashFlowRequest(dates=[date.today()]))
        self.assertEqual((result["rawCount"], result["verifiedCount"]), (0, 0))
        self.assertIsNotNone(main.status("moomoo")["cashFlowLastSuccess"])

    def test_overview_aggregates_cached_brokers_and_assets(self):
        result = main.overview("SGD")
        self.assertEqual((result["totalEquity"], result["netContributions"], result["overallPnl"]),
                         ("300", "89", "211"))
        self.assertEqual((result["cash"], result["holdingsValue"]), ("138", "162"))
        self.assertEqual((result["stocksValue"], result["fundsValue"], result["otherHoldingsValue"]), ("12", "0", "150"))
        self.assertEqual(result["brokerCount"], 2)
        self.assertTrue(result["complete"])
        self.assertEqual(result["brokers"][0]["allocationPct"], str(Decimal("100") / Decimal("300") * 100))

    def test_overview_hides_immaterial_unclassified_rounding(self):
        main.DATABASES["tiger"].sync(replace(snapshot(), holdings_value=Decimal("12.50")), [], [])
        main.DATABASES["moomoo"].sync(replace(snapshot(), total_equity=Decimal("200"), cash=Decimal("50"),
                                    holdings_value=Decimal("150"), positions=()), [], [])
        self.assertEqual(main.overview("SGD")["otherHoldingsValue"], "150")

    def test_overview_uses_each_brokers_fx_rate_without_adapters(self):
        main.DATABASES["moomoo"].sync(replace(snapshot(), total_equity=Decimal("200"), cash=Decimal("50"),
                                    holdings_value=Decimal("150"), positions=(), sgd_to_usd=Decimal("0.5")), [], [])
        with patch.object(BROKERS["tiger"], "adapter_factory", side_effect=AssertionError("adapter called")), \
             patch.object(BROKERS["moomoo"], "adapter_factory", side_effect=AssertionError("adapter called")):
            result = main.overview("USD")
        self.assertEqual((result["totalEquity"], result["netContributions"], result["overallPnl"]),
                         ("175.00", "66.75", "108.25"))
        self.assertEqual((result["cash"], result["holdingsValue"]), ("91.00", "84.00"))

    def test_overview_marks_missing_broker_without_hiding_available_data(self):
        handle, empty_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        original = main.DATABASES["moomoo"]
        try:
            main.DATABASES["moomoo"] = Database(empty_path)
            result = main.overview("SGD")
        finally:
            main.DATABASES["moomoo"] = original
            os.unlink(empty_path)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missingBrokers"], ["moomoo"])
        self.assertEqual((result["brokerCount"], result["totalEquity"]), (1, "100"))
        self.assertFalse(result["brokers"][1]["hasData"])

    def test_overview_handles_both_brokers_missing(self):
        first_handle, first_path = tempfile.mkstemp(suffix=".db")
        second_handle, second_path = tempfile.mkstemp(suffix=".db")
        os.close(first_handle)
        os.close(second_handle)
        original = main.DATABASES["tiger"], main.DATABASES["moomoo"]
        try:
            main.DATABASES["tiger"], main.DATABASES["moomoo"] = Database(first_path), Database(second_path)
            result = main.overview("SGD")
        finally:
            main.DATABASES["tiger"], main.DATABASES["moomoo"] = original
            os.unlink(first_path)
            os.unlink(second_path)
        self.assertEqual((result["brokerCount"], result["totalEquity"]), (0, "0"))
        self.assertEqual(result["missingBrokers"], ["tiger", "moomoo"])

    def test_overview_iterates_a_new_registry_entry_without_adapter_calls(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        extra = BrokerDefinition("extra", "Extra", "EXTRA_DB_PATH", path, (),
                                 lambda: (_ for _ in ()).throw(AssertionError("adapter called")),
                                 (RuntimeError,), BrokerCapabilities())
        store = Database(path)
        store.sync(replace(snapshot(), total_equity=Decimal("50"), cash=Decimal("10"),
                           holdings_value=Decimal("40")), [], [])
        try:
            with patch.dict(BROKERS, {"extra": extra}), patch.dict(main.DATABASES, {"extra": store}):
                result = main.overview("SGD")
            self.assertEqual((result["supportedBrokerCount"], result["brokerCount"], result["totalEquity"]),
                             (3, 3, "350"))
        finally:
            os.unlink(path)

    def test_unconfigured_broker_is_visible_but_not_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            metadata = main.brokers()
            result = main.overview("SGD")
        self.assertFalse(any(item["configured"] for item in metadata))
        self.assertEqual(result["configuredBrokerCount"], 0)
        self.assertEqual(result["missingBrokers"], [])
        self.assertTrue(result["complete"])

    def test_overview_rejects_unsupported_currency(self):
        messages = []

        async def request():
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": "/api/overview",
                     "raw_path": b"/api/overview", "query_string": b"currency=EUR",
                     "headers": [], "client": ("test", 1), "server": ("test", 80)}

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await main.app(scope, receive, send)

        asyncio.run(request())
        self.assertEqual(messages[0]["status"], 422)

    def test_invalid_broker_is_rejected(self):
        with self.assertRaisesRegex(main.HTTPException, "Broker must be"):
            main.summary("SGD", "other")


if __name__ == "__main__":
    unittest.main()
