import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from models import Funding, HistoryPoint, Position, Snapshot

logging.getLogger("tigeropen").setLevel(logging.CRITICAL)
logging.getLogger("backoff").setLevel(logging.CRITICAL)


class TigerError(RuntimeError):
    pass


def _request_error(stage: str, exc: Exception) -> TigerError:
    message = str(exc).lower()
    if "certificate_verify_failed" in message or "certificate verify failed" in message:
        detail = "TLS certificate verification failed"
    elif "timed out" in message or "timeout" in message:
        detail = "request timed out"
    elif "rate" in message or "too many" in message:
        detail = "rate limit reached; try again later"
    elif "auth" in message or "permission" in message or "signature" in message:
        detail = "authentication or permission was rejected"
    elif "connection" in message or "network" in message:
        detail = "network connection failed"
    else:
        detail = f"request failed ({type(exc).__name__})"
    return TigerError(f"Tiger {stage}: {detail}; previous data was preserved")


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except Exception as exc:
        raise TigerError(f"Tiger returned an invalid {field}") from exc


def _records(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    if isinstance(value, dict):
        value = value.get("history", value.get("items", []))
    return list(value)


def _funds_are_excluded(equity: Decimal, cash: Decimal, positions: tuple[Position, ...]) -> bool:
    funds = sum((position.market_value for position in positions if position.asset_type == "FUND"), Decimal())
    all_positions = sum((position.market_value for position in positions), Decimal())
    reported_holdings = equity - cash
    return funds != 0 and abs(reported_holdings - (all_positions - funds)) < abs(reported_holdings - all_positions)


class LiveTigerAdapter:
    """Read-only normalization boundary around the official Tiger OpenAPI SDK."""

    def __init__(self) -> None:
        self.components: dict[str, str] = {}
        tiger_id = os.getenv("TIGER_ID")
        account = os.getenv("TIGER_ACCOUNT")
        key_path = os.getenv("TIGER_PRIVATE_KEY_PATH")
        if not tiger_id or not account or not key_path:
            raise TigerError("Live mode requires TIGER_ID, TIGER_ACCOUNT and TIGER_PRIVATE_KEY_PATH")
        try:
            from tigeropen.common.util.signature_utils import read_private_key
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.trade.trade_client import TradeClient
            config = TigerOpenClientConfig(sandbox_debug=False)
            config.log_level = logging.CRITICAL
            config.tiger_id, config.account = tiger_id, account
            config.private_key = read_private_key(key_path)
            self.client, self.account = TradeClient(config), account
        except Exception as exc:
            raise TigerError("Tiger authentication/configuration failed") from exc

    def fetch(self, history_start: str = "2015-01-01") -> tuple[Snapshot, list[Funding], list[HistoryPoint]]:
        try:
            try:
                assets = self.client.get_prime_assets(account=self.account, base_currency="SGD")
                self.components["assets"] = "ok"
            except Exception as exc:
                raise _request_error("assets", exc) from exc
            try:
                usd_assets = self.client.get_prime_assets(account=self.account, base_currency="USD")
                self.components["exchange_rate"] = "ok"
            except Exception as exc:
                raise _request_error("USD conversion rate", exc) from exc
            try:
                raw_positions = self.client.get_positions(account=self.account, sec_type="STK")
                self.components["stocks"] = "ok"
            except Exception as exc:
                raise _request_error("stock positions", exc) from exc
            try:
                raw_funds = self.client.get_positions(account=self.account, sec_type="FUND")
                self.components["funds"] = "ok"
            except Exception as exc:
                raise _request_error("fund positions", exc) from exc
            try:
                raw_funding = self.client.get_funding_history()
                self.components["funding"] = "ok"
            except Exception as exc:
                raise _request_error("funding history", exc) from exc
            try:
                history_end = date.today().isoformat()
                raw_history = self.client.get_analytics_asset(account=self.account, start_date=history_start, end_date=history_end, currency="SGD")
                self.components["analytics"] = "ok"
            except Exception:
                raw_history = []
                self.components["analytics"] = "unavailable"
            try:
                raw_history_usd = self.client.get_analytics_asset(account=self.account, start_date=history_start, end_date=history_end, currency="USD")
                self.components["historical_fx"] = "ok"
            except Exception:
                raw_history_usd = []
                self.components["historical_fx"] = "current-rate fallback"
            segments = _value(assets, "segments", default={})
            segment = (segments.get("S") or next(iter(segments.values()), None)) if isinstance(segments, dict) else assets
            if segment is None:
                raise TigerError("Tiger returned no Prime asset segment")
            securities_equity = _decimal(_value(segment, "net_liquidation", "equity", "total_equity"), "total equity")
            usd_segments = _value(usd_assets, "segments", default={})
            usd_segment = (usd_segments.get("S") or next(iter(usd_segments.values()), None)) if isinstance(usd_segments, dict) else usd_assets
            usd_securities_equity = _decimal(_value(usd_segment, "net_liquidation", "equity", "total_equity"), "USD total equity")
            sgd_to_usd = Decimal("1") if securities_equity == 0 else usd_securities_equity / securities_equity
            cash = _decimal(_value(segment, "cash", "cash_balance", default=0), "cash")
            currency_assets = _value(segment, "currency_assets", default={})
            rates = {str(code): _decimal(_value(asset, "forex_rate", "exchange_rate", default=1), f"{code} exchange rate")
                     for code, asset in currency_assets.items()}
            rates["SGD"] = Decimal("1")
            positions = tuple([*(self._position(p, rates, "STK") for p in _records(raw_positions)),
                               *(self._position(p, rates, "FUND") for p in _records(raw_funds))])
            funds = [position for position in positions if position.asset_type == "FUND"]
            funds_excluded = _funds_are_excluded(securities_equity, cash, positions)
            equity = securities_equity + (sum((position.market_value for position in funds), Decimal()) if funds_excluded else Decimal())
            snapshot_value = Snapshot(datetime.now(timezone.utc), "SGD", equity, cash, equity - cash,
                                      _decimal(_value(segment, "unrealized_pl", "unrealized_pnl", default=0), "unrealized P&L") + (sum((position.unrealized_pnl for position in funds), Decimal()) if funds_excluded else Decimal()),
                                      _decimal(_value(segment, "realized_pl", "realized_pnl", default=0), "realized P&L"), positions, sgd_to_usd)
            sgd_history = [self._history(h) for h in _records(raw_history)]
            usd_history = {point.captured_at.date(): point.total_equity for point in (self._history(h) for h in _records(raw_history_usd))}
            normalized_history = [HistoryPoint(point.captured_at, point.total_equity,
                                               sgd_to_usd if point.total_equity == 0 or point.captured_at.date() not in usd_history else usd_history[point.captured_at.date()] / point.total_equity)
                                  for point in sgd_history]
            return snapshot_value, [self._funding(f) for f in _records(raw_funding)], normalized_history
        except TigerError:
            raise
        except Exception as exc:
            raise TigerError(f"Tiger response processing failed ({type(exc).__name__}); previous data was preserved") from exc

    def _position(self, p: Any, rates: dict[str, Decimal] | None = None, asset_type_override: str | None = None) -> Position:
        contract = _value(p, "contract", default={})
        symbol = _value(p, "symbol", default=_value(contract, "symbol"))
        if not symbol:
            raise TigerError("Tiger returned a position without a symbol")
        currency = str(_value(p, "currency", default=_value(contract, "currency", default="")))
        asset_type = asset_type_override or str(_value(p, "sec_type", default=_value(contract, "sec_type", default="STK")))
        rate = (rates or {}).get(currency, Decimal("1"))
        return Position(str(symbol), str(_value(p, "name", default=_value(contract, "name", default=symbol))),
                        str(_value(p, "market", default=_value(contract, "market", default=""))), currency,
                        _decimal(_value(p, "quantity", default=0), "quantity"), _decimal(_value(p, "average_cost", "avg_cost", default=0), "average cost") * rate,
                        _decimal(_value(p, "market_price", "latest_price", default=0), "market price") * rate, _decimal(_value(p, "market_value", default=0), "market value") * rate,
                        _decimal(_value(p, "unrealized_pnl", default=0), "unrealized P&L") * rate, asset_type)

    def _funding(self, f: Any) -> Funding:
        raw_completed = _value(f, "completed_status")
        completed = bool(raw_completed) if raw_completed is not None else str(_value(f, "status", "state", default="")).upper() in {"COMPLETED", "SUCCESS", "SUCC", "DONE"}
        raw_date = _value(f, "business_date", "biz_date", "date")
        business_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10].replace("/", "-"))
        return Funding(str(_value(f, "transaction_id", "id")), str(_value(f, "type", "funding_type")),
                       str(_value(f, "currency")), _decimal(_value(f, "amount"), "funding amount"), business_date, completed)

    def _history(self, h: Any) -> HistoryPoint:
        raw = _value(h, "timestamp", "dt", "date", "time")
        captured = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return HistoryPoint(captured, _decimal(_value(h, "asset", "equity", "total_equity"), "historical equity"))


def adapter():
    return LiveTigerAdapter()
