"""Temporal workflow for automated investment execution (Phase 4 placeholder).

This workflow defines the structure for automated trading. The actual
broker integration (e.g., Alpaca, Interactive Brokers) is left as a
placeholder until Phase 4.

To integrate a real broker, replace `execute_trade_placeholder` with
your broker's SDK calls (e.g., alpaca-trade-api, ib_insync).
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from workflows.price_alert import TASK_QUEUE


@dataclass
class InvestmentOrder:
    symbol: str
    action: str          # "buy" or "sell"
    quantity: float      # number of shares (or dollar amount if use_dollars=True)
    use_dollars: bool    # if True, quantity is a dollar amount
    order_type: str      # "market" or "limit"
    limit_price: float   # only used when order_type == "limit"
    reason: str          # human-readable reason for the trade (e.g., "Price alert triggered")


@dataclass
class InvestmentResult:
    success: bool
    order_id: str
    filled_price: float
    filled_quantity: float
    message: str


# --- Activities ---

@activity.defn
async def validate_investment_input(order: InvestmentOrder) -> str:
    """Validate the order before execution. Returns 'ok' or an error message."""
    if order.action not in ("buy", "sell"):
        return f"Invalid action '{order.action}'. Must be 'buy' or 'sell'."
    if order.quantity <= 0:
        return "Quantity must be positive."
    if order.order_type not in ("market", "limit"):
        return f"Invalid order type '{order.order_type}'."
    if order.order_type == "limit" and order.limit_price <= 0:
        return "Limit price must be positive for limit orders."
    if not order.symbol:
        return "Symbol is required."
    return "ok"


@activity.defn
async def execute_trade_placeholder(order: InvestmentOrder) -> InvestmentResult:
    """Execute a trade via Alpaca paper trading (falls back to placeholder if keys not set)."""
    from config import ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_PAPER

    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        # No keys — return a dry-run placeholder result
        await asyncio.sleep(0.2)
        return InvestmentResult(
            success=True,
            order_id=f"DRYRUN-{order.symbol}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            filled_price=0.0,
            filled_quantity=order.quantity,
            message=f"[DRY RUN] {order.action.upper()} {order.quantity} {order.symbol} "
                    f"({order.order_type}) — add ALPACA_API_KEY to .env to enable paper trading",
        )

    def _submit():
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, paper=ALPACA_PAPER)
        side = OrderSide.BUY if order.action == "buy" else OrderSide.SELL

        if order.order_type == "limit":
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=order.limit_price,
            )
        else:
            if order.use_dollars:
                req = MarketOrderRequest(
                    symbol=order.symbol,
                    notional=round(order.quantity, 2),
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            else:
                req = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )

        submitted = client.submit_order(req)
        filled_price = float(submitted.filled_avg_price or 0)
        filled_qty = float(submitted.filled_qty or order.quantity)
        mode = "PAPER" if ALPACA_PAPER else "LIVE"
        return InvestmentResult(
            success=True,
            order_id=str(submitted.id),
            filled_price=filled_price,
            filled_quantity=filled_qty,
            message=f"[{mode}] {order.action.upper()} {filled_qty} {order.symbol} "
                    f"@ {'$' + str(filled_price) if filled_price else 'market'} "
                    f"| order_id={submitted.id} status={submitted.status}",
        )

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _submit)
    except Exception as e:
        return InvestmentResult(
            success=False,
            order_id="",
            filled_price=0.0,
            filled_quantity=0.0,
            message=f"Alpaca error: {e}",
        )


@activity.defn
async def record_investment_log(order: InvestmentOrder, result: InvestmentResult) -> None:
    """Persist the investment attempt and result to SQLite."""
    import sqlite3
    from config import DB_PATH

    def _write():
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS investment_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                reason TEXT,
                success INTEGER NOT NULL,
                order_id TEXT,
                filled_price REAL,
                filled_quantity REAL,
                message TEXT,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO investment_log
                (symbol, action, quantity, order_type, limit_price, reason,
                 success, order_id, filled_price, filled_quantity, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.symbol, order.action, order.quantity, order.order_type,
            order.limit_price, order.reason,
            int(result.success), result.order_id, result.filled_price,
            result.filled_quantity, result.message,
        ))
        conn.commit()
        conn.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write)


# --- Workflow ---

@workflow.defn
class AutoInvestWorkflow:
    """Durable workflow for executing an automated investment order.

    Steps:
    1. Validate the order
    2. Execute the trade (placeholder in Phase 4)
    3. Record the result to the investment log
    4. Return a summary

    The workflow is durable: if the worker crashes mid-execution,
    Temporal replays it from the last checkpoint automatically.
    """

    @workflow.run
    async def run(self, order: InvestmentOrder) -> str:
        retry = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)

        # Step 1: Validate
        validation = await workflow.execute_activity(
            validate_investment_input,
            order,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry,
        )
        if validation != "ok":
            return f"REJECTED: {validation}"

        # Step 2: Execute
        result: InvestmentResult = await workflow.execute_activity(
            execute_trade_placeholder,
            order,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry,
        )

        # Step 3: Log
        await workflow.execute_activity(
            record_investment_log,
            args=[order, result],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry,
        )

        status = "SUCCESS" if result.success else "FAILED"
        return f"{status}: {result.message}"
