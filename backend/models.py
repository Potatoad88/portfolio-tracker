from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Funding:
    transaction_id: str
    type: str
    currency: str
    amount: Decimal
    business_date: date
    completed: bool


@dataclass(frozen=True)
class Position:
    symbol: str
    name: str
    market: str
    currency: str
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    asset_type: str = "STK"


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    reporting_currency: str
    total_equity: Decimal
    cash: Decimal
    holdings_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    positions: tuple[Position, ...]
    sgd_to_usd: Decimal = Decimal("1")


@dataclass(frozen=True)
class HistoryPoint:
    captured_at: datetime
    total_equity: Decimal
    sgd_to_usd: Decimal = Decimal("1")
