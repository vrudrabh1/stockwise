"""FastAPI trade API — parse natural-language commands and submit to AutoInvestWorkflow."""

import re
import sqlite3
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import DB_PATH
from workflows.auto_invest import AutoInvestWorkflow, InvestmentOrder
from workflows.price_alert import TASK_QUEUE

TEMPORAL_HOST = "localhost:7233"

app = FastAPI(title="Stockwise Trade API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TradeCommand(BaseModel):
    command: str


def parse_command(raw: str) -> InvestmentOrder:
    """Parse a natural-language trade command into an InvestmentOrder.

    Supported forms:
        buy AAPL 10 shares
        sell TSLA 5
        buy MSFT $500
        buy NVDA limit 900
        sell AAPL 3 shares limit 180
        buy SPY                     → default $100 notional market order
    """
    text = raw.strip()
    lower = text.lower()

    if lower.startswith("buy"):
        action = "buy"
    elif lower.startswith("sell"):
        action = "sell"
    else:
        raise ValueError("Command must start with 'buy' or 'sell'")

    tokens = text.split()
    if len(tokens) < 2:
        raise ValueError("Symbol required — e.g. 'buy AAPL 10 shares'")

    symbol = tokens[1].upper()
    rest = " ".join(tokens[2:])

    # limit price
    order_type = "market"
    limit_price = 0.0
    lm = re.search(r"\blimit\s+(\d+(?:\.\d+)?)", rest, re.IGNORECASE)
    if lm:
        order_type = "limit"
        limit_price = float(lm.group(1))
        rest = (rest[: lm.start()] + rest[lm.end() :]).strip()

    # quantity — dollar amount takes priority over share count
    use_dollars = False
    quantity = 100.0  # default: $100 notional

    dollar_match = re.search(r"\$(\d+(?:\.\d+)?)", rest)
    share_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:shares?)?", rest, re.IGNORECASE)

    if dollar_match:
        quantity = float(dollar_match.group(1))
        use_dollars = True
    elif share_match:
        quantity = float(share_match.group(1))
        use_dollars = False
    else:
        use_dollars = True
        quantity = 100.0

    return InvestmentOrder(
        symbol=symbol,
        action=action,
        quantity=quantity,
        use_dollars=use_dollars,
        order_type=order_type,
        limit_price=limit_price,
        reason=f"Claude chat: {raw}",
    )


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/orders")
def get_orders(limit: int = 20):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM investment_log ORDER BY executed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return {"error": str(e), "orders": []}


@app.post("/trade")
async def submit_trade(body: TradeCommand):
    try:
        order = parse_command(body.command)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    parsed = {
        "symbol": order.symbol,
        "action": order.action,
        "quantity": order.quantity,
        "use_dollars": order.use_dollars,
        "order_type": order.order_type,
        "limit_price": order.limit_price,
    }

    try:
        from temporalio.client import Client

        client = await Client.connect(TEMPORAL_HOST)
        workflow_id = f"trade-{order.symbol}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        result = await client.execute_workflow(
            AutoInvestWorkflow.run,
            order,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        return {"status": "submitted", "parsed": parsed, "result": result}

    except Exception as e:
        qty_str = (
            f"${order.quantity}" if order.use_dollars else f"{order.quantity} shares"
        )
        limit_str = f" @ limit ${order.limit_price}" if order.order_type == "limit" else ""
        dry_run_msg = (
            f"[DRY RUN — Temporal unavailable ({type(e).__name__})] "
            f"Would {order.action.upper()} {qty_str} of {order.symbol} "
            f"({order.order_type}{limit_str})"
        )
        return {"status": "dry_run", "parsed": parsed, "result": dry_run_msg}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("trade_api:app", host="0.0.0.0", port=8765, reload=True)
