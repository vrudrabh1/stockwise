"""Temporal worker — runs all InvBot workflows and activities.

Start this alongside the Streamlit app:
    python workflows/worker.py

Requires a local Temporal server:
    brew install temporal
    temporal server start-dev
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from workflows.price_alert import (
    TASK_QUEUE,
    PriceAlertWorkflow,
    fetch_current_price,
    record_triggered_alert,
)
from workflows.auto_invest import (
    AutoInvestWorkflow,
    validate_investment_input,
    execute_trade_placeholder,
    record_investment_log,
)
from workflows.stock_journey import (
    StockJourneyWorkflow,
    fetch_and_record_snapshot,
    close_journey,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Connecting to Temporal server at localhost:7233 ...")
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PriceAlertWorkflow, AutoInvestWorkflow, StockJourneyWorkflow],
        activities=[
            fetch_current_price,
            record_triggered_alert,
            validate_investment_input,
            execute_trade_placeholder,
            record_investment_log,
            fetch_and_record_snapshot,
            close_journey,
        ],
    )

    log.info(f"Worker started on task queue '{TASK_QUEUE}'. Ctrl+C to stop.")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
