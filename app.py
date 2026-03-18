"""InvBot — Investment Analysis & Recommendations."""

import asyncio
import time
from datetime import datetime as dt

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from config import TIME_PERIODS, FINNHUB_KEY
import plotly.graph_objects as go

from data.fetcher import (
    get_price_history, get_fundamentals, search_tickers,
    get_top_performers, get_dividend_leaders,
    get_upcoming_ipos, get_recent_ipos,
    get_market_indices, get_market_sentiment, get_sp500_sparkline,
    get_market_news, get_company_news, get_sector_performance,
    get_russell2000_constituents,
)
from data.db import (
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    log_search, get_recent_searches,
    add_position, remove_position, get_positions, update_position,
    create_price_alert, update_alert_workflow_id, cancel_price_alert,
    get_active_alerts, get_triggered_alerts,
    create_stock_journey, update_journey_workflow_id, get_journey_snapshots,
    get_active_journeys, get_all_journeys, finalize_journey,
    log_visit, get_visit_log, get_visit_summary,
    get_investment_log,
)
from analysis.recommender import get_recommendation
from ui.components import (
    render_candlestick_chart, render_technical_chart,
    render_recommendation_card, render_fundamentals_table,
)

st.set_page_config(
    page_title="InvBot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Activity-based cache management ---
# Cache only refreshes when user is active (interacted within last 5 min)
IDLE_TIMEOUT = 300  # 5 minutes
CACHE_REFRESH_INTERVALS = {
    "prices": 300,       # 5 min
    "fundamentals": 3600,  # 1 hr
}
# Use the shorter interval for auto-refresh
CACHE_REFRESH_INTERVAL = CACHE_REFRESH_INTERVALS["prices"]

now = time.time()

# Detect idle: check time since the PREVIOUS rerun
if "prev_activity" not in st.session_state:
    st.session_state["prev_activity"] = now
if "last_cache_refresh" not in st.session_state:
    st.session_state["last_cache_refresh"] = now

idle_time = now - st.session_state["prev_activity"]
st.session_state["prev_activity"] = now

if idle_time <= IDLE_TIMEOUT:
    # User is active — auto-refresh cache if interval has passed
    if now - st.session_state["last_cache_refresh"] > CACHE_REFRESH_INTERVAL:
        st.cache_data.clear()
        st.session_state["last_cache_refresh"] = now

# --- Sidebar ---
st.sidebar.title("InvBot")
page = st.sidebar.radio("Navigate", [
    "Market Overview", "Top Performers", "Dividends", "IPO Calendar",
    "News", "Search & Analyze", "Watchlist", "Alerts", "Portfolio",
    "Trade", "Stock Journey", "Russell 2000", "Sector Heatmap", "Visitor Log",
])

# --- Log this page visit ---
_headers = st.context.headers if hasattr(st, "context") else {}
_ip = _headers.get("X-Forwarded-For", _headers.get("X-Real-Ip", "local"))
_ua = _headers.get("User-Agent", "")
if "last_logged_page" not in st.session_state or st.session_state["last_logged_page"] != page:
    log_visit(page, _ip, _ua)
    st.session_state["last_logged_page"] = page

# Cache controls
st.sidebar.markdown("---")
if st.sidebar.button("Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.session_state["last_cache_refresh"] = time.time()
    st.toast("Cache cleared! Data will be refreshed.", icon="🔄")
    st.rerun()

st.sidebar.caption(
    "Auto-refresh when active | Paused when idle > 5min"
)

# Recent searches in sidebar
recent = get_recent_searches(5)
if recent:
    st.sidebar.markdown("---")
    st.sidebar.subheader("Recent Searches")
    for sym in recent:
        if st.sidebar.button(sym, key=f"recent_{sym}"):
            st.session_state["search_symbol"] = sym


# --- Temporal helpers ---

def _start_alert_workflow(alert_id: int, symbol: str, target_price: float, direction: str) -> str:
    """Start a PriceAlertWorkflow and return the workflow_id. Returns '' on failure."""
    try:
        from temporalio.client import Client
        from workflows.price_alert import PriceAlertWorkflow, AlertInput, TASK_QUEUE

        async def _run():
            client = await Client.connect("localhost:7233")
            wf_id = f"alert-{alert_id}"
            await client.start_workflow(
                PriceAlertWorkflow.run,
                AlertInput(
                    alert_id=alert_id,
                    symbol=symbol,
                    target_price=target_price,
                    direction=direction,
                ),
                id=wf_id,
                task_queue=TASK_QUEUE,
            )
            return wf_id

        return asyncio.run(_run())
    except Exception as e:
        st.warning(f"Temporal unavailable — alert saved but workflow not started. ({e})")
        return ""


def _start_journey_workflow(journey_id: int, symbol: str, interval_seconds: int, max_duration_hours: float) -> str:
    """Start a StockJourneyWorkflow. Returns the workflow_id or '' on failure."""
    try:
        from temporalio.client import Client
        from workflows.stock_journey import StockJourneyWorkflow, JourneyInput, TASK_QUEUE

        async def _run():
            client = await Client.connect("localhost:7233")
            wf_id = f"journey-{journey_id}"
            await client.start_workflow(
                StockJourneyWorkflow.run,
                JourneyInput(
                    journey_id=journey_id,
                    symbol=symbol,
                    interval_seconds=interval_seconds,
                    max_duration_hours=max_duration_hours,
                ),
                id=wf_id,
                task_queue=TASK_QUEUE,
            )
            return wf_id

        return asyncio.run(_run())
    except Exception as e:
        st.warning(f"Temporal unavailable — journey saved but workflow not started. ({e})")
        return ""


def _submit_trade(symbol: str, action: str, quantity: float, use_dollars: bool,
                  order_type: str, limit_price: float, reason: str) -> str:
    """Submit an AutoInvestWorkflow and return the result string. Returns error msg on failure."""
    try:
        from temporalio.client import Client
        from workflows.auto_invest import AutoInvestWorkflow, InvestmentOrder, TASK_QUEUE

        async def _run():
            client = await Client.connect("localhost:7233")
            import uuid
            wf_id = f"trade-{symbol}-{uuid.uuid4().hex[:8]}"
            handle = await client.start_workflow(
                AutoInvestWorkflow.run,
                InvestmentOrder(
                    symbol=symbol,
                    action=action,
                    quantity=quantity,
                    use_dollars=use_dollars,
                    order_type=order_type,
                    limit_price=limit_price,
                    reason=reason,
                ),
                id=wf_id,
                task_queue=TASK_QUEUE,
            )
            return await handle.result()

        return asyncio.run(_run())
    except Exception as e:
        return f"ERROR: {e}"


def _cancel_journey_workflow(workflow_id: str) -> bool:
    """Send a cancel signal to a running StockJourneyWorkflow."""
    try:
        from temporalio.client import Client
        from workflows.stock_journey import StockJourneyWorkflow

        async def _run():
            client = await Client.connect("localhost:7233")
            handle = client.get_workflow_handle_for(StockJourneyWorkflow.run, workflow_id)
            await handle.signal(StockJourneyWorkflow.cancel)
            return True

        return asyncio.run(_run())
    except Exception:
        return False


# --- Market Overview Page ---
if page == "Market Overview":
    st.title("Market Overview")

    with st.spinner("Loading market data..."):
        indices = get_market_indices()
        sentiment = get_market_sentiment()
        sparkline_df = get_sp500_sparkline()

    # Indices row
    if indices:
        cols = st.columns(len(indices))
        for col, (name, data) in zip(cols, indices.items()):
            delta_color = "normal"
            col.metric(
                label=name,
                value=f"{data['value']:,.2f}",
                delta=f"{data['change_pct']:+.2f}%",
                delta_color=delta_color,
            )
    else:
        st.warning("Could not fetch index data.")

    st.markdown("---")

    # Sentiment gauge + sparkline
    col_sentiment, col_spark = st.columns([1, 2])

    with col_sentiment:
        score = sentiment["score"]
        label = sentiment["label"]
        color = (
            "#ff4444" if score < 20
            else "#ff8800" if score < 40
            else "#aaaaaa" if score < 60
            else "#88cc44" if score < 80
            else "#00cc44"
        )
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            title={"text": f"Market Sentiment<br><b>{label}</b>"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": color},
                "steps": [
                    {"range": [0, 20], "color": "#330000"},
                    {"range": [20, 40], "color": "#332200"},
                    {"range": [40, 60], "color": "#222222"},
                    {"range": [60, 80], "color": "#1a2200"},
                    {"range": [80, 100], "color": "#003300"},
                ],
            },
        ))
        fig_gauge.update_layout(height=260, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig_gauge, use_container_width=True)

        comp = sentiment.get("components", {})
        st.caption(
            f"VIX: {comp.get('vix', '—')} · "
            f"Breadth: {comp.get('breadth', '—')} · "
            f"Momentum: {comp.get('momentum', '—')}"
        )

    with col_spark:
        if not sparkline_df.empty:
            time_col = "Datetime" if "Datetime" in sparkline_df.columns else "Date"
            fig_spark = go.Figure(go.Scatter(
                x=sparkline_df[time_col],
                y=sparkline_df["Close"],
                mode="lines",
                line=dict(color="#00aaff", width=2),
                fill="tozeroy",
                fillcolor="rgba(0,170,255,0.1)",
            ))
            fig_spark.update_layout(
                title="S&P 500 — 5 Day",
                height=260,
                margin=dict(t=40, b=10, l=10, r=10),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="#333"),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_spark, use_container_width=True)
        else:
            st.info("Sparkline data unavailable.")

    # Sector snapshot
    st.markdown("---")
    st.subheader("Sector Performance (Today)")
    with st.spinner("Loading sector data..."):
        sector_df = get_sector_performance("1d")

    if not sector_df.empty:
        sector_df_sorted = sector_df.sort_values("Change %", ascending=True)
        colors = ["#ff4444" if v < 0 else "#44cc44" for v in sector_df_sorted["Change %"]]
        fig_bar = go.Figure(go.Bar(
            x=sector_df_sorted["Change %"],
            y=sector_df_sorted["Sector"],
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.2f}%" for v in sector_df_sorted["Change %"]],
            textposition="outside",
        ))
        fig_bar.update_layout(
            height=360,
            margin=dict(t=10, b=10, l=10, r=60),
            xaxis=dict(showgrid=True, gridcolor="#333", zeroline=True, zerolinecolor="#555"),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("Sector data unavailable.")


# --- Top Performers Page ---
elif page == "Top Performers":
    st.title("Top Performers")

    period_options = {
        "Today": "1d",
        "This Week": "5d",
        "This Month": "1mo",
        "3 Months": "3mo",
        "6 Months": "6mo",
        "1 Year": "1y",
    }

    selected_period = st.selectbox("Time Period", list(period_options.keys()), index=2)
    yf_period = period_options[selected_period]

    with st.spinner(f"Fetching {selected_period.lower()} performance data..."):
        df = get_top_performers(period=yf_period)

    if df.empty:
        st.warning("Could not fetch performance data. Try again later.")
    else:
        watchlist_symbols = {w["symbol"] for w in get_watchlist()}

        def render_performers_table(rows, key_prefix):
            header = st.columns([1, 2, 2, 2, 2])
            header[0].markdown("**#**")
            header[1].markdown("**Symbol**")
            header[2].markdown("**Price**")
            header[3].markdown("**Change %**")
            header[4].markdown("**Watchlist**")
            st.divider()
            for i, row in enumerate(rows, 1):
                sym = row["Symbol"]
                chg = row["Change %"]
                color = "green" if chg >= 0 else "red"
                cols = st.columns([1, 2, 2, 2, 2])
                cols[0].write(i)
                cols[1].write(sym)
                cols[2].write(f"${row['Price']:.2f}")
                cols[3].markdown(f"<span style='color:{color}'>{chg:+.2f}%</span>", unsafe_allow_html=True)
                if sym in watchlist_symbols:
                    if cols[4].button("Remove", key=f"{key_prefix}_rm_{sym}"):
                        remove_from_watchlist(sym)
                        st.rerun()
                else:
                    if cols[4].button("+ Watch", key=f"{key_prefix}_add_{sym}"):
                        add_to_watchlist(sym, sym)
                        st.rerun()

        # Top gainers
        st.subheader("Top Gainers")
        render_performers_table(df.head(10).to_dict("records"), "g")

        st.markdown("---")

        # Top losers
        st.subheader("Top Losers")
        render_performers_table(df.tail(10).iloc[::-1].to_dict("records"), "l")

        # Full leaderboard
        with st.expander("View All"):
            render_performers_table(df.to_dict("records"), "all")


# --- Dividends Page ---
elif page == "Dividends":
    st.title("Top Dividend Stocks & ETFs")

    with st.spinner("Fetching dividend data..."):
        div_df = get_dividend_leaders()

    if div_df.empty:
        st.warning("Could not fetch dividend data. Try again later.")
    else:
        # Filter tabs
        tab_all, tab_etfs, tab_stocks = st.tabs(["All", "ETFs", "Stocks"])

        format_dict = {
            "Price": "${:.2f}",
            "Yield %": "{:.2f}%",
            "Annual Div": "${:.2f}",
            "Payout Ratio": "{:.1f}%",
            "5Y Avg Yield": "{:.2f}%",
        }

        def style_div_table(df):
            styled = df.style.format(format_dict, na_rep="—")
            styled = styled.map(
                lambda v: "color: green; font-weight: bold" if isinstance(v, (int, float)) and v >= 4
                else "color: lightgreen" if isinstance(v, (int, float)) and v >= 2
                else "",
                subset=["Yield %"],
            )
            return styled

        with tab_all:
            all_div = div_df.copy()
            all_div.index = range(1, len(all_div) + 1)
            st.dataframe(style_div_table(all_div), width="stretch")

        with tab_etfs:
            etf_div = div_df[div_df["Type"] == "ETF"].copy()
            if etf_div.empty:
                st.info("No ETF dividend data available.")
            else:
                etf_div.index = range(1, len(etf_div) + 1)
                st.dataframe(style_div_table(etf_div), width="stretch")

        with tab_stocks:
            stock_div = div_df[div_df["Type"] == "Stock"].copy()
            if stock_div.empty:
                st.info("No stock dividend data available.")
            else:
                stock_div.index = range(1, len(stock_div) + 1)
                st.dataframe(style_div_table(stock_div), width="stretch")

        # Summary metrics
        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        with col1:
            avg_yield = div_df["Yield %"].mean()
            st.metric("Avg Yield", f"{avg_yield:.2f}%")
        with col2:
            max_yield = div_df.iloc[0]
            st.metric("Highest Yield", f"{max_yield['Yield %']:.2f}%", delta=max_yield["Symbol"])
        with col3:
            etf_avg = div_df[div_df["Type"] == "ETF"]["Yield %"].mean()
            st.metric("Avg ETF Yield", f"{etf_avg:.2f}%" if not pd.isna(etf_avg) else "—")


# --- IPO Calendar Page ---
elif page == "IPO Calendar":
    st.title("IPO Calendar")

    if not FINNHUB_KEY:
        st.warning(
            "Finnhub API key required for IPO data. "
            "Get a free key at [finnhub.io](https://finnhub.io) and add it to your `.env` file:\n\n"
            "`FINNHUB_KEY=your_key_here`"
        )
    else:
        tab_upcoming, tab_recent = st.tabs(["Upcoming IPOs", "Recent IPOs"])

        with tab_upcoming:
            with st.spinner("Fetching upcoming IPOs..."):
                upcoming_df = get_upcoming_ipos()

            if upcoming_df.empty:
                st.info("No upcoming IPOs found in the next 90 days.")
            else:
                st.caption(f"Showing {len(upcoming_df)} upcoming IPOs (next 90 days)")
                st.dataframe(upcoming_df, width="stretch", hide_index=True)

        with tab_recent:
            with st.spinner("Fetching recent IPOs..."):
                recent_df = get_recent_ipos()

            if recent_df.empty:
                st.info("No recent IPOs found in the past 30 days.")
            else:
                st.caption(f"Showing {len(recent_df)} IPOs from the past 30 days")
                st.dataframe(recent_df, width="stretch", hide_index=True)


# --- News Page ---
elif page == "News":
    st.title("Market News")

    if not FINNHUB_KEY:
        st.warning(
            "Finnhub API key required for news data. "
            "Get a free key at [finnhub.io](https://finnhub.io) and add it to your `.env` file:\n\n"
            "`FINNHUB_KEY=your_key_here`"
        )
    else:
        with st.spinner("Fetching market news..."):
            news_items = get_market_news("general")

        if not news_items:
            st.info("No news articles available at this time.")
        else:
            st.caption(f"Showing {len(news_items)} latest market news articles")
            for article in news_items:
                headline = article.get("headline", "No headline")
                url = article.get("url", "#")
                source = article.get("source", "Unknown")
                ts = article.get("datetime", 0)
                summary = article.get("summary", "")
                image = article.get("image", "")

                date_str = dt.fromtimestamp(ts).strftime("%b %d, %Y %I:%M %p") if ts else ""
                summary_snippet = (summary[:200] + "...") if len(summary) > 200 else summary

                card_html = f"""
                <div style="border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 12px;">
                    <a href="{url}" target="_blank" style="text-decoration: none; color: inherit;">
                        <h4 style="margin: 0 0 8px 0;">{headline}</h4>
                    </a>
                    <p style="margin: 0 0 6px 0; font-size: 0.85em; color: #888;">
                        {source} &middot; {date_str}
                    </p>
                    <p style="margin: 0; font-size: 0.9em; color: #aaa;">{summary_snippet}</p>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)


# --- Search & Analyze Page ---
elif page == "Search & Analyze":
    st.title("Stock & ETF Analysis")

    # Live search-as-you-type (triggers after 3 characters)
    def search_stocks(query: str) -> list[tuple[str, str]]:
        """Return list of (display_label, symbol) for the searchbox."""
        if len(query.strip()) < 3:
            return []
        results = search_tickers(query)
        return [
            (f"{r['symbol']} — {r['name']} ({r['type']})", r["symbol"])
            for r in results[:8]
        ]

    selected = st_searchbox(
        search_stocks,
        label="Search by ticker or company name",
        placeholder="e.g., AAPL, Tesla, SPY",
        key="ticker_search",
        clear_on_submit=False,
    )

    # Analyze the selected ticker
    symbol = selected
    if symbol:
        log_search(symbol)
        st.session_state["search_symbol"] = symbol

        with st.spinner(f"Fetching data for {symbol}..."):
            fundamentals = get_fundamentals(symbol)

        if not fundamentals:
            st.error(f"Could not find data for '{symbol}'. Please check the ticker.")
        else:
            # Header
            name = fundamentals.get("shortName", symbol)
            price = fundamentals.get("currentPrice")
            sector = fundamentals.get("sector", "")
            industry = fundamentals.get("industry", "")

            st.markdown(f"## {name} ({symbol})")
            if sector:
                st.caption(f"{sector} · {industry}")

            # Price header
            col_price, col_high, col_low, col_cap = st.columns(4)
            with col_price:
                st.metric("Current Price", f"${price:.2f}" if price else "N/A")
            with col_high:
                high_52 = fundamentals.get("fiftyTwoWeekHigh")
                st.metric("52W High", f"${high_52:.2f}" if high_52 else "N/A")
            with col_low:
                low_52 = fundamentals.get("fiftyTwoWeekLow")
                st.metric("52W Low", f"${low_52:.2f}" if low_52 else "N/A")
            with col_cap:
                mcap = fundamentals.get("marketCap")
                if mcap:
                    cap_str = f"${mcap/1e12:.2f}T" if mcap >= 1e12 else f"${mcap/1e9:.2f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M"
                else:
                    cap_str = "N/A"
                st.metric("Market Cap", cap_str)

            # Watchlist button
            watchlist_symbols = [w["symbol"] for w in get_watchlist()]
            if symbol in watchlist_symbols:
                if st.button("Remove from Watchlist"):
                    remove_from_watchlist(symbol)
                    st.rerun()
            else:
                if st.button("Add to Watchlist"):
                    add_to_watchlist(symbol, name)
                    st.rerun()

            # Time period selector
            period_label = st.select_slider(
                "Time Period",
                options=list(TIME_PERIODS.keys()),
                value="1Y",
            )
            period_days = TIME_PERIODS[period_label]

            with st.spinner("Loading price history..."):
                price_df = get_price_history(symbol, period_days)

            if price_df.empty:
                st.warning("No price history available.")
            else:
                # Tabs for different views
                tab_price, tab_technical, tab_fundamentals, tab_recommendation, tab_news = st.tabs(
                    ["Price Chart", "Technical Analysis", "Fundamentals", "Recommendation", "News"]
                )

                with tab_price:
                    fig = render_candlestick_chart(price_df, symbol)
                    st.plotly_chart(fig, width="stretch")

                with tab_technical:
                    fig = render_technical_chart(price_df, symbol)
                    st.plotly_chart(fig, width="stretch")

                with tab_fundamentals:
                    render_fundamentals_table(fundamentals)

                with tab_recommendation:
                    rec = get_recommendation(price_df, fundamentals)
                    render_recommendation_card(rec)

                with tab_news:
                    if not FINNHUB_KEY:
                        st.warning("Finnhub API key required for news. Add `FINNHUB_KEY` to your `.env` file.")
                    else:
                        with st.spinner(f"Fetching news for {symbol}..."):
                            company_news = get_company_news(symbol)

                        if not company_news:
                            st.info(f"No recent news found for {symbol}.")
                        else:
                            st.caption(f"Showing {len(company_news)} recent news articles for {symbol}")
                            for article in company_news:
                                headline = article.get("headline", "No headline")
                                url = article.get("url", "#")
                                source = article.get("source", "Unknown")
                                ts = article.get("datetime", 0)
                                summary = article.get("summary", "")

                                date_str = dt.fromtimestamp(ts).strftime("%b %d, %Y %I:%M %p") if ts else ""
                                summary_snippet = (summary[:200] + "...") if len(summary) > 200 else summary

                                card_html = f"""
                                <div style="border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 12px;">
                                    <a href="{url}" target="_blank" style="text-decoration: none; color: inherit;">
                                        <h4 style="margin: 0 0 8px 0;">{headline}</h4>
                                    </a>
                                    <p style="margin: 0 0 6px 0; font-size: 0.85em; color: #888;">
                                        {source} &middot; {date_str}
                                    </p>
                                    <p style="margin: 0; font-size: 0.9em; color: #aaa;">{summary_snippet}</p>
                                </div>
                                """
                                st.markdown(card_html, unsafe_allow_html=True)


# --- Watchlist Page ---
elif page == "Watchlist":
    st.title("Watchlist")

    watchlist = get_watchlist()

    if not watchlist:
        st.info("Your watchlist is empty. Search for a stock and add it!")
    else:
        for item in watchlist:
            sym = item["symbol"]
            col1, col2, col3 = st.columns([2, 4, 1])
            with col1:
                st.markdown(f"### {sym}")
            with col2:
                st.write(item.get("name", ""))
                st.caption(f"Added: {item['added_at']}")
            with col3:
                if st.button("Analyze", key=f"wl_analyze_{sym}"):
                    st.session_state["search_symbol"] = sym
                    st.session_state["page"] = "Search & Analyze"
                    st.rerun()
                if st.button("Remove", key=f"wl_remove_{sym}"):
                    remove_from_watchlist(sym)
                    st.rerun()

            # Price alert expander per stock
            with st.expander(f"Set Price Alert — {sym}"):
                active_alerts = get_active_alerts(sym)
                if active_alerts:
                    st.caption(f"{len(active_alerts)} active alert(s):")
                    for a in active_alerts:
                        acol1, acol2 = st.columns([4, 1])
                        acol1.write(
                            f"{'↑' if a['direction'] == 'above' else '↓'} "
                            f"${a['target_price']:.2f} ({a['direction']})"
                        )
                        if acol2.button("Cancel", key=f"cancel_alert_{a['id']}"):
                            cancel_price_alert(a["id"])
                            st.rerun()
                    st.divider()

                with st.form(key=f"alert_form_{sym}"):
                    target = st.number_input("Target Price ($)", min_value=0.01, step=0.01, format="%.2f", key=f"target_{sym}")
                    direction = st.radio("Trigger when price goes", ["above", "below"], horizontal=True, key=f"dir_{sym}")
                    submitted = st.form_submit_button("Create Alert")
                    if submitted and target > 0:
                        alert_id = create_price_alert(sym, target, direction)
                        wf_id = _start_alert_workflow(alert_id, sym, target, direction)
                        if wf_id:
                            update_alert_workflow_id(alert_id, wf_id)
                            st.success(f"Alert created! Monitoring {sym} {'above' if direction == 'above' else 'below'} ${target:.2f}")
                        else:
                            st.info("Alert saved. Start the Temporal worker to activate monitoring.")
                        st.rerun()

            st.divider()


# --- Alerts Page ---
elif page == "Alerts":
    st.title("Price Alerts")

    tab_active, tab_triggered = st.tabs(["Active", "Triggered"])

    with tab_active:
        active = get_active_alerts()
        if not active:
            st.info("No active alerts. Set one from the Watchlist page.")
        else:
            header = st.columns([2, 2, 2, 2, 1])
            header[0].markdown("**Symbol**")
            header[1].markdown("**Target**")
            header[2].markdown("**Direction**")
            header[3].markdown("**Created**")
            header[4].markdown("**Action**")
            st.divider()
            for a in active:
                cols = st.columns([2, 2, 2, 2, 1])
                cols[0].write(a["symbol"])
                cols[1].write(f"${a['target_price']:.2f}")
                arrow = "↑ above" if a["direction"] == "above" else "↓ below"
                cols[2].write(arrow)
                cols[3].caption(str(a["created_at"])[:16])
                if cols[4].button("Cancel", key=f"alerts_cancel_{a['id']}"):
                    cancel_price_alert(a["id"])
                    st.rerun()

    with tab_triggered:
        triggered = get_triggered_alerts(limit=50)
        if not triggered:
            st.info("No triggered alerts yet.")
        else:
            for a in triggered:
                arrow = "↑" if a["direction"] == "above" else "↓"
                st.markdown(
                    f"**{a['symbol']}** — target {arrow} ${a['target_price']:.2f} "
                    f"hit at **${a['triggered_price']:.2f}** "
                    f"on {str(a['triggered_at'])[:16]}"
                )
            st.caption(f"Showing last {len(triggered)} triggered alerts.")


# --- Portfolio Page ---
elif page == "Portfolio":
    st.title("Portfolio")

    positions = get_positions()

    # Add position form
    with st.expander("Add / Update Position"):
        with st.form("add_position_form"):
            p_col1, p_col2, p_col3 = st.columns(3)
            p_sym = p_col1.text_input("Symbol").upper().strip()
            p_shares = p_col2.number_input("Shares", min_value=0.001, step=0.001, format="%.3f")
            p_price = p_col3.number_input("Avg Buy Price ($)", min_value=0.01, step=0.01, format="%.2f")
            if st.form_submit_button("Save Position"):
                if p_sym and p_shares > 0 and p_price > 0:
                    add_position(p_sym, p_shares, p_price)
                    st.success(f"Position saved: {p_sym}")
                    st.rerun()
                else:
                    st.warning("Please fill in all fields.")

    if not positions:
        st.info("No positions yet. Add one above.")
    else:
        # Fetch current prices for P&L
        symbols = [p["symbol"] for p in positions]
        price_map = {}
        with st.spinner("Fetching current prices..."):
            for sym in symbols:
                try:
                    import yfinance as yf
                    t = yf.Ticker(sym)
                    hist = t.history(period="1d")
                    price_map[sym] = float(hist["Close"].iloc[-1]) if not hist.empty else None
                except Exception:
                    price_map[sym] = None

        # Summary metrics
        total_cost = sum(p["shares"] * p["buy_price"] for p in positions)
        total_value = sum(
            p["shares"] * (price_map.get(p["symbol"]) or p["buy_price"])
            for p in positions
        )
        total_pnl = total_value - total_cost
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Cost Basis", f"${total_cost:,.2f}")
        m2.metric("Current Value", f"${total_value:,.2f}")
        m3.metric("Total P&L", f"${total_pnl:+,.2f}", delta=f"{pnl_pct:+.2f}%")

        st.markdown("---")

        # Positions table
        hdr = st.columns([2, 1, 2, 2, 2, 2, 1])
        for h, label in zip(hdr, ["Symbol", "Shares", "Buy Price", "Current", "Value", "P&L", ""]):
            h.markdown(f"**{label}**")
        st.divider()

        for pos in positions:
            sym = pos["symbol"]
            shares = pos["shares"]
            buy = pos["buy_price"]
            current = price_map.get(sym)
            value = shares * (current or buy)
            pnl = shares * ((current - buy) if current else 0)
            pnl_color = "green" if pnl >= 0 else "red"

            cols = st.columns([2, 1, 2, 2, 2, 2, 1])
            cols[0].write(sym)
            cols[1].write(f"{shares:.3f}")
            cols[2].write(f"${buy:.2f}")
            cols[3].write(f"${current:.2f}" if current else "—")
            cols[4].write(f"${value:,.2f}")
            cols[5].markdown(
                f"<span style='color:{pnl_color}'>${pnl:+,.2f}</span>",
                unsafe_allow_html=True,
            )
            if cols[6].button("Remove", key=f"port_rm_{sym}"):
                remove_position(sym)
                st.rerun()


# --- Trade Page ---
elif page == "Trade":
    st.title("Trade")
    st.caption("Place buy/sell orders via the AutoInvest Temporal workflow, routed through Alpaca paper trading.")

    from config import ALPACA_API_KEY, ALPACA_PAPER
    if ALPACA_API_KEY:
        mode_label = "Paper Trading" if ALPACA_PAPER else "Live Trading"
        mode_color = "success" if ALPACA_PAPER else "warning"
        if ALPACA_PAPER:
            st.success(f"**{mode_label} mode (Alpaca)** — orders are sent to Alpaca paper trading. No real money is moved.")
        else:
            st.warning(f"**{mode_label} mode (Alpaca)** — orders will execute with real money!")
    else:
        st.info("**Dry-run mode** — orders are validated and logged but not sent to any broker. Add `ALPACA_API_KEY` to `.env` to enable paper trading.")

    tab_order, tab_history = st.tabs(["Place Order", "Order History"])

    with tab_order:
        with st.form("trade_form"):
            tc1, tc2 = st.columns(2)
            t_sym = tc1.text_input("Symbol", placeholder="AAPL").upper().strip()
            t_action = tc2.radio("Action", ["buy", "sell"], horizontal=True)

            tc3, tc4 = st.columns(2)
            t_qty_type = tc3.radio("Quantity type", ["Shares", "Dollar amount"], horizontal=True)
            t_qty = tc4.number_input(
                "Shares" if t_qty_type == "Shares" else "Amount ($)",
                min_value=0.001, step=0.001, format="%.3f"
            )

            tc5, tc6 = st.columns(2)
            t_order_type = tc5.radio("Order type", ["market", "limit"], horizontal=True)
            t_limit = tc6.number_input(
                "Limit price ($)", min_value=0.0, step=0.01, format="%.2f",
                disabled=(t_order_type == "market"),
            )

            t_reason = st.text_input("Reason (optional)", placeholder="e.g. RSI oversold, price alert triggered")

            submitted = st.form_submit_button("Submit Order", type="primary")

        if submitted:
            if not t_sym:
                st.error("Symbol is required.")
            elif t_qty <= 0:
                st.error("Quantity must be greater than 0.")
            elif t_order_type == "limit" and t_limit <= 0:
                st.error("Limit price required for limit orders.")
            else:
                with st.spinner(f"Submitting {t_action.upper()} order for {t_sym}..."):
                    result = _submit_trade(
                        symbol=t_sym,
                        action=t_action,
                        quantity=t_qty,
                        use_dollars=(t_qty_type == "Dollar amount"),
                        order_type=t_order_type,
                        limit_price=t_limit,
                        reason=t_reason,
                    )

                if result.startswith("ERROR"):
                    st.warning(f"Temporal unavailable — order not processed.\n\n`{result}`\n\nStart the worker with `python workflows/worker.py` to enable trade execution.")
                elif result.startswith("REJECTED"):
                    st.error(f"Order rejected: {result}")
                else:
                    st.success(result)
                    st.rerun()

    with tab_history:
        orders = get_investment_log(50)
        if not orders:
            st.info("No orders yet.")
        else:
            st.caption(f"Last {len(orders)} orders")
            hdr = st.columns([1, 2, 1, 2, 2, 2, 4])
            for h, lbl in zip(hdr, ["#", "Symbol", "Action", "Qty", "Type", "Status", "Message"]):
                h.markdown(f"**{lbl}**")
            st.divider()
            for i, o in enumerate(orders, 1):
                cols = st.columns([1, 2, 1, 2, 2, 2, 4])
                cols[0].write(i)
                cols[1].write(o["symbol"])
                action_color = "green" if o["action"] == "buy" else "red"
                cols[2].markdown(f"<span style='color:{action_color}'>{o['action'].upper()}</span>", unsafe_allow_html=True)
                qty_label = f"${o['quantity']:,.2f}" if o.get("use_dollars") else f"{o['quantity']:.3f} sh"
                cols[3].write(qty_label)
                cols[4].write(o["order_type"])
                cols[5].write("✅ OK" if o["success"] else "❌ Failed")
                cols[6].caption(str(o.get("message", ""))[:80])


# --- Stock Journey Page ---
elif page == "Stock Journey":
    st.title("Stock Journey")
    st.caption("Track a stock's price over time using a durable Temporal workflow.")

    # --- Start a new journey ---
    with st.expander("Start New Journey", expanded=True):
        with st.form("new_journey_form"):
            jcol1, jcol2, jcol3 = st.columns(3)
            j_sym = jcol1.text_input("Symbol (e.g. AAPL)").upper().strip()

            interval_labels = {
                "Every 1 min": 60,
                "Every 5 min": 300,
                "Every 15 min": 900,
                "Every 30 min": 1800,
                "Every hour": 3600,
            }
            j_interval_label = jcol2.selectbox("Sample interval", list(interval_labels.keys()), index=1)
            j_interval = interval_labels[j_interval_label]

            duration_labels = {
                "1 hour": 1.0,
                "4 hours": 4.0,
                "1 day": 24.0,
                "3 days": 72.0,
                "1 week": 168.0,
            }
            j_duration_label = jcol3.selectbox("Track for", list(duration_labels.keys()), index=2)
            j_duration = duration_labels[j_duration_label]

            if st.form_submit_button("Start Journey"):
                if not j_sym:
                    st.warning("Enter a stock symbol.")
                else:
                    journey_id = create_stock_journey(j_sym, j_interval, j_duration)
                    wf_id = _start_journey_workflow(journey_id, j_sym, j_interval, j_duration)
                    if wf_id:
                        update_journey_workflow_id(journey_id, wf_id)
                        st.success(
                            f"Journey started for **{j_sym}** — "
                            f"sampling {j_interval_label.lower()}, tracking for {j_duration_label}."
                        )
                    else:
                        st.info("Journey saved. Start the Temporal worker to begin tracking.")
                    st.rerun()

    # --- Active journeys ---
    active_journeys = get_active_journeys()
    all_journeys = get_all_journeys(limit=20)

    tab_active, tab_history = st.tabs([
        f"Active ({len(active_journeys)})",
        f"History ({len(all_journeys)})",
    ])

    with tab_active:
        if not active_journeys:
            st.info("No active journeys. Start one above.")
        else:
            for j in active_journeys:
                snapshots = get_journey_snapshots(j["id"])
                sym = j["symbol"]
                n = len(snapshots)

                with st.container():
                    hcol1, hcol2, hcol3, hcol4 = st.columns([2, 2, 2, 1])
                    hcol1.markdown(f"### {sym}")
                    hcol2.caption(
                        f"Every {j['interval_seconds']//60} min · "
                        f"{j['max_duration_hours']:.0f}h max"
                    )
                    hcol3.caption(f"Started: {str(j['started_at'])[:16]}  |  {n} snapshots")

                    if hcol4.button("Cancel", key=f"j_cancel_{j['id']}"):
                        if j.get("workflow_id"):
                            _cancel_journey_workflow(j["workflow_id"])
                        else:
                            finalize_journey(j["id"], "cancelled")
                        st.rerun()

                    if n == 0:
                        st.info("Waiting for first snapshot…")
                    else:
                        prices = [s["price"] for s in snapshots]
                        times = [s["recorded_at"] for s in snapshots]

                        # Key stats
                        first_price = prices[0]
                        last_price = prices[-1]
                        high = max(prices)
                        low = min(prices)
                        total_chg = last_price - first_price
                        total_chg_pct = (total_chg / first_price * 100) if first_price else 0

                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Current", f"${last_price:.2f}", delta=f"{total_chg_pct:+.2f}%")
                        m2.metric("Start", f"${first_price:.2f}")
                        m3.metric("High", f"${high:.2f}")
                        m4.metric("Low", f"${low:.2f}")

                        # Journey chart
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=times,
                            y=prices,
                            mode="lines+markers",
                            line=dict(color="#00aaff", width=2),
                            marker=dict(size=4),
                            fill="tozeroy",
                            fillcolor="rgba(0,170,255,0.08)",
                            name=sym,
                            hovertemplate="$%{y:.2f}<br>%{x}<extra></extra>",
                        ))
                        # Annotate first and last
                        fig.add_annotation(x=times[0], y=prices[0], text=f"Start ${first_price:.2f}", showarrow=True, arrowhead=2, ax=30, ay=-30)
                        fig.add_annotation(x=times[-1], y=prices[-1], text=f"Now ${last_price:.2f}", showarrow=True, arrowhead=2, ax=-30, ay=-30)

                        line_color = "#44cc44" if total_chg >= 0 else "#ff4444"
                        fig.add_hline(y=first_price, line_dash="dot", line_color=line_color, opacity=0.5, annotation_text="Start")

                        fig.update_layout(
                            title=f"{sym} Price Journey ({n} snapshots)",
                            height=320,
                            margin=dict(t=40, b=20, l=10, r=10),
                            xaxis=dict(showgrid=False),
                            yaxis=dict(showgrid=True, gridcolor="#333"),
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            showlegend=False,
                        )
                        st.plotly_chart(fig, use_container_width=True)

                    st.divider()

    with tab_history:
        if not all_journeys:
            st.info("No journey history yet.")
        else:
            for j in all_journeys:
                snapshots = get_journey_snapshots(j["id"])
                sym = j["symbol"]
                n = len(snapshots)
                status = j["status"]
                badge = {"active": "🟢", "completed": "✅", "cancelled": "❌"}.get(status, "")

                with st.expander(f"{badge} {sym} — {str(j['started_at'])[:16]} ({n} snapshots, {status})"):
                    if n < 2:
                        st.info("Not enough snapshots to chart.")
                    else:
                        prices = [s["price"] for s in snapshots]
                        times = [s["recorded_at"] for s in snapshots]
                        first_price = prices[0]
                        last_price = prices[-1]
                        total_chg_pct = ((last_price - first_price) / first_price * 100) if first_price else 0

                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Start", f"${first_price:.2f}")
                        m2.metric("End", f"${last_price:.2f}", delta=f"{total_chg_pct:+.2f}%")
                        m3.metric("High", f"${max(prices):.2f}")
                        m4.metric("Low", f"${min(prices):.2f}")

                        fig = go.Figure(go.Scatter(
                            x=times,
                            y=prices,
                            mode="lines",
                            line=dict(color="#00aaff" if total_chg_pct >= 0 else "#ff4444", width=2),
                            fill="tozeroy",
                            fillcolor="rgba(0,170,255,0.06)",
                            hovertemplate="$%{y:.2f}<br>%{x}<extra></extra>",
                        ))
                        fig.update_layout(
                            height=240,
                            margin=dict(t=10, b=10, l=10, r=10),
                            xaxis=dict(showgrid=False),
                            yaxis=dict(showgrid=True, gridcolor="#333"),
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                        )
                        st.plotly_chart(fig, use_container_width=True)


# --- Russell 2000 Page ---
elif page == "Russell 2000":
    st.title("Russell 2000 Constituents")
    st.caption("~2,000 small-cap US stocks tracked via the IWM ETF holdings. Updated daily.")

    with st.spinner("Fetching IWM holdings from iShares..."):
        r2k = get_russell2000_constituents()

    if r2k.empty:
        st.error("Could not load Russell 2000 data. iShares may be temporarily unavailable.")
    else:
        watchlist_symbols = {w["symbol"] for w in get_watchlist()}

        # Filters
        fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
        search_q = fcol1.text_input("Search ticker or name", placeholder="e.g. ACME or biotech").strip().upper()

        sectors = ["All"] + sorted(r2k["Sector"].dropna().unique().tolist()) if "Sector" in r2k.columns else ["All"]
        sel_sector = fcol2.selectbox("Sector", sectors)

        st.caption(f"{len(r2k)} constituents loaded")

        # Apply filters
        filtered = r2k.copy()
        if search_q:
            mask = filtered["Ticker"].str.contains(search_q, na=False)
            if "Name" in filtered.columns:
                mask |= filtered["Name"].str.upper().str.contains(search_q, na=False)
            filtered = filtered[mask]
        if sel_sector != "All" and "Sector" in filtered.columns:
            filtered = filtered[filtered["Sector"] == sel_sector]

        st.caption(f"Showing {len(filtered)} stocks")

        # Table with watchlist buttons
        has_sector = "Sector" in filtered.columns
        has_price = "Price" in filtered.columns
        has_weight = "Weight %" in filtered.columns

        col_widths = [1, 3, 3, 2, 2, 1] if has_sector else [1, 3, 2, 2, 1]
        header_labels = (
            ["#", "Ticker", "Name", "Sector", "Weight %", ""]
            if has_sector else
            ["#", "Ticker", "Name", "Weight %", ""]
        )

        hdr = st.columns(col_widths)
        for h, lbl in zip(hdr, header_labels):
            h.markdown(f"**{lbl}**")
        st.divider()

        # Paginate: show 50 at a time
        page_size = 50
        total_pages = max(1, (len(filtered) - 1) // page_size + 1)
        r2k_page = fcol3.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
        start = (r2k_page - 1) * page_size
        page_rows = filtered.iloc[start: start + page_size]

        for i, (_, row) in enumerate(page_rows.iterrows(), start=start + 1):
            ticker = row["Ticker"]
            cols = st.columns(col_widths)
            cols[0].write(i)
            cols[1].write(ticker)
            if "Name" in row:
                cols[2].write(str(row["Name"])[:30])
            if has_sector:
                cols[3].caption(str(row.get("Sector", "")) )
                if has_weight:
                    cols[4].write(f"{row['Weight %']:.3f}%")
                btn_col = cols[5]
            else:
                if has_weight:
                    cols[3].write(f"{row['Weight %']:.3f}%")
                btn_col = cols[4]

            if ticker in watchlist_symbols:
                if btn_col.button("✓", key=f"r2k_rm_{ticker}", help="Remove from watchlist"):
                    remove_from_watchlist(ticker)
                    st.rerun()
            else:
                if btn_col.button("+", key=f"r2k_add_{ticker}", help="Add to watchlist"):
                    add_to_watchlist(ticker, str(row.get("Name", ticker)))
                    st.rerun()


# --- Sector Heatmap Page ---
elif page == "Sector Heatmap":
    st.title("Sector Heatmap")

    period_opts = {"Today": "1d", "1 Week": "5d", "1 Month": "1mo", "3 Months": "3mo", "YTD": "ytd", "1 Year": "1y"}
    sel_period = st.selectbox("Period", list(period_opts.keys()), index=0)

    with st.spinner("Fetching sector data..."):
        sector_df = get_sector_performance(period_opts[sel_period])

    if sector_df.empty:
        st.warning("Could not fetch sector data.")
    else:
        sector_df_sorted = sector_df.sort_values("Change %", ascending=False).reset_index(drop=True)

        # Treemap heatmap
        colors = sector_df_sorted["Change %"].tolist()
        fig = go.Figure(go.Treemap(
            labels=sector_df_sorted["Sector"],
            parents=[""] * len(sector_df_sorted),
            values=[abs(c) + 0.5 for c in colors],
            customdata=list(zip(sector_df_sorted["ETF"], sector_df_sorted["Price"], sector_df_sorted["Change %"])),
            hovertemplate="<b>%{label}</b><br>ETF: %{customdata[0]}<br>Price: $%{customdata[1]:.2f}<br>Change: %{customdata[2]:+.2f}%<extra></extra>",
            text=[f"{c:+.2f}%" for c in colors],
            textposition="middle center",
            marker=dict(
                colors=colors,
                colorscale=[[0, "#aa0000"], [0.5, "#333333"], [1, "#007700"]],
                cmid=0,
                showscale=True,
                colorbar=dict(title="Change %"),
            ),
        ))
        fig.update_layout(height=500, margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            sector_df_sorted.style.format({"Price": "${:.2f}", "Change %": "{:+.2f}%"}),
            hide_index=True,
            use_container_width=True,
        )


# --- Visitor Log Page ---
elif page == "Visitor Log":
    st.title("Visitor Log")

    summary = get_visit_summary()
    log = get_visit_log(200)

    if not log:
        st.info("No visits recorded yet.")
    else:
        # Summary cards
        total = sum(r["visits"] for r in summary)
        unique_ips = len({r["ip"] for r in log if r["ip"] and r["ip"] != "local"})
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Page Views", total)
        col2.metric("Unique IPs", unique_ips)
        col3.metric("Pages Tracked", len(summary))

        st.subheader("Views by Page")
        summary_df = pd.DataFrame(summary)
        st.dataframe(summary_df, hide_index=True, use_container_width=True)

        st.subheader("Recent Visits")
        log_df = pd.DataFrame(log)
        st.dataframe(log_df, hide_index=True, use_container_width=True)
