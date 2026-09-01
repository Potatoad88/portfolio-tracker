import os
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import main
from brokers import BROKERS
from database import Database
from ibkr_flex import IBKRFlexClient, IBKRFlexError
from models import Funding
from test_app import snapshot


def statement(lines=""):
    return f"""<FlexQueryResponse><FlexStatements><FlexStatement accountId="U123">
    <StatementOfFunds>{lines}</StatementOfFunds>
    </FlexStatement></FlexStatements></FlexQueryResponse>""".encode()

def error(message, code="1019"):
    return (f"<FlexStatementResponse><Status>Fail</Status><ErrorCode>{code}</ErrorCode>"
            f"<ErrorMessage>{message}</ErrorMessage></FlexStatementResponse>").encode()


class FlexTransport:
    def __init__(self, payload=None, pending=0):
        self.payload = payload or statement()
        self.pending = pending
        self.urls = []
        self.references = 0

    def __call__(self, url):
        self.urls.append(url)
        if "/SendRequest?" in url:
            self.references += 1
            return (f"<FlexStatementResponse><Status>Success</Status>"
                    f"<ReferenceCode>{self.references}</ReferenceCode>"
                    f"</FlexStatementResponse>").encode()
        if self.pending:
            self.pending -= 1
            return error("Statement is not available.")
        return self.payload


class IBKRFlexClientTests(unittest.TestCase):
    def client(self, transport, **environment):
        values = {"IBKR_FLEX_QUERY_ID": "1623267", "IBKR_FLEX_TOKEN": "secret",
                  "IBKR_ACCOUNT_ID": "U123", **environment}
        with patch.dict(os.environ, values, clear=True):
            return IBKRFlexClient(request=transport, sleep=lambda _: None)

    def test_parses_only_deposits_and_withdrawals_in_sgd(self):
        lines = """
        <StatementOfFundsLine accountId="U123" currency="USD" fxRateToBase="1.35"
          date="20260102" settleDate="20260103" activityCode="DEP"
          activityDescription="Wire deposit" amount="100" transactionID="10"/>
        <StatementOfFundsLine accountId="U123" currency="SGD" fxRateToBase="1"
          date="20260104" activityCode="WITH" activityDescription="Withdrawal"
          debit="-25" transactionID="11"/>
        <StatementOfFundsLine accountId="U123" currency="SGD" fxRateToBase="1"
          date="20260105" activityCode="DIV" amount="999" transactionID="12"/>
        """
        rows = self.client(FlexTransport(statement(lines))).fetch_cash_flows(
            date(2026, 1, 1), date(2026, 1, 5))
        self.assertEqual([(row.transaction_id, row.type, row.amount)
                          for row in rows],
                         [("10", "DEPOSIT", Decimal("135.00")),
                          ("11", "WITHDRAWAL", Decimal("-25"))])
        self.assertTrue(all(row.currency == "SGD" and row.completed for row in rows))

    def test_chunks_ranges_to_at_most_365_days_and_retries_pending_report(self):
        transport = FlexTransport(pending=1)
        client = self.client(transport)
        client.fetch_cash_flows(date(2025, 1, 1), date(2026, 1, 1))
        sends = [parse_qs(urlparse(url).query) for url in transport.urls
                 if "/SendRequest?" in url]
        self.assertEqual([(item["fd"][0], item["td"][0]) for item in sends],
                         [("20250101", "20251231"), ("20260101", "20260101")])
        self.assertEqual(len([url for url in transport.urls
                              if "/GetStatement?" in url]), 3)

    def test_rejects_unsafe_or_malformed_flex_data_without_exposing_token(self):
        cases = [
            (b"<not-xml", "invalid statement XML"),
            (statement('<StatementOfFundsLine activityCode="DEP" date="20260101" amount="1"/>'),
             "transaction ID"),
            (statement('<StatementOfFundsLine accountId="OTHER" activityCode="DEP" date="20260101" amount="1" transactionID="1"/>'),
             "unexpected account"),
            (statement('<StatementOfFundsLine accountId="U123" activityCode="DEP" date="bad" amount="1" transactionID="1"/>'),
             "transaction date"),
            (statement('<StatementOfFundsLine accountId="U123" activityCode="DEP" date="20260101" amount="NaN" transactionID="1"/>'),
             "transfer amount"),
        ]
        for payload, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(IBKRFlexError, message) as raised:
                    self.client(FlexTransport(payload)).fetch_cash_flows(
                        date(2026, 1, 1), date(2026, 1, 1))
                self.assertNotIn("secret", str(raised.exception))

    def test_retries_temporarily_unavailable_send_request(self):
        calls = []

        def transport(url):
            calls.append(url)
            if len(calls) == 1:
                return error("Statement is not available.")
            if "/SendRequest?" in url:
                return (b"<FlexStatementResponse><Status>Success</Status>"
                        b"<ReferenceCode>1</ReferenceCode></FlexStatementResponse>")
            return statement()

        self.client(transport).fetch_cash_flows(date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(len([url for url in calls if "/SendRequest?" in url]), 2)

    def test_redacts_token_from_provider_error(self):
        response = (b"<FlexStatementResponse><Status>Fail</Status>"
                    b"<ErrorMessage>bad secret token</ErrorMessage></FlexStatementResponse>")
        with self.assertRaises(IBKRFlexError) as raised:
            self.client(lambda _: response).fetch_cash_flows(
                date(2026, 1, 1), date(2026, 1, 1))
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))

    def test_requires_token_and_numeric_query_id(self):
        for values, message in [
            ({"IBKR_FLEX_QUERY_ID": "", "IBKR_FLEX_TOKEN": "x"}, "numeric"),
            ({"IBKR_FLEX_QUERY_ID": "1", "IBKR_FLEX_TOKEN": ""}, "required"),
        ]:
            with self.subTest(message=message), patch.dict(os.environ, values, clear=True):
                with self.assertRaisesRegex(IBKRFlexError, message):
                    IBKRFlexClient(request=lambda _: b"")


class IBKRFlexEndpointTests(unittest.TestCase):
    def setUp(self):
        file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        file.close()
        self.path = file.name
        self.store = Database(self.path)
        self.store.sync(snapshot(), [], [])
        self.original = main.DATABASES["ibkr"]
        main.DATABASES["ibkr"] = self.store

    def tearDown(self):
        main.DATABASES["ibkr"] = self.original
        os.unlink(self.path)

    def test_first_sync_requires_start_then_enables_contribution_pnl(self):
        calls = []
        rows = [Funding("1", "DEPOSIT", "SGD", Decimal("75"),
                        date(2025, 1, 1), True, "IN")]

        class Client:
            def fetch_cash_flows(self, start, end):
                calls.append((start, end))
                return rows

        with patch.object(BROKERS["ibkr"], "cash_flow_factory",
                          return_value=Client()):
            with self.assertRaisesRegex(main.HTTPException, "start date"):
                main.sync_cash_flow(main.CashFlowRequest(), "ibkr")
            result = main.sync_cash_flow(
                main.CashFlowRequest(startDate=date(2025, 1, 1)), "ibkr")
        self.assertEqual(calls, [(date(2025, 1, 1), date.today() - timedelta(days=1))])
        self.assertEqual((result["rawCount"], result["verifiedCount"]), (1, 1))
        self.assertEqual(main.status("ibkr")["cashFlowCompleteSince"], "2025-01-01")
        summary = main.summary("SGD", "ibkr")
        self.assertTrue(summary["supportsContributions"])
        self.assertEqual(summary["netContributions"], "75")

    def test_incremental_sync_overlaps_two_days_and_failure_preserves_data(self):
        self.store.sync_cash_flows(
            [Funding("existing", "DEPOSIT", "SGD", Decimal("10"),
                     date.today() - timedelta(days=3), True, "IN")],
            date.today() - timedelta(days=30))
        state = self.store.one(
            "SELECT cash_flow_last_success,cash_flow_complete_since FROM sync_state WHERE id=1")
        calls = []

        class Client:
            def fetch_cash_flows(self, start, end):
                calls.append((start, end))
                return []

        with patch.object(BROKERS["ibkr"], "cash_flow_factory",
                          return_value=Client()):
            main.sync_cash_flow(main.CashFlowRequest(), "ibkr")
        self.assertEqual(calls[0][0], date.fromisoformat(
            state["cash_flow_last_success"][:10]) - timedelta(days=2))

        class Failure:
            def fetch_cash_flows(self, start, end):
                raise IBKRFlexError("Flex unavailable")

        before = self.store.one(
            "SELECT cash_flow_last_success,cash_flow_complete_since FROM sync_state WHERE id=1")
        with patch.object(BROKERS["ibkr"], "cash_flow_factory",
                          return_value=Failure()), self.assertRaises(main.HTTPException):
            main.sync_cash_flow(main.CashFlowRequest(), "ibkr")
        after = self.store.one(
            "SELECT cash_flow_last_success,cash_flow_complete_since FROM sync_state WHERE id=1")
        self.assertEqual(before, after)
        self.assertEqual(self.store.one(
            "SELECT COUNT(*) AS count FROM funding_transactions")["count"], 1)


if __name__ == "__main__":
    unittest.main()
