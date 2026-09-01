import json
from io import BytesIO
import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import main
from brokers import BROKERS, BrokerCapabilities, BrokerDefinition
from database import Database
from ibkr_adapter import IBKRAdapter, IBKRError
from test_app import snapshot


ACCOUNTS = [{"accountId": "U123", "currency": "SGD"}]
LEDGER = {
    "BASE": {"netliquidationvalue": 1000, "cashbalance": 500,
             "unrealizedpnl": 20, "realizedpnl": 5},
    "SGD": {"exchangerate": 1},
    "USD": {"exchangerate": 1.35},
}
POSITIONS = [
    {"ticker": "AAPL", "name": "Apple", "countryCode": "US", "currency": "USD",
     "assetClass": "STK", "position": 2, "avgPrice": 100, "marketPrice": 110,
     "marketValue": 220, "unrealizedPnl": 10},
    {"ticker": "BOND", "name": "Unexpected bond", "currency": "SGD",
     "assetClass": "BOND", "position": 1, "avgPrice": 200, "marketPrice": 203,
     "marketValue": 203, "unrealizedPnl": 3},
]


class IBKRAdapterTests(unittest.TestCase):
    def adapter(self, responses=None):
        values = responses or {
            "/portfolio/accounts": ACCOUNTS,
            "/portfolio/U123/ledger": LEDGER,
            "/portfolio2/U123/positions": POSITIONS,
        }
        calls = []

        def get(path):
            calls.append(path)
            return values[path]

        env = {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api", "IBKR_VERIFY_SSL": "false"}
        environment = patch.dict(os.environ, env, clear=True)
        with environment:
            adapter = IBKRAdapter(get)
        return environment, adapter, calls

    def test_normalizes_sgd_account_and_uses_only_portfolio_gets(self):
        environment, adapter, calls = self.adapter()
        with environment:
            result, funding, history = adapter.fetch()
        self.assertEqual(calls, ["/portfolio/accounts", "/portfolio/U123/ledger",
                                 "/portfolio2/U123/positions"])
        self.assertFalse(any(word in path for path in calls for word in
                             ("marketdata", "order", "trade", "transfer", "statement", "flex")))
        self.assertEqual((result.total_equity, result.cash, result.holdings_value),
                         (Decimal("1000"), Decimal("500"), Decimal("500")))
        self.assertEqual((result.unrealized_pnl, result.realized_pnl), (Decimal("20"), Decimal("5")))
        self.assertEqual(result.sgd_to_usd, Decimal("1") / Decimal("1.35"))
        self.assertEqual((result.positions[0].market_value, result.positions[0].unrealized_pnl),
                         (Decimal("297.00"), Decimal("13.50")))
        self.assertEqual(result.positions[1].asset_type, "BOND")
        self.assertEqual((funding, history), ([], []))
        self.assertEqual(adapter.components, {"accounts": "ok", "ledger": "ok", "positions": "ok"})

    def test_selects_configured_account(self):
        responses = {
            "/portfolio/accounts": [*ACCOUNTS, {"accountId": "U999", "currency": "SGD"}],
            "/portfolio/U999/ledger": LEDGER,
            "/portfolio2/U999/positions": [],
        }
        calls = []
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api",
                                    "IBKR_ACCOUNT_ID": "U999"}, clear=True):
            IBKRAdapter(lambda path: calls.append(path) or responses[path]).fetch()
        self.assertIn("/portfolio/U999/ledger", calls)

    def test_requires_account_choice_when_multiple_are_visible(self):
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api"}, clear=True):
            adapter = IBKRAdapter(lambda _: [*ACCOUNTS, {"accountId": "U999", "currency": "SGD"}])
            with self.assertRaisesRegex(IBKRError, "U123, U999"):
                adapter.fetch()

    def test_rejects_unknown_account_and_non_sgd_base(self):
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api",
                                    "IBKR_ACCOUNT_ID": "missing"}, clear=True):
            with self.assertRaisesRegex(IBKRError, "visible accounts: U123"):
                IBKRAdapter(lambda _: ACCOUNTS).fetch()
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api"}, clear=True):
            with self.assertRaisesRegex(IBKRError, "SGD as its base currency"):
                IBKRAdapter(lambda _: [{"accountId": "U1", "currency": "USD"}]).fetch()

    def test_rejects_invalid_or_missing_financial_fields(self):
        for value in (None, "nan", "infinity"):
            ledger = {**LEDGER, "BASE": {**LEDGER["BASE"], "netliquidationvalue": value}}
            environment, adapter, _ = self.adapter({
                "/portfolio/accounts": ACCOUNTS,
                "/portfolio/U123/ledger": ledger,
                "/portfolio2/U123/positions": [],
            })
            with environment, self.subTest(value=value), self.assertRaisesRegex(IBKRError, "total equity"):
                adapter.fetch()
        environment, adapter, _ = self.adapter({
            "/portfolio/accounts": ACCOUNTS,
            "/portfolio/U123/ledger": {"BASE": LEDGER["BASE"]},
            "/portfolio2/U123/positions": [],
        })
        with environment, self.assertRaisesRegex(IBKRError, "USD exchange rate"):
            adapter.fetch()

    def test_rejects_malformed_positions_and_non_finite_values(self):
        invalid_rows = [None, {"currency": "SGD"},
                        {**POSITIONS[0], "marketValue": "nan"},
                        {**POSITIONS[0], "currency": "JPY"}]
        expected = ("invalid position data", "without a symbol", "market value", "no JPY exchange rate")
        for row, message in zip(invalid_rows, expected):
            environment, adapter, _ = self.adapter({
                "/portfolio/accounts": ACCOUNTS,
                "/portfolio/U123/ledger": LEDGER,
                "/portfolio2/U123/positions": [row],
            })
            with environment, self.subTest(row=row), self.assertRaisesRegex(IBKRError, message):
                adapter.fetch()

    def test_rejects_unverified_remote_gateway(self):
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://example.com/v1/api",
                                    "IBKR_VERIFY_SSL": "false"}, clear=True):
            with self.assertRaisesRegex(IBKRError, "only be disabled for localhost"):
                IBKRAdapter(lambda _: {})

    def test_wraps_gateway_and_invalid_json_errors(self):
        with patch.dict(os.environ, {"IBKR_GATEWAY_URL": "https://localhost:5000/v1/api"}, clear=True):
            adapter = IBKRAdapter()
            with patch("ibkr_adapter.urlopen", side_effect=HTTPError("url", 401, "", {}, None)), \
                 self.assertRaisesRegex(IBKRError, "authentication expired"):
                adapter._request_json("/portfolio/accounts")
            with patch("ibkr_adapter.urlopen", side_effect=URLError("down")), \
                 self.assertRaisesRegex(IBKRError, "Gateway unavailable"):
                adapter._request_json("/portfolio/accounts")
            with self.assertRaisesRegex(IBKRError, "invalid accounts data"):
                IBKRAdapter(lambda _: {"not": "a list"}).fetch()
            with patch("ibkr_adapter.urlopen", return_value=BytesIO(b"{")), \
                 self.assertRaisesRegex(IBKRError, "invalid JSON"):
                adapter._request_json("/portfolio/accounts")
            with patch("ibkr_adapter.urlopen", side_effect=HTTPError("url", 500, "", {}, None)), \
                 self.assertRaisesRegex(IBKRError, "HTTP 500"):
                adapter._request_json("/portfolio/accounts")


class IBKRIntegrationTests(unittest.TestCase):
    def test_sync_failure_preserves_cached_ibkr_data(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            store = Database(file.name)
            store.sync(replace(snapshot(), total_equity=Decimal("50")), [], [])
            before = store.one("SELECT last_success FROM sync_state WHERE id=1")["last_success"]
            client = type("Client", (), {"fetch": lambda self: (_ for _ in ()).throw(IBKRError("login required"))})()
            with patch.dict(main.DATABASES, {"ibkr": store}), \
                 patch.object(BROKERS["ibkr"], "adapter_factory", return_value=client), \
                 self.assertRaises(main.HTTPException):
                main.sync("ibkr")
            self.assertEqual(store.one("SELECT COUNT(*) AS count FROM portfolio_snapshots")["count"], 1)
            state = store.one("SELECT last_success,last_error FROM sync_state WHERE id=1")
            self.assertEqual(state["last_success"], before)
            self.assertEqual(state["last_error"], "login required")

    def test_overview_marks_pnl_unavailable_without_contributions(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            store = Database(file.name)
            store.sync(replace(snapshot(), total_equity=Decimal("50"), cash=Decimal("10"),
                               holdings_value=Decimal("40")), [], [])
            spec = BrokerDefinition("ibkr", "IBKR", "IBKR_DB_PATH", file.name, (),
                                    lambda: None, (IBKRError,),
                                    BrokerCapabilities(contributions=False, performance_history=False,
                                                       funding_history=False))
            with patch.dict(BROKERS, {"ibkr": spec}, clear=True), \
                 patch.dict(main.DATABASES, {"ibkr": store}, clear=True):
                result = main.overview("SGD")
            self.assertFalse(result["pnlComplete"])
            self.assertIsNone(result["overallPnl"])
            self.assertEqual(result["missingContributionBrokers"], ["ibkr"])
            self.assertEqual(result["totalEquity"], "50")

    def test_ibkr_funding_export_is_unavailable(self):
        with self.assertRaisesRegex(main.HTTPException, "positions, history"):
            main.export_csv("funding", "SGD", "ibkr")


if __name__ == "__main__":
    unittest.main()
