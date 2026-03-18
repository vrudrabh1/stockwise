"""Temporal workflow and activities for price alert monitoring."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


TASK_QUEUE = "invbot-alerts"

# How often to check the price (5 minutes during market hours)
CHECK_INTERVAL_SECONDS = 300

# Max workflow duration before auto-expiry (30 days)
MAX_DURATION_DAYS = 30


@dataclass
class AlertInput:
    alert_id: int
    symbol: str
    target_price: float
    direction: str  # "above" or "below"


@dataclass
class PriceCheckResult:
    current_price: float
    triggered: bool


# --- Activities ---

@activity.defn
async def fetch_current_price(symbol: str) -> float:
    """Fetch the latest price for a symbol via yfinance."""
    import yfinance as yf
    loop = asyncio.get_event_loop()

    def _fetch():
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if hist.empty:
            info = ticker.info
            return float(
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
                or 0
            )
        return float(hist["Close"].iloc[-1])

    return await loop.run_in_executor(None, _fetch)


@activity.defn
async def record_triggered_alert(alert_id: int, triggered_price: float) -> None:
    """Write alert trigger to the SQLite database."""
    from data.db import trigger_price_alert
    trigger_price_alert(alert_id, triggered_price)


# --- Workflow ---

@workflow.defn
class PriceAlertWorkflow:
    """Monitors a stock price and triggers when it crosses a threshold.

    The workflow polls the price every CHECK_INTERVAL_SECONDS and completes
    (successfully) when the alert fires, or after MAX_DURATION_DAYS.
    """

    @workflow.run
    async def run(self, inp: AlertInput) -> str:
        deadline = workflow.now() + timedelta(days=MAX_DURATION_DAYS)
        retry = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)

        while workflow.now() < deadline:
            try:
                price = await workflow.execute_activity(
                    fetch_current_price,
                    inp.symbol,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry,
                )
            except Exception:
                # If price fetch fails, wait and retry next cycle
                await workflow.sleep(CHECK_INTERVAL_SECONDS)
                continue

            triggered = (
                (inp.direction == "above" and price >= inp.target_price)
                or (inp.direction == "below" and price <= inp.target_price)
            )

            if triggered:
                await workflow.execute_activity(
                    record_triggered_alert,
                    args=[inp.alert_id, price],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry,
                )
                return (
                    f"TRIGGERED: {inp.symbol} hit ${price:.2f} "
                    f"(target: {inp.direction} ${inp.target_price:.2f})"
                )

            # Sleep until next check
            await workflow.sleep(CHECK_INTERVAL_SECONDS)

        return f"EXPIRED: {inp.symbol} alert expired after {MAX_DURATION_DAYS} days"
