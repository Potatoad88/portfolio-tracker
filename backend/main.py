import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from adapter import TigerError, adapter
from calculations import net_contributions, performance
from database import Database
from models import Funding

app = FastAPI(title="Tiger Portfolio Tracker")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
db = Database(os.getenv("TIGER_DB_PATH", "backend/portfolio.db"))
FUNDING_LABELS = {"1": "Deposit", "3": "Withdrawal", "20": "Withdrawal fee", "21": "Withdrawal refund",
                  "22": "Failed-withdrawal refund", "23": "Withdrawal-fee refund"}


def currency_factor(currency: str, snapshot: dict) -> Decimal:
    return Decimal("1") if currency == "SGD" else Decimal(snapshot.get("sgd_to_usd", "1"))


def converted(value: str, factor: Decimal) -> str:
    return str(Decimal(value) * factor)


@app.post("/api/sync")
def sync():
    try:
        client = adapter()
        latest_history = db.one("SELECT captured_at FROM history ORDER BY captured_at DESC LIMIT 1")
        history_start = (datetime.fromisoformat(latest_history["captured_at"]).date() - timedelta(days=1)).isoformat() if latest_history else "2015-01-01"
        snapshot, funding, history = client.fetch(history_start)
        net_contributions(funding)  # validate before the transaction begins
        if snapshot.reporting_currency != "SGD":
            raise ValueError("Unexpected snapshot currency; SGD required")
        db.sync(snapshot, funding, history, client.components)
        return {"ok": True, "capturedAt": snapshot.captured_at.isoformat()}
    except (TigerError, ValueError) as exc:
        db.record_error(str(exc))
        raise HTTPException(status_code=422 if isinstance(exc, ValueError) else 502, detail=str(exc)) from exc
    except Exception as exc:
        db.record_error("Sync failed; previous data was preserved")
        raise HTTPException(status_code=500, detail="Sync failed; previous data was preserved") from exc


@app.get("/api/summary")
def summary(currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    snap = db.one("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
    if not snap:
        return {"empty": True}
    funding = [Funding(r["transaction_id"], r["type"], r["currency"], Decimal(r["amount"]), datetime.fromisoformat(r["business_date"]).date(), bool(r["completed"])) for r in db.rows("SELECT * FROM funding_transactions")]
    try:
        contributions = net_contributions(funding)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    pnl, return_pct = performance(Decimal(snap["total_equity"]), contributions)
    factor = currency_factor(currency, snap)
    position_total = sum((Decimal(row["market_value"]) for row in db.rows("SELECT market_value FROM positions WHERE snapshot_id=?", (snap["id"],))), Decimal())
    reconciliation = (Decimal(snap["total_equity"]) - Decimal(snap["cash"]) - position_total) * factor
    return {"empty": False, "currency": currency, "totalEquity": converted(snap["total_equity"], factor), "netContributions": str(contributions * factor),
            "overallPnl": str(pnl * factor), "overallReturnPct": None if return_pct is None else str(return_pct), "cash": converted(snap["cash"], factor),
            "holdingsValue": converted(snap["holdings_value"], factor), "unrealizedPnl": converted(snap["unrealized_pnl"], factor), "realizedPnl": converted(snap["realized_pnl"], factor),
            "reconciliationDifference": str(reconciliation), "capturedAt": snap["captured_at"]}


@app.get("/api/positions")
def positions(currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    latest = db.one("SELECT id FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
    if not latest:
        return []
    snap = db.one("SELECT sgd_to_usd FROM portfolio_snapshots WHERE id=?", (latest["id"],))
    factor = currency_factor(currency, snap)
    rows = db.rows("SELECT symbol,name,market,currency,quantity,average_cost,market_price,market_value,unrealized_pnl,asset_type FROM positions WHERE snapshot_id=? ORDER BY market_value DESC", (latest["id"],))
    for row in rows:
        row["display_currency"] = currency
        for field in ("average_cost", "market_price", "market_value", "unrealized_pnl"):
            row[field] = converted(row[field], factor)
    return rows


@app.get("/api/funding")
def funding(currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    snap = db.one("SELECT sgd_to_usd FROM portfolio_snapshots ORDER BY id DESC LIMIT 1") or {"sgd_to_usd": "1"}
    factor = currency_factor(currency, snap)
    rows = db.rows("SELECT transaction_id,type,currency,amount,business_date,completed FROM funding_transactions ORDER BY business_date DESC")
    for row in rows:
        row["original_currency"], row["display_currency"] = row["currency"], currency
        row["type_label"] = FUNDING_LABELS.get(row["type"], row["type"].replace("_", " ").title())
        row["amount"] = converted(row["amount"], factor)
    return rows


@app.get("/api/history")
def history(currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    stored = db.rows("SELECT captured_at,total_equity,sgd_to_usd FROM history ORDER BY captured_at")
    if not stored:
        stored = db.rows("SELECT captured_at,total_equity,sgd_to_usd FROM portfolio_snapshots ORDER BY captured_at")
    funding_rows = db.rows("SELECT transaction_id,type,currency,amount,business_date,completed FROM funding_transactions")
    funding_items = [Funding(r["transaction_id"], r["type"], r["currency"], Decimal(r["amount"]), datetime.fromisoformat(r["business_date"]).date(), bool(r["completed"])) for r in funding_rows]
    previous_contributions: Decimal | None = None
    for row in stored:
        point_date = datetime.fromisoformat(row["captured_at"]).date()
        contributions = net_contributions([item for item in funding_items if item.business_date <= point_date])
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
def status():
    state = db.one("SELECT last_success,last_error,components FROM sync_state WHERE id=1") or {}
    stale_minutes = int(os.getenv("TIGER_STALE_MINUTES", "60"))
    last = state.get("last_success")
    stale = not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > stale_minutes * 60
    return {"lastSuccess": last, "lastError": state.get("last_error"), "stale": stale, "mode": "live",
            "components": json.loads(state.get("components") or "{}")}


@app.get("/api/export/{dataset}")
def export_csv(dataset: str, currency: str = Query("SGD", pattern="^(SGD|USD)$")):
    sources = {"positions": lambda: positions(currency), "funding": lambda: funding(currency), "history": lambda: history(currency)}
    if dataset not in sources:
        raise HTTPException(404, "Export must be positions, funding, or history")
    rows = sources[dataset]()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="tiger-{dataset}-{currency.lower()}.csv"'})
