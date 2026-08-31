from decimal import Decimal
from models import Funding


DEPOSIT_TYPES = {"DEPOSIT", "1"}
WITHDRAWAL_TYPES = {"WITHDRAWAL", "3"}
FEE_TYPES = {"WITHDRAWAL_FEE", "20"}
REFUND_TYPES = {"WITHDRAWAL_REFUND", "REFUND", "21", "22", "23"}


def net_contributions(items: list[Funding]) -> Decimal:
    total = Decimal("0")
    for item in items:
        if not item.completed:
            continue
        if item.currency.upper() != "SGD":
            raise ValueError(f"Unexpected funding currency {item.currency}; only SGD is supported")
        kind = item.type.upper()
        if kind in DEPOSIT_TYPES:
            total += abs(item.amount)
        elif kind in WITHDRAWAL_TYPES or kind in FEE_TYPES:
            total -= abs(item.amount)
        elif kind in REFUND_TYPES:
            total += abs(item.amount)
    return total


def performance(equity: Decimal, contributions: Decimal) -> tuple[Decimal, Decimal | None]:
    pnl = equity - contributions
    return pnl, None if contributions == 0 else pnl / contributions * Decimal("100")
