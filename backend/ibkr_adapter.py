import json
import os
import ssl
from datetime import datetime, timezone
from decimal import Decimal
from ipaddress import ip_address
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from models import Position, Snapshot


class IBKRError(RuntimeError):
    pass


def _number(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except Exception as exc:
        raise IBKRError(f"IBKR returned an invalid {field}") from exc


def _is_loopback(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    try:
        return bool(hostname and ip_address(hostname).is_loopback)
    except ValueError:
        return False


class IBKRAdapter:
    """Read-only normalization boundary around the local Client Portal Gateway."""

    def __init__(self, get_json: Callable[[str], Any] | None = None) -> None:
        self.components: dict[str, str] = {}
        self.base_url = os.getenv("IBKR_GATEWAY_URL", "").rstrip("/")
        if not self.base_url:
            raise IBKRError("IBKR_GATEWAY_URL is required")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise IBKRError("IBKR_GATEWAY_URL must be an HTTPS URL")
        verify = os.getenv("IBKR_VERIFY_SSL", "false").lower() not in {"0", "false", "no"}
        if not verify and not _is_loopback(parsed.hostname):
            raise IBKRError("IBKR SSL verification may only be disabled for localhost")
        self.context = ssl.create_default_context() if verify else ssl._create_unverified_context()
        self.get_json = get_json or self._request_json

    def _request_json(self, path: str) -> Any:
        try:
            with urlopen(Request(f"{self.base_url}{path}", headers={"Accept": "application/json"}),
                         timeout=15, context=self.context) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = ("authentication expired; open the Client Portal Gateway and log in"
                      if exc.code in {401, 403} else f"Gateway returned HTTP {exc.code}")
            raise IBKRError(f"IBKR {detail}; previous data was preserved") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IBKRError("IBKR Gateway unavailable; start it and log in at its localhost URL; previous data was preserved") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IBKRError("IBKR Gateway returned invalid JSON; previous data was preserved") from exc

    def _get(self, component: str, path: str, expected: type) -> Any:
        try:
            value = self.get_json(path)
        except IBKRError:
            raise
        except Exception as exc:
            raise IBKRError(f"IBKR {component} request failed ({type(exc).__name__}); previous data was preserved") from exc
        if not isinstance(value, expected):
            raise IBKRError(f"IBKR returned invalid {component} data; previous data was preserved")
        self.components[component] = "ok"
        return value

    def fetch(self):
        accounts = self._get("accounts", "/portfolio/accounts", list)
        account = self._select_account(accounts)
        account_id = str(account.get("accountId") or account.get("id") or "")
        if str(account.get("currency", "")).upper() != "SGD":
            raise IBKRError("IBKR v1 requires an account with SGD as its base currency")
        ledger = self._get("ledger", f"/portfolio/{account_id}/ledger", dict)
        rows = self._get("positions", f"/portfolio2/{account_id}/positions", list)
        base = ledger.get("BASE")
        if not isinstance(base, dict):
            raise IBKRError("IBKR returned no BASE account ledger")
        usd = ledger.get("USD")
        if not isinstance(usd, dict):
            raise IBKRError("IBKR returned no USD exchange rate")
        usd_to_sgd = _number(usd.get("exchangerate"), "USD exchange rate")
        if usd_to_sgd <= 0:
            raise IBKRError("IBKR returned an invalid USD exchange rate")
        positions = tuple(self._position(row, ledger) for row in rows)
        total = _number(base.get("netliquidationvalue"), "total equity")
        cash = _number(base.get("cashbalance"), "cash balance")
        captured = datetime.now(timezone.utc)
        snapshot = Snapshot(captured, "SGD", total, cash, total - cash,
                            _number(base.get("unrealizedpnl", 0), "unrealized P&L"),
                            _number(base.get("realizedpnl", 0), "realized P&L"),
                            positions, Decimal("1") / usd_to_sgd)
        return snapshot, [], []

    @staticmethod
    def _select_account(accounts: list[Any]) -> dict:
        available = [item for item in accounts if isinstance(item, dict) and (item.get("accountId") or item.get("id"))]
        requested = os.getenv("IBKR_ACCOUNT_ID", "").strip()
        if requested:
            for account in available:
                if str(account.get("accountId") or account.get("id")) == requested:
                    return account
            ids = ", ".join(str(item.get("accountId") or item.get("id")) for item in available) or "none"
            raise IBKRError(f"IBKR_ACCOUNT_ID is unavailable; visible accounts: {ids}")
        if len(available) == 1:
            return available[0]
        ids = ", ".join(str(item.get("accountId") or item.get("id")) for item in available) or "none"
        raise IBKRError(f"Set IBKR_ACCOUNT_ID; visible accounts: {ids}")

    @staticmethod
    def _position(row: Any, ledger: dict[str, Any]) -> Position:
        if not isinstance(row, dict):
            raise IBKRError("IBKR returned invalid position data")
        symbol = str(row.get("ticker") or row.get("contractDesc") or row.get("description") or "").strip()
        if not symbol:
            raise IBKRError("IBKR returned a position without a symbol")
        currency = str(row.get("currency") or "").upper()
        currency_ledger = ledger.get(currency)
        if not isinstance(currency_ledger, dict):
            raise IBKRError(f"IBKR returned no {currency} exchange rate")
        rate = _number(currency_ledger.get("exchangerate"), f"{currency} exchange rate")
        if rate <= 0:
            raise IBKRError(f"IBKR returned an invalid {currency} exchange rate")
        asset_type = str(row.get("assetClass") or row.get("secType") or "OTHER").upper()
        return Position(symbol, str(row.get("name") or row.get("description") or symbol),
                        str(row.get("listingExchange") or row.get("countryCode") or ""), currency,
                        _number(row.get("position"), "position quantity"),
                        _number(row.get("avgPrice", row.get("avgCost", 0)), "average price") * rate,
                        _number(row.get("marketPrice", row.get("mktPrice", 0)), "market price") * rate,
                        _number(row.get("marketValue", row.get("mktValue", 0)), "market value") * rate,
                        _number(row.get("unrealizedPnl", 0), "position unrealized P&L") * rate,
                        "STK" if asset_type == "STK" else asset_type)
