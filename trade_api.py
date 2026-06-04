"""FastAPI trade API — parse natural-language commands and submit trades via Alpaca."""

import asyncio
import re
import sqlite3
from datetime import datetime

import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import DB_PATH, ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_PAPER
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


# ---------------------------------------------------------------------------
# Stock parser
# ---------------------------------------------------------------------------

def parse_stock_command(raw: str) -> InvestmentOrder:
    """Parse stock commands:
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

    order_type = "market"
    limit_price = 0.0
    lm = re.search(r"\blimit\s+(\d+(?:\.\d+)?)", rest, re.IGNORECASE)
    if lm:
        order_type = "limit"
        limit_price = float(lm.group(1))
        rest = (rest[: lm.start()] + rest[lm.end():]).strip()

    use_dollars = False
    quantity = 100.0

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


# ---------------------------------------------------------------------------
# Options parser + executor
# ---------------------------------------------------------------------------

def _build_occ_symbol(symbol: str, expiration: str, option_type: str, strike: float) -> str:
    """Build an OCC option symbol, e.g. MRVL260605C00300000."""
    exp = datetime.strptime(expiration, "%Y-%m-%d").strftime("%y%m%d")
    opt_char = "C" if option_type == "call" else "P"
    strike_int = int(round(strike * 1000))
    return f"{symbol}{exp}{opt_char}{strike_int:08d}"


def parse_options_command(raw: str) -> dict:
    """Parse options commands:
        buy MRVL call 300 1 contract
        buy DELL put $420 2 contracts
        sell AAPL call 200 strike 1 contract
        buy MRVL call 300             → default 1 contract, nearest expiry
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
    if len(tokens) < 3:
        raise ValueError("Options command needs symbol and call/put — e.g. 'buy MRVL call 300'")

    symbol = tokens[1].upper()

    if "call" in lower:
        option_type = "call"
    elif "put" in lower:
        option_type = "put"
    else:
        raise ValueError("Specify 'call' or 'put'")

    # everything after "call"/"put"
    split_on = "call" if option_type == "call" else "put"
    after = lower.split(split_on, 1)[-1]

    # strike — first number after call/put keyword
    strike_match = re.search(r"\$?(\d+(?:\.\d+)?)", after)
    if not strike_match:
        raise ValueError("Strike price required — e.g. 'buy MRVL call 300'")
    strike = float(strike_match.group(1))

    # contracts — look for "<n> contract(s)" or a second standalone number
    qty_match = re.search(r"(\d+)\s*contracts?", after, re.IGNORECASE)
    if qty_match:
        quantity = int(qty_match.group(1))
    else:
        nums = re.findall(r"\d+(?:\.\d+)?", after)
        quantity = int(float(nums[1])) if len(nums) >= 2 else 1

    # nearest expiry from yfinance
    ticker = yf.Ticker(symbol)
    exps = ticker.options
    if not exps:
        raise ValueError(f"No options data found for {symbol}")
    expiration = exps[0]

    occ_symbol = _build_occ_symbol(symbol, expiration, option_type, strike)

    return {
        "symbol": symbol,
        "occ_symbol": occ_symbol,
        "action": action,
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "quantity": quantity,
    }


async def execute_options_trade(parsed: dict) -> dict:
    """Submit an options order directly to Alpaca (market order, DAY)."""
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        msg = (
            f"[DRY RUN — no Alpaca keys] Would {parsed['action'].upper()} "
            f"{parsed['quantity']} contract(s) of {parsed['occ_symbol']}"
        )
        return {"status": "dry_run", "result": msg}

    def _submit():
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import OptionsOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

        client = TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, paper=ALPACA_PAPER)
        side = OrderSide.BUY if parsed["action"] == "buy" else OrderSide.SELL

        req = OptionsOrderRequest(
            symbol=parsed["occ_symbol"],
            qty=parsed["quantity"],
            side=side,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        submitted = client.submit_order(req)
        mode = "PAPER" if ALPACA_PAPER else "LIVE"
        return {
            "status": "submitted",
            "result": (
                f"[{mode}] {parsed['action'].upper()} {parsed['quantity']} contract(s) "
                f"{parsed['occ_symbol']} | order_id={submitted.id} status={submitted.status}"
            ),
        }

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _submit)
    except Exception as e:
        return {"status": "error", "result": f"Alpaca error: {e}"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "supports": ["stocks", "options"],
    }


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
    lower = body.command.lower()
    is_options = "call" in lower or "put" in lower

    if is_options:
        try:
            parsed = parse_options_command(body.command)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        result = await execute_options_trade(parsed)
        return {
            "type": "option",
            "parsed": parsed,
            "status": result["status"],
            "result": result["result"],
        }

    # --- stock path ---
    try:
        order = parse_stock_command(body.command)
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
        return {"type": "stock", "status": "submitted", "parsed": parsed, "result": result}

    except Exception as e:
        qty_str = f"${order.quantity}" if order.use_dollars else f"{order.quantity} shares"
        limit_str = f" @ limit ${order.limit_price}" if order.order_type == "limit" else ""
        dry_run_msg = (
            f"[DRY RUN — Temporal unavailable ({type(e).__name__})] "
            f"Would {order.action.upper()} {qty_str} of {order.symbol} "
            f"({order.order_type}{limit_str})"
        )
        return {"type": "stock", "status": "dry_run", "parsed": parsed, "result": dry_run_msg}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("trade_api:app", host="0.0.0.0", port=8765, reload=True)
