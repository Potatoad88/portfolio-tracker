from decimal import Decimal
from models import Funding


DEPOSIT_TYPES = {"DEPOSIT", "1"}
WITHDRAWAL_TYPES = {"WITHDRAWAL", "3"}
FEE_TYPES = {"WITHDRAWAL_FEE", "20"}
REFUND_TYPES = {"WITHDRAWAL_REFUND", "REFUND", "21", "22", "23"}


def contribution_breakdown(items: list[Funding]) -> dict[str, Decimal]:
    totals = {"deposits": Decimal(), "withdrawals": Decimal(),
              "fees": Decimal(), "refunds": Decimal()}
    categories = ((DEPOSIT_TYPES, "deposits"), (WITHDRAWAL_TYPES, "withdrawals"),
                  (FEE_TYPES, "fees"), (REFUND_TYPES, "refunds"))
    for item in items:
        if not item.completed:
            continue
        kind = item.type.upper()
        for types, category in categories:
            if kind in types:
                if item.currency.upper() != "SGD":
                    raise ValueError(f"Unexpected funding currency {item.currency}; only SGD is supported")
                totals[category] += abs(item.amount)
                break
    totals["net_contributions"] = (totals["deposits"] - totals["withdrawals"]
                                    - totals["fees"] + totals["refunds"])
    return totals


def net_contributions(items: list[Funding]) -> Decimal:
    return contribution_breakdown(items)["net_contributions"]


def reconciliation_breakdown(equity: Decimal, cash: Decimal, holdings: Decimal,
                             positions: list[dict]) -> dict[str, Decimal | bool]:
    stocks = sum((Decimal(row["market_value"]) for row in positions
                  if row["asset_type"] in {"STK", "ETF"}), Decimal())
    funds = sum((Decimal(row["market_value"]) for row in positions
                 if row["asset_type"] == "FUND"), Decimal())
    other = sum((Decimal(row["market_value"]) for row in positions
                 if row["asset_type"] not in {"STK", "ETF", "FUND"}), Decimal())
    position_total = stocks + funds + other
    unclassified = holdings - position_total
    composition_difference = equity - cash - holdings
    return {
        "stocks_value": stocks,
        "funds_value": funds,
        "other_positions_value": other,
        "position_total": position_total,
        "unclassified_holdings_value": unclassified,
        "equity_composition_difference": composition_difference,
        "reconciliation_difference": equity - cash - position_total,
        "reconciled": abs(unclassified) <= Decimal("1") and abs(composition_difference) <= Decimal("1"),
    }


def performance(equity: Decimal, contributions: Decimal) -> tuple[Decimal, Decimal | None]:
    pnl = equity - contributions
    return pnl, None if contributions == 0 else pnl / contributions * Decimal("100")
