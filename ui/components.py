"""Reusable Streamlit UI components for charts and displays."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import streamlit as st

from analysis.technicals import compute_all_indicators


def render_candlestick_chart(df: pd.DataFrame, symbol: str) -> go.Figure:
    """Create an interactive candlestick chart with volume."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )

    fig.add_trace(
        go.Candlestick(
            x=df["Date"], open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], name="Price",
        ),
        row=1, col=1,
    )

    colors = ["red" if row["Close"] < row["Open"] else "green" for _, row in df.iterrows()]
    fig.add_trace(
        go.Bar(x=df["Date"], y=df["Volume"], marker_color=colors, name="Volume", opacity=0.5),
        row=2, col=1,
    )

    fig.update_layout(
        title=f"{symbol} Price Chart",
        xaxis_rangeslider_visible=False,
        height=600,
        template="plotly_dark",
        showlegend=False,
    )
    fig.update_yaxes(title_text="Price ($)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)

    return fig


def render_technical_chart(df: pd.DataFrame, symbol: str) -> go.Figure:
    """Create a chart with technical indicators (SMA, Bollinger Bands, RSI, MACD)."""
    indicators = compute_all_indicators(df)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.55, 0.2, 0.25],
        subplot_titles=("Price & Indicators", "RSI", "MACD"),
    )

    # Price + SMAs + Bollinger Bands
    fig.add_trace(
        go.Scatter(x=df["Date"], y=df["Close"], name="Close", line=dict(color="white", width=1.5)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["SMA_20"], name="SMA 20", line=dict(color="orange", width=1)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["SMA_50"], name="SMA 50", line=dict(color="cyan", width=1)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["SMA_200"], name="SMA 200", line=dict(color="magenta", width=1)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["Date"], y=indicators["BB_Upper"], name="BB Upper",
            line=dict(color="gray", width=0.5, dash="dash"),
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["Date"], y=indicators["BB_Lower"], name="BB Lower",
            line=dict(color="gray", width=0.5, dash="dash"),
            fill="tonexty", fillcolor="rgba(128,128,128,0.1)",
        ),
        row=1, col=1,
    )

    # RSI
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["RSI"], name="RSI", line=dict(color="yellow", width=1.5)),
        row=2, col=1,
    )
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)

    # MACD
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["MACD"], name="MACD", line=dict(color="cyan", width=1.5)),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["Date"], y=indicators["MACD_Signal"], name="Signal", line=dict(color="orange", width=1)),
        row=3, col=1,
    )
    histogram_colors = ["green" if v >= 0 else "red" for v in indicators["MACD_Histogram"]]
    fig.add_trace(
        go.Bar(x=df["Date"], y=indicators["MACD_Histogram"], name="Histogram", marker_color=histogram_colors),
        row=3, col=1,
    )

    fig.update_layout(
        title=f"{symbol} Technical Analysis",
        height=800,
        template="plotly_dark",
    )

    return fig


def render_recommendation_card(recommendation: dict) -> None:
    """Display the recommendation as a styled card."""
    signal = recommendation["signal"]
    score = recommendation["score"]

    color_map = {
        "Strong Buy": "green",
        "Buy": "lightgreen",
        "Hold": "orange",
        "Sell": "salmon",
        "Strong Sell": "red",
    }
    color = color_map.get(signal, "gray")

    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            border-left: 5px solid {color};
            padding: 20px;
            border-radius: 10px;
            margin: 10px 0;
        ">
            <h2 style="color: {color}; margin: 0;">{signal}</h2>
            <p style="color: white; font-size: 18px; margin: 5px 0;">
                Combined Score: <strong>{score}/100</strong>
            </p>
            <p style="color: #aaa; margin: 0;">
                Technical: {recommendation['technical_score']} | Fundamental: {recommendation['fundamental_score']}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Technical Signals")
        for reason in recommendation["technical_reasons"]:
            st.write(f"- {reason}")
    with col2:
        st.subheader("Fundamental Signals")
        for reason in recommendation["fundamental_reasons"]:
            st.write(f"- {reason}")


def render_fundamentals_table(fundamentals: dict) -> None:
    """Display key fundamentals in a clean format."""
    if not fundamentals:
        st.warning("No fundamental data available.")
        return

    display_map = {
        "marketCap": ("Market Cap", lambda v: f"${v/1e9:.2f}B" if v > 1e9 else f"${v/1e6:.0f}M"),
        "trailingPE": ("P/E (TTM)", lambda v: f"{v:.2f}"),
        "forwardPE": ("P/E (Forward)", lambda v: f"{v:.2f}"),
        "pegRatio": ("PEG Ratio", lambda v: f"{v:.2f}"),
        "priceToBook": ("P/B Ratio", lambda v: f"{v:.2f}"),
        "revenueGrowth": ("Revenue Growth", lambda v: f"{v:.1%}"),
        "earningsGrowth": ("Earnings Growth", lambda v: f"{v:.1%}"),
        "profitMargins": ("Profit Margin", lambda v: f"{v:.1%}"),
        "returnOnEquity": ("ROE", lambda v: f"{v:.1%}"),
        "debtToEquity": ("Debt/Equity", lambda v: f"{v:.0f}%"),
        "dividendYield": ("Dividend Yield", lambda v: f"{v:.2%}"),
        "beta": ("Beta", lambda v: f"{v:.2f}"),
        "fiftyTwoWeekHigh": ("52W High", lambda v: f"${v:.2f}"),
        "fiftyTwoWeekLow": ("52W Low", lambda v: f"${v:.2f}"),
        "currentPrice": ("Current Price", lambda v: f"${v:.2f}"),
        "targetMeanPrice": ("Analyst Target", lambda v: f"${v:.2f}"),
    }

    col1, col2 = st.columns(2)
    items = list(display_map.items())
    mid = len(items) // 2

    for i, (key, (label, formatter)) in enumerate(items):
        value = fundamentals.get(key)
        if value is not None:
            col = col1 if i < mid else col2
            with col:
                st.metric(label=label, value=formatter(value))
