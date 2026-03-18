"""Stock and ETF data fetching using yfinance with Streamlit caching."""

from datetime import datetime, timedelta

import finnhub
import streamlit as st
import yfinance as yf
import pandas as pd

from config import FINNHUB_KEY


@st.cache_data
def get_ticker_info(symbol: str) -> dict | None:
    """Fetch basic info for a stock/ETF ticker."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("trailingPegRatio") is None and info.get("shortName") is None:
            return None
        return info
    except Exception:
        return None


@st.cache_data
def get_price_history(symbol: str, period_days: int = 365) -> pd.DataFrame:
    """Fetch historical price data for a ticker."""
    ticker = yf.Ticker(symbol)
    end = datetime.now()
    start = end - timedelta(days=period_days)
    df = ticker.history(start=start, end=end)
    if df.empty:
        return df
    df = df.reset_index()
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]]


@st.cache_data
def get_fundamentals(symbol: str) -> dict:
    """Extract key fundamental metrics from ticker info."""
    info = get_ticker_info(symbol)
    if not info:
        return {}

    keys = [
        "shortName", "symbol", "quoteType", "sector", "industry",
        "marketCap", "enterpriseValue",
        "trailingPE", "forwardPE", "pegRatio",
        "priceToBook", "priceToSalesTrailing12Months",
        "revenueGrowth", "earningsGrowth", "profitMargins",
        "returnOnEquity", "returnOnAssets",
        "debtToEquity", "currentRatio",
        "dividendYield", "trailingAnnualDividendYield",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "fiftyDayAverage", "twoHundredDayAverage",
        "beta", "totalRevenue", "totalDebt", "freeCashflow",
        "currentPrice", "targetMeanPrice", "recommendationKey",
    ]
    return {k: info.get(k) for k in keys if info.get(k) is not None}


@st.cache_data
def search_tickers(query: str) -> list[dict]:
    """Search for tickers matching a query string."""
    try:
        results = yf.Search(query)
        quotes = results.quotes if hasattr(results, "quotes") else []
        return [
            {
                "symbol": q.get("symbol", ""),
                "name": q.get("shortname") or q.get("longname", ""),
                "type": q.get("quoteType", ""),
                "exchange": q.get("exchange", ""),
            }
            for q in quotes
            if q.get("symbol")
        ]
    except Exception:
        return []


# Major stocks and ETFs to track for top performers
TOP_TICKERS = [
    # Major indices ETFs
    "SPY", "QQQ", "DIA", "IWM",
    # Mega caps
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    # Tech
    "AMD", "INTC", "CRM", "ADBE", "NFLX", "AVGO", "ORCL", "CSCO",
    # Finance
    "JPM", "BAC", "GS", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "PFE", "LLY", "ABBV",
    # Energy
    "XOM", "CVX", "COP",
    # Consumer
    "WMT", "KO", "PEP", "MCD", "NKE", "DIS",
    # Popular ETFs
    "VTI", "VOO", "ARKK", "XLF", "XLE", "XLK", "SOXX",
]


@st.cache_data(ttl=300)  # refresh every 5 minutes
def get_top_performers(period: str = "1d", count: int = 10) -> pd.DataFrame:
    """Get top performing stocks for a given period."""
    tickers_str = " ".join(TOP_TICKERS)
    # "1d" yields only one intraday row; fetch 2 daily bars instead so we can
    # compute open-of-day → latest close change.
    fetch_period = "5d" if period == "1d" else period
    fetch_interval = "1d" if period == "1d" else "1d"
    data = yf.download(tickers_str, period=fetch_period, interval=fetch_interval,
                       group_by="ticker", progress=False)

    results = []
    for symbol in TOP_TICKERS:
        try:
            if len(TOP_TICKERS) == 1:
                ticker_data = data
            else:
                ticker_data = data[symbol]

            if ticker_data.empty or len(ticker_data) < 2:
                continue

            # For 1d view, only compare the last two daily bars (prev close → today)
            if period == "1d":
                ticker_data = ticker_data.iloc[-2:]

            start_price = ticker_data["Close"].iloc[0]
            end_price = ticker_data["Close"].iloc[-1]

            if pd.isna(start_price) or pd.isna(end_price) or start_price == 0:
                continue

            change_pct = ((end_price - start_price) / start_price) * 100

            results.append({
                "Symbol": symbol,
                "Price": round(float(end_price), 2),
                "Change %": round(float(change_pct), 2),
            })
        except (KeyError, IndexError):
            continue

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("Change %", ascending=False).reset_index(drop=True)
    return df


# Top dividend stocks and ETFs to track
DIVIDEND_TICKERS = {
    "ETFs": [
        "SCHD", "VYM", "HDV", "JEPI", "JEPQ", "DGRO", "DVY", "NOBL",
        "SPYD", "VIG", "VYMI", "IDV",
    ],
    "Stocks": [
        "JNJ", "KO", "PEP", "PG", "ABT", "MMM", "T", "VZ",
        "XOM", "CVX", "MO", "PM", "O", "ABBV", "IBM", "ED",
        "SO", "DUK", "CAT", "EMR", "SWK", "TROW",
    ],
}


@st.cache_data
def get_dividend_leaders() -> pd.DataFrame:
    """Fetch dividend yield and info for top dividend stocks and ETFs."""
    all_tickers = []
    ticker_types = {}
    for category, symbols in DIVIDEND_TICKERS.items():
        for s in symbols:
            all_tickers.append(s)
            ticker_types[s] = category.rstrip("s")  # "ETF" or "Stock"

    results = []
    for symbol in all_tickers:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if not info:
                continue

            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
                or 0
            )

            # Try to compute yield from dividend rate / price
            div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0
            if div_rate and price and price > 0:
                yield_pct = (float(div_rate) / float(price)) * 100
                annual_dividend = float(div_rate)
            else:
                # For ETFs: dividendYield is already a percentage (e.g. 3.3 = 3.3%)
                dv = info.get("dividendYield")
                if dv and dv > 0:
                    yield_pct = float(dv) if dv > 0.5 else float(dv) * 100
                    annual_dividend = (yield_pct / 100) * float(price) if price else 0
                else:
                    # Last fallback: trailingAnnualDividendYield (decimal 0-1)
                    trailing = info.get("trailingAnnualDividendYield")
                    if trailing and trailing > 0:
                        yield_pct = float(trailing) * 100
                        annual_dividend = trailing * float(price) if price else 0
                    else:
                        continue

            payout = info.get("payoutRatio")
            if payout is not None:
                payout_pct = float(payout) * 100 if payout <= 1 else float(payout)
            else:
                payout_pct = None

            five_yr = info.get("fiveYearAvgDividendYield")

            results.append({
                "Symbol": symbol,
                "Name": info.get("shortName", symbol),
                "Type": ticker_types.get(symbol, ""),
                "Price": round(float(price), 2) if price else 0,
                "Yield %": round(yield_pct, 2),
                "Annual Div": round(float(annual_dividend), 2) if annual_dividend else 0,
                "Payout Ratio": round(payout_pct, 1) if payout_pct is not None else None,
                "5Y Avg Yield": round(float(five_yr), 2) if five_yr else None,
            })
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("Yield %", ascending=False).reset_index(drop=True)
    return df


@st.cache_data
def get_market_indices() -> dict:
    """Fetch current values and daily change % for major market indices."""
    indices = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "Russell 2000": "^RUT",
    }
    results = {}
    for name, symbol in indices.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty or len(hist) < 2:
                continue
            current = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            change_pct = ((current - prev) / prev) * 100
            results[name] = {
                "symbol": symbol,
                "value": round(current, 2),
                "change_pct": round(change_pct, 2),
            }
        except Exception:
            continue
    return results


@st.cache_data
def get_market_sentiment() -> dict:
    """Compute a simple Fear & Greed style sentiment score (0-100).

    Components:
    - VIX level (lower = greedier): 0-100 score
    - Market breadth (% of TOP_TICKERS above 50-day SMA): 0-100
    - Momentum (S&P 500 vs its 200-day SMA): 0-100
    Returns dict with overall score, label, and component scores.
    """
    import numpy as np

    components = {}

    # 1. VIX component
    try:
        vix = yf.Ticker("^VIX")
        vix_hist = vix.history(period="5d")
        if not vix_hist.empty:
            vix_val = float(vix_hist["Close"].iloc[-1])
            # VIX 10 = extreme greed (100), VIX 40+ = extreme fear (0)
            vix_score = max(0, min(100, (40 - vix_val) / 30 * 100))
            components["vix"] = round(vix_score, 1)
        else:
            components["vix"] = 50
    except Exception:
        components["vix"] = 50

    # 2. Market breadth — % of TOP_TICKERS above their 50-day SMA
    try:
        above_sma = 0
        total = 0
        tickers_str = " ".join(TOP_TICKERS)
        data = yf.download(tickers_str, period="3mo", group_by="ticker", progress=False)
        for sym in TOP_TICKERS:
            try:
                ticker_data = data[sym] if len(TOP_TICKERS) > 1 else data
                closes = ticker_data["Close"].dropna()
                if len(closes) >= 50:
                    sma50 = closes.rolling(50).mean().iloc[-1]
                    current = closes.iloc[-1]
                    total += 1
                    if current > sma50:
                        above_sma += 1
            except Exception:
                continue
        breadth_score = (above_sma / total * 100) if total > 0 else 50
        components["breadth"] = round(breadth_score, 1)
    except Exception:
        components["breadth"] = 50

    # 3. S&P 500 momentum vs 200-day SMA
    try:
        sp500 = yf.Ticker("^GSPC")
        sp_hist = sp500.history(period="1y")
        if len(sp_hist) >= 200:
            current = float(sp_hist["Close"].iloc[-1])
            sma200 = float(sp_hist["Close"].rolling(200).mean().iloc[-1])
            # % above/below 200-day SMA, scaled to 0-100
            pct_diff = ((current - sma200) / sma200) * 100
            # -10% = 0, +10% = 100
            momentum_score = max(0, min(100, (pct_diff + 10) / 20 * 100))
            components["momentum"] = round(momentum_score, 1)
        else:
            components["momentum"] = 50
    except Exception:
        components["momentum"] = 50

    # Weighted average
    overall = (
        components["vix"] * 0.4
        + components["breadth"] * 0.35
        + components["momentum"] * 0.25
    )
    overall = round(max(0, min(100, overall)), 1)

    if overall >= 80:
        label = "Extreme Greed"
    elif overall >= 60:
        label = "Greed"
    elif overall >= 40:
        label = "Neutral"
    elif overall >= 20:
        label = "Fear"
    else:
        label = "Extreme Fear"

    return {
        "score": overall,
        "label": label,
        "components": components,
    }


@st.cache_data
def get_sp500_sparkline() -> pd.DataFrame:
    """Fetch 5-day S&P 500 price data for a sparkline chart."""
    try:
        ticker = yf.Ticker("^GSPC")
        hist = ticker.history(period="5d", interval="1h")
        if hist.empty:
            return pd.DataFrame()
        df = hist.reset_index()
        return df[["Datetime", "Close"]] if "Datetime" in df.columns else df[["Date", "Close"]]
    except Exception:
        return pd.DataFrame()


@st.cache_data
def get_market_news(category: str = "general") -> list[dict]:
    """Fetch general market news from Finnhub."""
    if not FINNHUB_KEY:
        return []

    try:
        client = finnhub.Client(api_key=FINNHUB_KEY)
        news = client.general_news(category, min_id=0)
        results = []
        for item in news[:20]:
            results.append({
                "title": item.get("headline", ""),
                "headline": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "datetime": item.get("datetime", 0),
                "image": item.get("image", ""),
                "summary": item.get("summary", ""),
            })
        return results
    except Exception:
        return []


@st.cache_data
def get_company_news(symbol: str, days: int = 7) -> list[dict]:
    """Fetch company-specific news from Finnhub."""
    if not FINNHUB_KEY:
        return []

    try:
        client = finnhub.Client(api_key=FINNHUB_KEY)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        news = client.company_news(symbol, _from=start, to=end)
        results = []
        for item in news[:15]:
            results.append({
                "title": item.get("headline", ""),
                "headline": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "datetime": item.get("datetime", 0),
                "image": item.get("image", ""),
                "summary": item.get("summary", ""),
            })
        return results
    except Exception:
        return []


@st.cache_data
def get_dividends(symbol: str) -> pd.DataFrame:
    """Fetch dividend history for a ticker."""
    ticker = yf.Ticker(symbol)
    dividends = ticker.dividends
    if dividends.empty:
        return pd.DataFrame()
    df = dividends.reset_index()
    df.columns = ["Date", "Dividend"]
    return df


@st.cache_data
def get_upcoming_ipos() -> pd.DataFrame:
    """Fetch upcoming IPOs from Finnhub.

    Returns DataFrame with columns: Date, Name, Symbol, Exchange,
    Price Range, Shares, Status.
    """
    if not FINNHUB_KEY:
        return pd.DataFrame()

    try:
        client = finnhub.Client(api_key=FINNHUB_KEY)
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        data = client.ipo_calendar(_from=today, to=future)

        ipos = data.get("ipoCalendar", [])
        if not ipos:
            return pd.DataFrame()

        results = []
        for ipo in ipos:
            price_low = ipo.get("priceRangeLow", 0) or 0
            price_high = ipo.get("priceRangeHigh", 0) or 0
            if price_low and price_high:
                price_range = f"${price_low:.2f} - ${price_high:.2f}"
            elif price_low:
                price_range = f"${price_low:.2f}"
            else:
                price_range = "TBD"

            shares = ipo.get("numberOfShares", 0) or 0
            if shares >= 1e6:
                shares_str = f"{shares/1e6:.1f}M"
            elif shares > 0:
                shares_str = f"{shares:,.0f}"
            else:
                shares_str = "TBD"

            results.append({
                "Date": ipo.get("date", ""),
                "Name": ipo.get("name", "N/A"),
                "Symbol": ipo.get("symbol", "N/A"),
                "Exchange": ipo.get("exchange", ""),
                "Price Range": price_range,
                "Shares": shares_str,
                "Status": ipo.get("status", ""),
                "Total Value": f"${ipo.get('totalSharesValue', 0)/1e6:.1f}M" if ipo.get("totalSharesValue") else "TBD",
            })

        df = pd.DataFrame(results)
        df = df.sort_values("Date").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


@st.cache_data
def get_sector_performance(period: str = "1d") -> pd.DataFrame:
    """Fetch sector ETF performance for a given period.

    Returns DataFrame with columns: Sector, ETF, Price, Change %.
    """
    symbols = list(SECTOR_ETFS.values())
    tickers_str = " ".join(symbols)
    data = yf.download(tickers_str, period=period, group_by="ticker", progress=False)

    results = []
    for sector, etf in SECTOR_ETFS.items():
        try:
            ticker_data = data[etf]
            if ticker_data.empty or len(ticker_data) < 2:
                continue

            start_price = ticker_data["Close"].iloc[0]
            end_price = ticker_data["Close"].iloc[-1]

            if pd.isna(start_price) or pd.isna(end_price) or start_price == 0:
                continue

            change_pct = ((end_price - start_price) / start_price) * 100

            results.append({
                "Sector": sector,
                "ETF": etf,
                "Price": round(float(end_price), 2),
                "Change %": round(float(change_pct), 2),
            })
        except (KeyError, IndexError):
            continue

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


@st.cache_data
def get_recent_ipos() -> pd.DataFrame:
    """Fetch recently priced IPOs from Finnhub."""
    if not FINNHUB_KEY:
        return pd.DataFrame()

    try:
        client = finnhub.Client(api_key=FINNHUB_KEY)
        past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        data = client.ipo_calendar(_from=past, to=today)

        ipos = data.get("ipoCalendar", [])
        if not ipos:
            return pd.DataFrame()

        results = []
        for ipo in ipos:
            price_low = ipo.get("priceRangeLow", 0) or 0
            price_high = ipo.get("priceRangeHigh", 0) or 0
            if price_low and price_high:
                price_range = f"${price_low:.2f} - ${price_high:.2f}"
            elif price_low:
                price_range = f"${price_low:.2f}"
            else:
                price_range = "TBD"

            results.append({
                "Date": ipo.get("date", ""),
                "Name": ipo.get("name", "N/A"),
                "Symbol": ipo.get("symbol", "N/A"),
                "Exchange": ipo.get("exchange", ""),
                "Price Range": price_range,
                "Status": ipo.get("status", ""),
            })

        df = pd.DataFrame(results)
        df = df.sort_values("Date", ascending=False).reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400)  # cache for 24h — holdings don't change intraday
def get_russell2000_constituents() -> pd.DataFrame:
    """Fetch Russell 2000 constituents from the IWM ETF holdings CSV.

    Returns DataFrame with columns: Ticker, Name, Sector, Weight (%), Price.
    Cached for 24 hours since the list changes only at index rebalancing.
    """
    url = (
        "https://www.ishares.com/us/products/239710/ISHARES-RUSSELL-2000-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    try:
        import requests, io
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), skiprows=9, thousands=",")
        # Keep only equity rows with a valid ticker
        df = df[df["Asset Class"] == "Equity"].copy()
        df = df.dropna(subset=["Ticker"])
        df = df[df["Ticker"].str.match(r"^[A-Z]{1,5}$")]

        keep = {"Ticker": "Ticker", "Name": "Name", "Sector": "Sector",
                "Weight (%)": "Weight %", "Price": "Price"}
        df = df.rename(columns=keep)[[c for c in keep.values() if c in df.rename(columns=keep).columns]]

        for col in ["Weight %", "Price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("Weight %", ascending=False).reset_index(drop=True)
        return df
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Russell 2000 fetch failed: %s", e)
        return pd.DataFrame()
