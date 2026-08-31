import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from adapter import TigerError, adapter as tiger_adapter
from calculations import net_contributions, performance
from database import Database
from models import Funding
from moomoo_adapter import MoomooAdapter, MoomooError

app = FastAPI(title="Portfolio Tracker")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
db = Database(os.getenv("TIGER_DB_PATH", "backend/portfolio.db"))
moomoo_db = Database(os.getenv("MOOMOO_DB_PATH", "backend/moomoo.db"))
FUNDING_LABELS = {"1": "Deposit", "3": "Withdrawal", "20": "Withdrawal fee", "21": "Withdrawal refund",
                  "22": "Failed-withdrawal refund", "23": "Withdrawal-fee refund"}


def database(broker: str) -> Database:
    if broker == "tiger":
        return db
    if broker == "moomoo":
        return moomoo_db
    raise HTTPException(422, "Broker must be tiger or moomoo")


def currency_factor(currency: str, snapshot: dict) -> Decimal:
    return Decimal("1") if currency == "SGD" else Decimal(snapshot.get("sgd_to_usd", "1"))


def converted(value: str, factor: Decimal) -> str:
    return str(Decimal(value) * factor)


def is_stale(last_success: str | None) -> bool:
    if not last_success:
        return True
    stale_minutes = int(os.getenv("PORTFOLIO_STALE_MINUTES", os.getenv("TIGER_STALE_MINUTES", "60")))
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last_success)).total_seconds() > stale_minutes * 60


def funding_items(store: Database) -> list[Funding]:
    return [Funding(row["transaction_id"], row["type"], row["currency"], Decimal(row["amount"]),
                    datetime.fromisoformat(row["business_date"]).date(), bool(row["completed"]), row["direction"],
                    datetime.fromisoformat(row["settlement_date"]).date() if row["settlement_date"] else None, row["remark"])
            for row in store.rows("SELECT * FROM funding_transactions")]


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


def contribution_items(store: Database, broker: str) -> list[Funding]:
    items = funding_items(store)
    return items if broker == "tiger" else moomoo_contributions(items)


@app.post("/api/sync")
def sync(broker: str = "tiger"):
    store = database(broker)
    try:
        latest = store.one("SELECT captured_at FROM history ORDER BY captured_at DESC LIMIT 1")
        history_start = (datetime.fromisoformat(latest["captured_at"]).date() - timedelta(days=1)).isoformat() if latest else "2015-01-01"
        client = tiger_adapter() if broker == "tiger" else MoomooAdapter()
        snapshot, funding_rows, history_rows = client.fetch() if broker == "moomoo" else client.fetch(history_start)
        if broker == "tiger":
            net_contributions(funding_rows)
        if snapshot.reporting_currency != "SGD":
            raise ValueError("Unexpected snapshot currency; SGD required")
        store.sync(snapshot, funding_rows, history_rows, client.components)
        return {"ok": True, "broker": broker, "capturedAt": snapshot.captured_at.isoformat()}
    except (TigerError, MoomooError, ValueError) as exc:
        store.record_error(str(exc))
        raise HTTPException(status_code=422 if isinstance(exc, ValueError) else 502, detail=str(exc)) from exc
    except Exception as exc:
        store.record_error("Sync failed; previous data was preserved")
        raise HTTPException(status_code=500, detail="Sync failed; previous data was preserved") from exc


class CashFlowRequest(BaseModel):
    dates: list[date]


@app.post("/api/cash-flow/sync")
def sync_cash_flow(request: CashFlowRequest, broker: str = "moomoo"):
    if broker != "moomoo":
        raise HTTPException(422, "Cash-flow sync is available only for Moomoo")
    if not request.dates:
        raise HTTPException(422, "Select at least one cash-flow date")
    if len(request.dates) > 20:
        raise HTTPException(422, "Select no more than 20 cash-flow dates")
    if len(set(request.dates)) != len(request.dates):
        raise HTTPException(422, "Cash-flow dates must be unique")
    if any(value > date.today() for value in request.dates):
        raise HTTPException(422, "Cash-flow dates cannot be in the future")
    try:
        rows = MoomooAdapter().fetch_cash_flows(sorted(request.dates))
        synced_at = moomoo_db.sync_cash_flows(rows)
        verified = sum(1 for item in moomoo_contributions(rows) if item.type)
        return {"ok": True, "rawCount": len(rows), "verifiedCount": verified, "syncedAt": synced_at}
    except MoomooError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@app.get("/api/summary")
def summary(currency: str = Query("SGD", pattern="^(SGD|USD)$"), broker: str = "tiger"):
    store = database(broker)
    supports_contributions = True
    snap = store.one("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
    if not snap:
        return {"empty": True, "supportsContributions": supports_contributions}
    factor = currency_factor(currency, snap)
    position_total = sum((Decimal(row["market_value"]) for row in store.rows(
        "SELECT market_value FROM positions WHERE snapshot_id=?", (snap["id"],))), Decimal())
    result = {"empty": False, "broker": broker, "supportsContributions": supports_contributions,
              "currency": currency, "totalEquity": converted(snap["total_equity"], factor),
              "cash": converted(snap["cash"], factor), "holdingsValue": converted(snap["holdings_value"], factor),
              "unrealizedPnl": converted(snap["unrealized_pnl"], factor),
              "realizedPnl": converted(snap["realized_pnl"], factor),
              "reconciliationDifference": str((Decimal(snap["total_equity"]) - Decimal(snap["cash"]) - position_total) * factor),
              "capturedAt": snap["captured_at"]}
    if supports_contributions:
        try:
            contributions = net_contributions(contribution_items(store, broker))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        pnl, return_pct = performance(Decimal(snap["total_equity"]), contributions)
        result.update(netContributions=str(contributions * factor), overallPnl=str(pnl * factor),
                      overallReturnPct=None if return_pct is None else str(return_pct))
    return result


@app.get("/api/overview")
def overview(currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    keys = ("totalEquity", "netContributions", "cash", "holdingsValue", "stocksValue", "fundsValue", "otherHoldingsValue")
    totals = {key: Decimal() for key in keys}
    brokers = []
    missing = []
    for name, store in (("tiger", db), ("moomoo", moomoo_db)):
        snap = store.one("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
        state = store.one("SELECT last_success,last_error FROM sync_state WHERE id=1") or {}
        if not snap:
            missing.append(name)
            brokers.append({"broker": name, "hasData": False, "totalEquity": "0", "allocationPct": "0",
                            "lastSuccess": state.get("last_success"), "lastError": state.get("last_error"), "stale": True})
            continue
        factor = currency_factor(currency, snap)
        rows = store.rows("SELECT market_value,asset_type FROM positions WHERE snapshot_id=?", (snap["id"],))
        stocks = sum((Decimal(row["market_value"]) for row in rows if row["asset_type"] != "FUND"), Decimal())
        funds = sum((Decimal(row["market_value"]) for row in rows if row["asset_type"] == "FUND"), Decimal())
        holdings = Decimal(snap["holdings_value"])
        unclassified = holdings - stocks - funds
        if abs(unclassified) < Decimal("1"):
            unclassified = Decimal()
        values = {"totalEquity": Decimal(snap["total_equity"]),
                  "netContributions": net_contributions(contribution_items(store, name)),
                  "cash": Decimal(snap["cash"]), "holdingsValue": holdings,
                  "stocksValue": stocks, "fundsValue": funds,
                  "otherHoldingsValue": max(unclassified, Decimal())}
        for key, value in values.items():
            totals[key] += value * factor
        brokers.append({"broker": name, "hasData": True, "totalEquity": str(values["totalEquity"] * factor),
                        "allocationPct": "0", "lastSuccess": state.get("last_success"),
                        "lastError": state.get("last_error"), "stale": is_stale(state.get("last_success"))})
    for item in brokers:
        if totals["totalEquity"] and item["hasData"]:
            item["allocationPct"] = str(Decimal(item["totalEquity"]) / totals["totalEquity"] * 100)
    return {"currency": currency, "complete": not missing, "missingBrokers": missing,
            "brokerCount": 2 - len(missing), **{key: str(value) for key, value in totals.items()},
            "overallPnl": str(totals["totalEquity"] - totals["netContributions"]), "brokers": brokers}


@app.get("/api/positions")
def positions(currency: str = Query("SGD", pattern="^(SGD|USD)$"), broker: str = "tiger"):
    store = database(broker)
    latest = store.one("SELECT id FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
    if not latest:
        return []
    snap = store.one("SELECT sgd_to_usd FROM portfolio_snapshots WHERE id=?", (latest["id"],))
    factor = currency_factor(currency, snap)
    rows = store.rows("SELECT symbol,name,market,currency,quantity,average_cost,market_price,market_value,unrealized_pnl,asset_type FROM positions WHERE snapshot_id=? ORDER BY market_value DESC", (latest["id"],))
    for row in rows:
        row["display_currency"] = currency
        for field in ("average_cost", "market_price", "market_value", "unrealized_pnl"):
            row[field] = converted(row[field], factor)
    return rows


@app.get("/api/funding")
def funding(currency: str = Query("SGD", pattern="^(SGD|USD)$"), broker: str = "tiger"):
    store = database(broker)
    snap = store.one("SELECT sgd_to_usd FROM portfolio_snapshots ORDER BY id DESC LIMIT 1") or {"sgd_to_usd": "1"}
    factor = currency_factor(currency, snap)
    if broker == "moomoo":
        rows = [{"transaction_id": item.transaction_id, "type": item.type, "currency": item.currency,
                 "amount": str(item.amount), "business_date": item.business_date.isoformat(),
                 "completed": int(item.completed), "direction": item.direction,
                 "settlement_date": item.settlement_date.isoformat() if item.settlement_date else None,
                 "remark": item.remark} for item in contribution_items(store, broker) if item.type]
        rows.sort(key=lambda row: row["business_date"], reverse=True)
    else:
        rows = store.rows("SELECT transaction_id,type,currency,amount,business_date,completed,direction,settlement_date,remark FROM funding_transactions ORDER BY business_date DESC")
    for row in rows:
        row["original_currency"] = row["currency"]
        row["display_currency"] = row["currency"] if broker == "moomoo" else currency
        row["type_label"] = FUNDING_LABELS.get(row["type"], row["type"].replace("_", " ").title())
        if broker == "tiger":
            row["amount"] = converted(row["amount"], factor)
    return rows


@app.get("/api/history")
def history(currency: str = Query("SGD", pattern="^(SGD|USD)$"), broker: str = "tiger"):
    store = database(broker)
    stored = store.rows("SELECT captured_at,total_equity,sgd_to_usd FROM history ORDER BY captured_at")
    if not stored:
        stored = store.rows("SELECT captured_at,total_equity,sgd_to_usd FROM portfolio_snapshots ORDER BY captured_at")
    items = contribution_items(store, broker)
    previous_contributions: Decimal | None = None
    for row in stored:
        point_date = datetime.fromisoformat(row["captured_at"]).date()
        contributions = net_contributions([item for item in items if item.business_date <= point_date])
        factor = Decimal("1") if currency == "SGD" else Decimal(row["sgd_to_usd"])
        equity = Decimal(row["total_equity"])
        row["total_equity"] = str(equity * factor)
        row["net_contributions"] = str(contributions * factor)
        row["performance_value"] = str((equity - contributions) * factor)
        row["funding_event"] = previous_contributions is not None and contributions != previous_contributions
        previous_contributions = contributions
        del row["sgd_to_usd"]
    return stored


@app.get("/api/sync/status")
def status(broker: str = "tiger"):
    state = database(broker).one("SELECT last_success,last_error,components,cash_flow_last_success FROM sync_state WHERE id=1") or {}
    last = state.get("last_success")
    return {"lastSuccess": last, "lastError": state.get("last_error"), "stale": is_stale(last), "mode": "live",
            "broker": broker, "components": json.loads(state.get("components") or "{}"),
            "cashFlowLastSuccess": state.get("cash_flow_last_success")}


@app.get("/api/export/{dataset}")
def export_csv(dataset: str, currency: str = Query("SGD", pattern="^(SGD|USD)$"), broker: str = "tiger"):
    sources = {"positions": lambda: positions(currency, broker), "funding": lambda: funding(currency, broker),
               "history": lambda: history(currency, broker)}
    if dataset not in sources:
        raise HTTPException(404, "Export must be positions, funding, or history")
    rows = sources[dataset]()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{broker}-{dataset}-{currency.lower()}.csv"'})
