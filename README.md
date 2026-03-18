# InvBot

An investment analysis and automation bot for stocks and ETFs. Provides real-time market data, technical analysis, buy/hold/sell recommendations, price alerts, automated trades via Alpaca, and durable stock-price journey tracking powered by Temporal.

---

## Tech Stack

| Layer | Technology |
|---|---|
| UI | [Streamlit](https://streamlit.io) |
| Charts | [Plotly](https://plotly.com/python/) |
| Market Data | [yfinance](https://github.com/ranaroussi/yfinance), [Finnhub](https://finnhub.io) |
| Database | SQLite (via Python `sqlite3`) |
| Workflow Engine | [Temporal](https://temporal.io) |
| Paper Trading | [Alpaca](https://alpaca.markets) |
| Language | Python 3.13 |

---

## Architecture

```
inv-bot/
├── app.py                    # Streamlit entry point — all pages and UI logic
├── config.py                 # Settings, API key loading from .env
├── data/
│   ├── fetcher.py            # yfinance + Finnhub data fetching (prices, fundamentals, news)
│   └── db.py                 # SQLite operations (watchlist, alerts, portfolio, journeys)
├── analysis/
│   ├── technicals.py         # SMA, EMA, RSI, MACD, Bollinger Bands
│   └── recommender.py        # Buy / Hold / Sell signal engine
├── ui/
│   └── components.py         # Plotly candlestick charts, technical overlays, cards
├── workflows/
│   ├── price_alert.py        # Temporal workflow: polls price until threshold is hit
│   ├── auto_invest.py        # Temporal workflow: validate → trade → log
│   ├── stock_journey.py      # Temporal workflow: periodic price snapshots over time
│   └── worker.py             # Temporal worker process (registers all workflows + activities)
├── requirements.txt
├── .env                      # API keys — never committed (gitignored)
└── .gitignore
```

### How the pieces connect

```
Browser
  └─► Streamlit (app.py)
        ├─► data/fetcher.py  ──► yfinance / Finnhub APIs
        ├─► data/db.py       ──► invbot.db (SQLite)
        ├─► analysis/        ──► technical indicators + recommendation score
        ├─► ui/components.py ──► Plotly charts
        └─► Temporal client  ──► localhost:7233
                                    └─► workflows/worker.py
                                          ├─► PriceAlertWorkflow
                                          ├─► AutoInvestWorkflow  ──► Alpaca API
                                          └─► StockJourneyWorkflow
```

### Database tables (SQLite)

| Table | Purpose |
|---|---|
| `watchlist` | Saved tickers |
| `search_history` | Recent searches |
| `recommendation_log` | Generated Buy/Hold/Sell signals |
| `price_alerts` | Active and triggered price alerts |
| `stock_journeys` | Journey metadata (symbol, status, duration) |
| `stock_journey_snapshots` | Per-interval price snapshots for each journey |
| `portfolio_positions` | Manually tracked positions |
| `investment_log` | Trade execution history (paper or live) |
| `visitor_log` | Page visit tracking |

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd inv-bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API keys

Copy the example below into a `.env` file at the project root. All keys are optional — the app degrades gracefully without them.

```
# Finnhub — real-time quotes and news (free: 60 calls/min)
FINNHUB_KEY=your_key_here

# Alpaca — paper or live trading
ALPACA_API_KEY=your_key_here
ALPACA_API_SECRET=your_secret_here
ALPACA_PAPER=true          # set to false for live trading (use with caution)

# Future / optional
ALPHA_VANTAGE_KEY=
POLYGON_KEY=
IEX_CLOUD_KEY=
```

> Get a free Finnhub key at https://finnhub.io
> Get a free Alpaca paper trading account at https://alpaca.markets

### 3. Install Temporal (required for Alerts, Trade, and Stock Journey pages)

```bash
brew install temporal        # macOS
# See https://docs.temporal.io for Linux/Windows
```

---

## Running the app

The app has two components. You can run just Streamlit if you only need market data, analysis, and portfolio features. The Temporal worker is only needed for Alerts, Trade execution, and Stock Journeys.

### Option A — Streamlit only (no Temporal)

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

### Option B — Full stack (all features)

Open **three terminal tabs**, all from the project root with the venv active:

**Tab 1 — Temporal dev server**
```bash
temporal server start-dev
```
Starts a local in-memory Temporal server at `localhost:7233`. The web UI is at http://localhost:8233.

**Tab 2 — Temporal worker**
```bash
python -m workflows.worker
```
Registers all workflows and activities with the Temporal server.

**Tab 3 — Streamlit app**
```bash
streamlit run app.py
```

---

## Pages

| Page | Description |
|---|---|
| **Market Overview** | Live S&P 500, NASDAQ, DOW, Russell 2000 indices + sentiment gauge |
| **Top Performers** | Best and worst movers across major stocks and ETFs for today / 1W / 1M |
| **Dividends** | Dividend yield leaders across ETFs and stocks |
| **IPO Calendar** | Upcoming and recent IPOs |
| **News** | Market-wide news and company-specific news via Finnhub |
| **Search & Analyze** | Ticker search, candlestick chart, technical indicators, Buy/Hold/Sell recommendation |
| **Watchlist** | Saved tickers with live prices and quick-add to watchlist |
| **Alerts** | Set price alerts (above/below a target). Triggers via Temporal `PriceAlertWorkflow` |
| **Portfolio** | Manually track positions with cost basis and P&L |
| **Trade** | Submit market or limit orders via Alpaca paper trading. Executed as `AutoInvestWorkflow` |
| **Stock Journey** | Track a stock's price every N minutes for up to 7 days via `StockJourneyWorkflow` |
| **Russell 2000** | Full constituent list of ~2,000 small-cap stocks from the IWM ETF |
| **Sector Heatmap** | Performance heatmap across S&P 500 sectors |
| **Visitor Log** | Internal page-visit history |

---

## Temporal workflows

Temporal makes the background tasks **durable** — if the worker crashes mid-execution, Temporal replays it automatically from the last checkpoint.

### PriceAlertWorkflow
Polls the current price every 5 minutes. Completes when the price crosses the target threshold, or expires after 30 days.

```
Start → loop every 5 min:
    fetch_current_price(symbol)
    if triggered:
        record_triggered_alert(alert_id, price)
        → DONE
    else:
        sleep(5 min)
```

### AutoInvestWorkflow
Executes a single trade order with full durability.

```
Start →
    validate_investment_input(order)
    execute_trade_placeholder(order)   # Alpaca paper/live or dry-run
    record_investment_log(order, result)
→ DONE
```

### StockJourneyWorkflow
Periodically snapshots a stock's price for a configurable duration. Supports graceful cancellation via a `cancel` signal.

```
Start → loop every N seconds (default 5 min):
    fetch_and_record_snapshot(journey_id, symbol)
    wait(interval) or cancel signal
→ close_journey(status)
→ DONE
```

---

## Monitoring trades in the Temporal UI

When `temporal server start-dev` is running, open http://localhost:8233.

- **Workflows tab** — every submitted alert, trade, or journey appears as a row with its status (`Running`, `Completed`, `Failed`)
- **Workflow detail** — click any workflow to see each activity step and its result. A successful paper trade shows: `SUCCESS: [PAPER] BUY 10 AAPL @ $182.34 | order_id=... status=filled`
- **Workers tab** — confirms the worker is connected and polling the `invbot-alerts` task queue

---

## Development

```bash
# Run with auto-reload
streamlit run app.py

# Check imports are clean (no sandbox violations)
python -c "from workflows.price_alert import PriceAlertWorkflow, fetch_current_price, record_triggered_alert; print('OK')"
python -c "from workflows.auto_invest import AutoInvestWorkflow; print('OK')"
python -c "from workflows.stock_journey import StockJourneyWorkflow; print('OK')"
```

### Adding a new data source

1. Add the fetch function to [data/fetcher.py](data/fetcher.py)
2. Import and call it in [app.py](app.py) on the relevant page
3. Store the API key in `.env` and load it via [config.py](config.py)

### Adding a new Temporal workflow

1. Create the workflow + activities in `workflows/your_workflow.py` — keep all I/O imports inside activity function bodies (not at module level)
2. Register it in [workflows/worker.py](workflows/worker.py) under `workflows=` and `activities=`
3. Start it from the Streamlit UI via `asyncio.run(client.start_workflow(...))`

---

## Roadmap

| Phase | Status |
|---|---|
| Phase 1 — Stock/ETF search, analysis, recommendations UI | Done |
| Phase 2 — Testing and validation of recommendations | In progress |
| Phase 3 — Integrate Alpha Vantage, Polygon for richer data | Planned |
| Phase 4 — Automated trading with full broker integration | Planned |
