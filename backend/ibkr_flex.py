import os
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from models import Funding


SERVICE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


class IBKRFlexError(RuntimeError):
    pass


TRANSIENT_MESSAGES = ("not available", "generating", "processing")
TRANSIENT_CODES = {"1003", "1004", "1018", "1019"}


def _number(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError
        return result
    except Exception as exc:
        raise IBKRFlexError(f"IBKR Flex returned an invalid {field}") from exc


def _xml(payload: bytes, component: str) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise IBKRFlexError(f"IBKR Flex returned invalid {component} XML") from exc


def _text(root: ET.Element, name: str) -> str:
    element = root.find(f".//{name}")
    return (element.text or "").strip() if element is not None else ""


class IBKRFlexClient:
    """Fetches read-only contribution history from IBKR Flex Web Service."""

    def __init__(self, request: Callable[[str], bytes] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.query_id = os.getenv("IBKR_FLEX_QUERY_ID", "").strip()
        self.token = os.getenv("IBKR_FLEX_TOKEN", "").strip()
        if not self.query_id.isdigit():
            raise IBKRFlexError("IBKR_FLEX_QUERY_ID must be a numeric Flex Query ID")
        if not self.token:
            raise IBKRFlexError("IBKR_FLEX_TOKEN is required")
        self.account_id = os.getenv("IBKR_ACCOUNT_ID", "").strip()
        self.request = request or self._request
        self.sleep = sleep

    @staticmethod
    def _request(url: str) -> bytes:
        try:
            with urlopen(Request(url, headers={"Accept": "application/xml",
                                               "User-Agent": "PortfolioTracker/1.0"}),
                         timeout=30) as response:
                return response.read()
        except HTTPError as exc:
            raise IBKRFlexError(f"IBKR Flex returned HTTP {exc.code}; previous data was preserved") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IBKRFlexError("IBKR Flex is unavailable; previous data was preserved") from exc

    def fetch_cash_flows(self, start: date, end: date) -> list[Funding]:
        if start > end:
            raise IBKRFlexError("IBKR Flex start date must not be after the end date")
        rows: list[Funding] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=364), end)
            rows.extend(self._fetch_chunk(cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
            if cursor <= end:
                self.sleep(1)
        return rows

    def _error_message(self, root: ET.Element) -> str:
        message = _text(root, "ErrorMessage") or "unknown error"
        return message.replace(self.token, "[redacted]")

    def _fetch_chunk(self, start: date, end: date) -> list[Funding]:
        params = {"t": self.token, "q": self.query_id, "v": "3",
                  "fd": start.strftime("%Y%m%d"), "td": end.strftime("%Y%m%d")}
        request_url = f"{SERVICE}/SendRequest?{urlencode(params)}"
        for attempt in range(12):
            response = _xml(self.request(request_url), "request")
            if _text(response, "Status") == "Success":
                break
            if not self._transient(response) or attempt == 11:
                raise IBKRFlexError(f"IBKR Flex request failed: {self._error_message(response)}")
            self.sleep(5)
        else:
            raise IBKRFlexError("IBKR Flex request could not be generated")
        reference = _text(response, "ReferenceCode")
        if not reference.isdigit():
            raise IBKRFlexError("IBKR Flex returned no valid reference code")
        url = f"{SERVICE}/GetStatement?{urlencode({'t': self.token, 'q': reference, 'v': '3'})}"
        for attempt in range(12):
            statement = _xml(self.request(url), "statement")
            if statement.tag.endswith("FlexQueryResponse"):
                return self._funding(statement)
            if not self._transient(statement) or attempt == 11:
                raise IBKRFlexError(f"IBKR Flex statement failed: {self._error_message(statement)}")
            self.sleep(5)
        raise IBKRFlexError("IBKR Flex statement could not be generated")

    def _transient(self, root: ET.Element) -> bool:
        message = self._error_message(root).lower()
        return _text(root, "ErrorCode") in TRANSIENT_CODES or any(
            text in message for text in TRANSIENT_MESSAGES)

    def _funding(self, root: ET.Element) -> list[Funding]:
        items = []
        for element in root.iter():
            if not element.tag.endswith("StatementOfFundsLine"):
                continue
            values = {key.lower(): value.strip() for key, value in element.attrib.items()}
            code = values.get("activitycode", "").upper()
            if code not in {"DEP", "WITH"}:
                continue
            account = values.get("accountid", "")
            if self.account_id and account and account != self.account_id:
                raise IBKRFlexError("IBKR Flex report contains an unexpected account")
            transaction_id = values.get("transactionid", "")
            if not transaction_id:
                raise IBKRFlexError("IBKR Flex returned a transfer without a transaction ID")
            transaction_date = self._date(values.get("date", ""), "transaction date")
            settlement = values.get("settledate", "")
            settlement_date = self._date(settlement, "settlement date") if settlement else None
            currency = values.get("currency", "SGD").upper()
            amount = self._amount(values)
            rate = _number(values.get("fxratetobase", "1"), "FX rate")
            if rate <= 0:
                raise IBKRFlexError("IBKR Flex returned an invalid FX rate")
            amount_sgd = amount * rate
            kind = "DEPOSIT" if code == "DEP" else "WITHDRAWAL"
            amount_sgd = abs(amount_sgd) if code == "DEP" else -abs(amount_sgd)
            description = values.get("activitydescription", "")
            remark = description if currency == "SGD" else f"{description} ({currency} at {rate})".strip()
            items.append(Funding(transaction_id, kind, "SGD", amount_sgd,
                                 transaction_date, True, "IN" if code == "DEP" else "OUT",
                                 settlement_date, remark))
        return items

    @staticmethod
    def _amount(values: dict[str, str]) -> Decimal:
        if values.get("amount"):
            return _number(values["amount"], "transfer amount")
        credit = _number(values.get("credit", "0") or "0", "credit")
        debit = _number(values.get("debit", "0") or "0", "debit")
        return credit + debit

    @staticmethod
    def _date(value: str, field: str) -> date:
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date()
        except (TypeError, ValueError) as exc:
            raise IBKRFlexError(f"IBKR Flex returned an invalid {field}") from exc
