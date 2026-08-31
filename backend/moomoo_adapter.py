import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from adapter import _decimal, _records, _value
from models import Funding, Position, Snapshot


class MoomooError(RuntimeError):
    pass


CASH_FIELDS = {"SGD": "sg_cash", "USD": "us_cash", "HKD": "hk_cash", "CNH": "cn_cash",
               "JPY": "jp_cash", "AUD": "au_cash", "CAD": "ca_cash", "MYR": "my_cash"}


def _number(value: Any, default: Decimal = Decimal()) -> Decimal:
    try:
        result = Decimal(str(value))
        return result if math.isfinite(float(result)) else default
    except Exception:
        return default


class MoomooAdapter:
    """Read-only normalization boundary around a localhost Moomoo OpenD gateway."""

    def __init__(self, client=None, sdk=None) -> None:
        self.components: dict[str, str] = {}
        self.account = int(os.getenv("MOOMOO_ACCOUNT_ID", "0"))
        if not self.account:
            raise MoomooError("Moomoo requires MOOMOO_ACCOUNT_ID")
        host = os.getenv("MOOMOO_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise MoomooError("Moomoo OpenD must use a localhost address")
        try:
            if sdk is None:
                import moomoo as sdk
            self.sdk = sdk
            self.client = client or sdk.OpenSecTradeContext(
                filter_trdmarket=sdk.TrdMarket.SG, host=host,
                port=int(os.getenv("MOOMOO_PORT", "11111")), security_firm=sdk.SecurityFirm.FUTUSG)
        except Exception as exc:
            raise MoomooError("Moomoo OpenD connection failed; start and log in to OpenD") from exc

    def _query(self, component: str, call):
        try:
            result, data = call()
            if result != self.sdk.RET_OK:
                raise MoomooError(f"Moomoo {component}: {data}")
            self.components[component] = "ok"
            return _records(data)
        except MoomooError:
            raise
        except Exception as exc:
            raise MoomooError(f"Moomoo {component} request failed ({type(exc).__name__}); previous data was preserved") from exc

    def _funds(self, currency: str) -> dict:
        rows = self._query(f"assets_{currency.lower()}", lambda: self.client.accinfo_query(
            trd_env=self.sdk.TrdEnv.REAL, acc_id=self.account, refresh_cache=False,
            currency=getattr(self.sdk.Currency, currency)))
        if not rows:
            raise MoomooError(f"Moomoo returned no {currency} account assets")
        return rows[0]

    def fetch(self, history_start: str = "", extra_cash_flow_dates: list[str] | None = None):
        try:
            accounts = self._query("account", self.client.get_acc_list)
            if not any(int(_value(row, "acc_id", default=0)) == self.account and str(_value(row, "trd_env", default="")).upper().endswith("REAL") for row in accounts):
                raise MoomooError("MOOMOO_ACCOUNT_ID is not a real account available in OpenD")
            raw_positions = self._query("positions", lambda: self.client.position_list_query(
                trd_env=self.sdk.TrdEnv.REAL, acc_id=self.account, refresh_cache=False))
            sgd_assets = self._funds("SGD")
            currencies = {str(_value(row, "currency", default="SGD")).upper() for row in raw_positions}
            currencies.update(code for code, field in CASH_FIELDS.items() if _number(_value(sgd_assets, field)) != 0)
            currencies.add("USD")
            assets = {"SGD": sgd_assets}
            for currency in currencies - {"SGD"}:
                if hasattr(self.sdk.Currency, currency):
                    assets[currency] = self._funds(currency)
            total = _decimal(_value(sgd_assets, "total_assets"), "Moomoo total assets")
            rates = {currency: total / _decimal(_value(row, "total_assets"), f"Moomoo {currency} assets")
                     for currency, row in assets.items() if _number(_value(row, "total_assets")) != 0}
            rates["SGD"] = Decimal("1")
            positions = tuple(self._position(row, rates) for row in raw_positions)
            cash = sum((_number(_value(sgd_assets, field)) * rates.get(currency, Decimal())
                        for currency, field in CASH_FIELDS.items()), Decimal())
            fund_assets = _number(_value(sgd_assets, "fund_assets"))
            position_total = sum((position.market_value for position in positions), Decimal())
            if fund_assets > 0 and abs(total - cash - position_total - fund_assets) < abs(total - cash - position_total):
                positions += (Position("MOOMOO_FUNDS", "Moomoo fund assets", "SG", "SGD", Decimal("1"),
                                       fund_assets, fund_assets, fund_assets, Decimal(), "FUND"),)
            usd_total = _decimal(_value(assets["USD"], "total_assets"), "Moomoo USD assets")
            snapshot = Snapshot(datetime.now(timezone.utc), "SGD", total, cash, total - cash,
                                sum((position.unrealized_pnl for position in positions), Decimal()), Decimal(),
                                positions, Decimal("1") if total == 0 else usd_total / total)
            flows = []
            current = date.fromisoformat(history_start) if history_start else date.today()
            dates = set(extra_cash_flow_dates or [])
            while current <= date.today():
                dates.add(current.isoformat())
                current += timedelta(days=1)
            if len(dates) > 20:
                raise MoomooError("Moomoo cash-flow refresh is limited to 20 dates at a time")
            for value in sorted(dates):
                day = date.fromisoformat(value)
                rows = self._query("cash_flow", lambda day=day: self.client.get_acc_cash_flow(
                    clearing_date=day.isoformat(), trd_env=self.sdk.TrdEnv.REAL, acc_id=self.account,
                    cashflow_direction=self.sdk.CashFlowDirection.NONE))
                flows.extend(self._cash_flow(row) for row in rows)
            return snapshot, flows, []
        finally:
            try:
                self.client.close()
            except Exception:
                pass

    @staticmethod
    def _position(row: Any, rates: dict[str, Decimal]) -> Position:
        symbol = str(_value(row, "code", default=""))
        if not symbol:
            raise MoomooError("Moomoo returned a position without a code")
        currency = str(_value(row, "currency", default="SGD")).upper()
        if currency not in rates:
            raise MoomooError(f"Moomoo returned unsupported position currency {currency}")
        rate = rates[currency]
        market = symbol.split(".", 1)[0] if "." in symbol else str(_value(row, "position_market", default=""))
        return Position(symbol, str(_value(row, "stock_name", default=symbol)), market, currency,
                        _decimal(_value(row, "qty", default=0), "Moomoo quantity"),
                        _number(_value(row, "average_cost", "cost_price")) * rate,
                        _number(_value(row, "nominal_price")) * rate,
                        _number(_value(row, "market_val")) * rate,
                        _number(_value(row, "unrealized_pl")) * rate)

    @staticmethod
    def _cash_flow(row: Any) -> Funding:
        flow_id = _value(row, "cashflow_id")
        if flow_id in (None, ""):
            raise MoomooError("Moomoo returned cash flow without an ID")
        raw_date = str(_value(row, "clearing_date", default=""))[:10]
        if not raw_date:
            raise MoomooError("Moomoo returned cash flow without a clearing date")
        settlement = str(_value(row, "settlement_date", default=""))[:10]
        return Funding(str(flow_id), str(_value(row, "cashflow_type", default="Others")),
                       str(_value(row, "currency")), _decimal(_value(row, "cashflow_amount"), "Moomoo cash flow amount"),
                       date.fromisoformat(raw_date), True, str(_value(row, "cashflow_direction", default="")),
                       date.fromisoformat(settlement) if settlement else None, str(_value(row, "cashflow_remark", default="")))
