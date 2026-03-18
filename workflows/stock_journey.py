"""Temporal workflow for tracking a stock's price journey over time.

Each run of StockJourneyWorkflow durably records price snapshots at a
configurable interval for up to `max_duration_hours`. The full history
is persisted in SQLite and can be visualised in the Streamlit UI.

Usage (from Streamlit):
    Start via _start_journey_workflow() helper in app.py.

Usage (manual):
    python -c "
    import asyncio
    from temporalio.client import Client
    from workflows.stock_journey import StockJourneyWorkflow, JourneyInput, TASK_QUEUE

    async def main():
        client = await Client.connect('localhost:7233')
        await client.start_workflow(
            StockJourneyWorkflow.run,
            JourneyInput(journey_id=1, symbol='AAPL', interval_seconds=300, max_duration_hours=24),
            id='journey-1',
            task_queue=TASK_QUEUE,
        )

    asyncio.run(main())
    "
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from workflows.price_alert import TASK_QUEUE  # share the same task queue


@dataclass
class JourneyInput:
    journey_id: int
    symbol: str
    interval_seconds: int       # how often to sample (e.g. 300 = 5 min)
    max_duration_hours: float   # how long to run (e.g. 24.0)


# --- Activities ---

@activity.defn
async def fetch_and_record_snapshot(journey_id: int, symbol: str) -> float:
    """Fetch current price and persist it as a journey snapshot. Returns the price."""
    import yfinance as yf
    from data.db import record_journey_snapshot
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

    price = await loop.run_in_executor(None, _fetch)
    record_journey_snapshot(journey_id, price)
    return price


@activity.defn
async def close_journey(journey_id: int, status: str) -> None:
    """Mark the journey as completed or cancelled in SQLite."""
    from data.db import finalize_journey
    finalize_journey(journey_id, status)


# --- Workflow ---

@workflow.defn
class StockJourneyWorkflow:
    """Durable workflow that periodically snapshots a stock price.

    - Polls every `interval_seconds`
    - Runs until `max_duration_hours` elapses or a cancel signal is received
    - All snapshots are persisted in `stock_journey_snapshots` via SQLite
    - Supports graceful cancellation via the `cancel` signal

    On completion the journey is marked 'completed'; on signal 'cancelled'.
    """

    def __init__(self):
        self._cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        """Signal the workflow to stop early."""
        self._cancel_requested = True

    @workflow.run
    async def run(self, inp: JourneyInput) -> str:
        deadline = workflow.now() + timedelta(hours=inp.max_duration_hours)
        retry = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)
        snapshots = 0
        last_price = 0.0

        while workflow.now() < deadline and not self._cancel_requested:
            try:
                last_price = await workflow.execute_activity(
                    fetch_and_record_snapshot,
                    args=[inp.journey_id, inp.symbol],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry,
                )
                snapshots += 1
            except Exception:
                pass  # skip bad fetch, retry next cycle

            # Sleep until next sample — wakes early if cancel signal arrives
            await workflow.wait_condition(
                lambda: self._cancel_requested,
                timeout=timedelta(seconds=inp.interval_seconds),
            )

        final_status = "cancelled" if self._cancel_requested else "completed"

        await workflow.execute_activity(
            close_journey,
            args=[inp.journey_id, final_status],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry,
        )

        return (
            f"Journey {inp.journey_id} ({inp.symbol}) {final_status}: "
            f"{snapshots} snapshots, last price ${last_price:.2f}"
        )
