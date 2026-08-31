import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

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


def funding_items(store: Database) -> list[Funding]:
    return [Funding(row["transaction_id"], row["type"], row["currency"], Decimal(row["amount"]),
                    datetime.fromisoformat(row["business_date"]).date(), bool(row["completed"]), row["direction"],
                    datetime.fromisoformat(row["settlement_date"]).date() if row["settlement_date"] else None, row["remark"])
            for row in store.rows("SELECT * FROM funding_transactions")]


def contribution_items(store: Database, broker: str) -> list[Funding]:
    items = funding_items(store)
    if broker == "tiger":
        return items
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


@app.post("/api/sync")
def sync(broker: str = "tiger"):
    store = database(broker)
    try:
        client = tiger_adapter() if broker == "tiger" else MoomooAdapter()
        latest = store.one("SELECT captured_at FROM history ORDER BY captured_at DESC LIMIT 1")
        cash_flow_dates: list[str] = []
        cash_flow_checked_through = None
        if broker == "moomoo":
            latest_flow = store.one("SELECT max(business_date) value FROM funding_transactions")
            days = min(max(int(os.getenv("MOOMOO_CASH_FLOW_DAYS", "20")), 1), 20)
            today = datetime.now(timezone.utc).date()
            state = store.one("SELECT cash_flow_checked_through FROM sync_state WHERE id=1") or {}
            checkpoint = state.get("cash_flow_checked_through")
            lookback = (today - timedelta(days=days - 1)).isoformat()
            history_start = max((datetime.fromisoformat(checkpoint).date() + timedelta(days=1)).isoformat(), lookback) if checkpoint else (today.isoformat() if latest_flow and latest_flow["value"] else lookback)
            history_start = min(history_start, today.isoformat())
            configured = {value.strip() for value in os.getenv("MOOMOO_CASH_FLOW_DATES", "").split(",") if value.strip()}
            stored = {row["business_date"] for row in store.rows("SELECT DISTINCT business_date FROM funding_transactions")}
            cash_flow_dates = sorted(configured - stored)
            if cash_flow_dates:
                history_start = today.isoformat()
            cash_flow_checked_through = today.isoformat()
        else:
            history_start = (datetime.fromisoformat(latest["captured_at"]).date() - timedelta(days=1)).isoformat() if latest else "2015-01-01"
        snapshot, funding_rows, history_rows = client.fetch(history_start, cash_flow_dates) if broker == "moomoo" else client.fetch(history_start)
        if broker == "tiger":
            net_contributions(funding_rows)
        if snapshot.reporting_currency != "SGD":
            raise ValueError("Unexpected snapshot currency; SGD required")
        store.sync(snapshot, funding_rows, history_rows, client.components, cash_flow_checked_through)
        return {"ok": True, "broker": broker, "capturedAt": snapshot.captured_at.isoformat()}
    except (TigerError, MoomooError, ValueError) as exc:
        store.record_error(str(exc))
        raise HTTPException(status_code=422 if isinstance(exc, ValueError) else 502, detail=str(exc)) from exc
    except Exception as exc:
        store.record_error("Sync failed; previous data was preserved")
        raise HTTPException(status_code=500, detail="Sync failed; previous data was preserved") from exc


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
    state = database(broker).one("SELECT last_success,last_error,components,cash_flow_checked_through FROM sync_state WHERE id=1") or {}
    stale_minutes = int(os.getenv("PORTFOLIO_STALE_MINUTES", os.getenv("TIGER_STALE_MINUTES", "60")))
    last = state.get("last_success")
    stale = not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > stale_minutes * 60
    return {"lastSuccess": last, "lastError": state.get("last_error"), "stale": stale, "mode": "live",
            "broker": broker, "components": json.loads(state.get("components") or "{}"),
            "cashFlowCheckedThrough": state.get("cash_flow_checked_through")}


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
