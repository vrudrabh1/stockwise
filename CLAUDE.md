# inv-bot

## Project Overview

An investment bot that provides stock and ETF search, analysis, and recommendations. Future phase will include automated trading capabilities.

## Tech Stack

- **Language:** Python 3.13
- **UI:** Streamlit
- **Charts:** Plotly
- **Data:** yfinance, pandas

## APIs & Data Sources

### Primary (No API Key Required)
- **yfinance** — Stock/ETF prices, historical data, fundamentals, financials, dividends, splits

### Future / Optional (API Key Required)
- **Alpha Vantage** — Technical indicators, forex, crypto (free: 25 req/day)
- **Finnhub** — Real-time quotes, market news, sentiment analysis (free: 60 calls/min)
- **Polygon.io** — Options data, tick-level data, aggregates (free: 5 calls/min)
- **IEX Cloud** — Comprehensive market data, company info (pay-as-you-go)

### API Key Configuration
Store API keys in a `.env` file (never commit to git):
```
ALPHA_VANTAGE_KEY=your_key_here
FINNHUB_KEY=your_key_here
POLYGON_KEY=your_key_here
IEX_CLOUD_KEY=your_key_here
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Development

```bash
# Run the app
streamlit run app.py
```

## Architecture

```
inv-bot/
├── app.py                  # Streamlit entry point
├── config.py               # Settings, API keys, constants
├── data/
│   ├── fetcher.py          # yfinance data fetching
│   └── db.py               # SQLite operations (watchlist, search history)
├── analysis/
│   ├── technicals.py       # Technical indicators (RSI, MACD, SMA, Bollinger)
│   └── recommender.py      # Buy/Hold/Sell recommendation engine
├── ui/
│   └── components.py       # Plotly charts and Streamlit display components
├── requirements.txt
├── .env                    # API keys (gitignored)
└── .gitignore
```

### Key Modules
- **data/fetcher.py** — Wraps yfinance for ticker search, price history, fundamentals, dividends
- **data/db.py** — SQLite for watchlists, search history, recommendation logs
- **analysis/technicals.py** — SMA, EMA, RSI, MACD, Bollinger Bands
- **analysis/recommender.py** — Scores technicals + fundamentals → Buy/Hold/Sell signal
- **ui/components.py** — Candlestick charts, technical overlay charts, recommendation cards

### Database (SQLite)
- `watchlist` — saved tickers
- `search_history` — recent searches
- `recommendation_log` — generated recommendations

## Roadmap

1. **Phase 1:** Stock/ETF search and recommendations UI (yfinance)
2. **Phase 2:** Testing and validation of recommendations
3. **Phase 3:** Integrate additional data APIs for richer analysis
4. **Phase 4:** Automated trading integration
