import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from adapter import TigerError, adapter as tiger_adapter
from models import Funding
from moomoo_adapter import MoomooAdapter, MoomooError


@dataclass(frozen=True)
class BrokerCapabilities:
    contributions: bool = True
    performance_history: bool = True
    funding_history: bool = True
    cash_flow_sync: bool = False
    exports: bool = True

    def public(self) -> dict[str, bool]:
        return {
            "contributions": self.contributions,
            "performanceHistory": self.performance_history,
            "fundingHistory": self.funding_history,
            "cashFlowSync": self.cash_flow_sync,
            "exports": self.exports,
        }


@dataclass
class BrokerDefinition:
    id: str
    display_name: str
    database_env: str
    database_default: str
    required_env: tuple[str, ...]
    adapter_factory: Callable[[], object]
    errors: tuple[type[Exception], ...]
    capabilities: BrokerCapabilities = BrokerCapabilities()
    fetches_history: bool = False
    validates_funding: bool = False
    convert_funding_currency: bool = False
    display_filtered_contributions: bool = False
    contribution_filter: Callable[[list[Funding]], list[Funding]] = lambda items: items

    @property
    def configured(self) -> bool:
        return all(os.getenv(name, "").strip() for name in self.required_env)

    def public(self) -> dict:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "configured": self.configured,
            "capabilities": self.capabilities.public(),
        }


def moomoo_contributions(items: list[Funding]) -> list[Funding]:
    manual = {value.strip() for value in os.getenv("MOOMOO_MANUAL_WITHDRAWALS", "").split(",") if value.strip()}
    result = []
    for item in items:
        kind = ""
        if item.currency.upper() == "SGD" and item.direction == "IN" and item.remark.upper().startswith(("DDIIRC", "DDIIRGPC")):
            kind = "DEPOSIT"
        elif item.currency.upper() == "SGD" and (item.type == "Bank Transfer Withdrawals" or f"{item.business_date.isoformat()}:{abs(item.amount)}" in manual):
            kind = "WITHDRAWAL"
        result.append(Funding(item.transaction_id, kind, item.currency, item.amount, item.business_date,
                              item.completed, item.direction, item.settlement_date, item.remark))
    return result


BROKERS = {
    "tiger": BrokerDefinition(
        id="tiger", display_name="Tiger", database_env="TIGER_DB_PATH",
        database_default="backend/portfolio.db",
        required_env=("TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"),
        adapter_factory=tiger_adapter, errors=(TigerError,), fetches_history=True,
        validates_funding=True, convert_funding_currency=True,
    ),
    "moomoo": BrokerDefinition(
        id="moomoo", display_name="Moomoo", database_env="MOOMOO_DB_PATH",
        database_default="backend/moomoo.db", required_env=("MOOMOO_ACCOUNT_ID",),
        adapter_factory=MoomooAdapter, errors=(MoomooError,),
        capabilities=BrokerCapabilities(cash_flow_sync=True),
        display_filtered_contributions=True, contribution_filter=moomoo_contributions,
    ),
}


def definition(broker: str) -> BrokerDefinition:
    try:
        return BROKERS[broker]
    except KeyError as exc:
        raise ValueError(f"Broker must be one of: {', '.join(BROKERS)}") from exc


def requested_history_start(latest: dict | None) -> str:
    return (datetime.fromisoformat(latest["captured_at"]).date() - timedelta(days=1)).isoformat() if latest else "2015-01-01"
