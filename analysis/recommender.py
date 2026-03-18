"""Investment recommendation engine combining technical and fundamental analysis."""

import pandas as pd

from analysis.technicals import compute_all_indicators


def _score_technicals(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score based on technical indicators. Returns (score, reasons).

    Score range: -100 to +100.
    """
    if df.empty or len(df) < 200:
        return 0.0, ["Insufficient data for technical analysis"]

    indicators = compute_all_indicators(df)
    latest = indicators.iloc[-1]
    score = 0.0
    reasons = []

    # RSI signal
    rsi = latest.get("RSI")
    if rsi is not None and not pd.isna(rsi):
        if rsi < 30:
            score += 20
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 70:
            score -= 20
            reasons.append(f"RSI overbought ({rsi:.1f})")
        else:
            score += 5
            reasons.append(f"RSI neutral ({rsi:.1f})")

    # MACD crossover
    macd = latest.get("MACD")
    macd_signal = latest.get("MACD_Signal")
    if macd is not None and macd_signal is not None and not pd.isna(macd) and not pd.isna(macd_signal):
        if macd > macd_signal:
            score += 15
            reasons.append("MACD bullish crossover")
        else:
            score -= 15
            reasons.append("MACD bearish crossover")

    # Price vs moving averages
    price = latest["Close"]
    sma_50 = latest.get("SMA_50")
    sma_200 = latest.get("SMA_200")

    if sma_50 is not None and not pd.isna(sma_50):
        if price > sma_50:
            score += 10
            reasons.append("Price above 50-day SMA")
        else:
            score -= 10
            reasons.append("Price below 50-day SMA")

    if sma_200 is not None and not pd.isna(sma_200):
        if price > sma_200:
            score += 10
            reasons.append("Price above 200-day SMA")
        else:
            score -= 10
            reasons.append("Price below 200-day SMA")

    # Golden/Death cross
    if sma_50 is not None and sma_200 is not None and not pd.isna(sma_50) and not pd.isna(sma_200):
        if sma_50 > sma_200:
            score += 15
            reasons.append("Golden cross (50 SMA > 200 SMA)")
        else:
            score -= 15
            reasons.append("Death cross (50 SMA < 200 SMA)")

    # Bollinger Band position
    bb_upper = latest.get("BB_Upper")
    bb_lower = latest.get("BB_Lower")
    if bb_upper is not None and bb_lower is not None and not pd.isna(bb_upper) and not pd.isna(bb_lower):
        if price <= bb_lower:
            score += 10
            reasons.append("Price at lower Bollinger Band (potential bounce)")
        elif price >= bb_upper:
            score -= 10
            reasons.append("Price at upper Bollinger Band (potential pullback)")

    return max(-100, min(100, score)), reasons


def _score_fundamentals(fundamentals: dict) -> tuple[float, list[str]]:
    """Score based on fundamental metrics. Returns (score, reasons).

    Score range: -100 to +100.
    """
    if not fundamentals:
        return 0.0, ["No fundamental data available"]

    score = 0.0
    reasons = []

    # P/E ratio
    pe = fundamentals.get("trailingPE")
    if pe is not None:
        if pe < 15:
            score += 15
            reasons.append(f"Low P/E ratio ({pe:.1f}) — potentially undervalued")
        elif pe < 25:
            score += 5
            reasons.append(f"Moderate P/E ratio ({pe:.1f})")
        elif pe < 40:
            score -= 5
            reasons.append(f"High P/E ratio ({pe:.1f})")
        else:
            score -= 15
            reasons.append(f"Very high P/E ratio ({pe:.1f}) — potentially overvalued")

    # PEG ratio
    peg = fundamentals.get("pegRatio")
    if peg is not None:
        if peg < 1:
            score += 15
            reasons.append(f"PEG < 1 ({peg:.2f}) — growth at reasonable price")
        elif peg < 2:
            score += 5
            reasons.append(f"PEG moderate ({peg:.2f})")
        else:
            score -= 10
            reasons.append(f"PEG > 2 ({peg:.2f}) — expensive for growth rate")

    # Revenue growth
    rev_growth = fundamentals.get("revenueGrowth")
    if rev_growth is not None:
        if rev_growth > 0.20:
            score += 15
            reasons.append(f"Strong revenue growth ({rev_growth:.0%})")
        elif rev_growth > 0.05:
            score += 5
            reasons.append(f"Moderate revenue growth ({rev_growth:.0%})")
        elif rev_growth < 0:
            score -= 15
            reasons.append(f"Revenue declining ({rev_growth:.0%})")

    # Profit margins
    margins = fundamentals.get("profitMargins")
    if margins is not None:
        if margins > 0.20:
            score += 10
            reasons.append(f"High profit margins ({margins:.0%})")
        elif margins > 0.10:
            score += 5
            reasons.append(f"Decent profit margins ({margins:.0%})")
        elif margins < 0:
            score -= 10
            reasons.append(f"Negative margins ({margins:.0%})")

    # Debt to equity
    de = fundamentals.get("debtToEquity")
    if de is not None:
        if de < 50:
            score += 10
            reasons.append(f"Low debt/equity ({de:.0f}%)")
        elif de > 150:
            score -= 10
            reasons.append(f"High debt/equity ({de:.0f}%)")

    # ROE
    roe = fundamentals.get("returnOnEquity")
    if roe is not None:
        if roe > 0.20:
            score += 10
            reasons.append(f"Strong ROE ({roe:.0%})")
        elif roe > 0.10:
            score += 5
            reasons.append(f"Decent ROE ({roe:.0%})")
        elif roe < 0:
            score -= 10
            reasons.append(f"Negative ROE ({roe:.0%})")

    # Analyst target vs current price
    current = fundamentals.get("currentPrice")
    target = fundamentals.get("targetMeanPrice")
    if current and target:
        upside = (target - current) / current
        if upside > 0.20:
            score += 15
            reasons.append(f"Analyst upside {upside:.0%} (target ${target:.2f})")
        elif upside > 0.05:
            score += 5
            reasons.append(f"Analyst upside {upside:.0%} (target ${target:.2f})")
        elif upside < -0.10:
            score -= 10
            reasons.append(f"Analyst downside {upside:.0%} (target ${target:.2f})")

    return max(-100, min(100, score)), reasons


def get_recommendation(
    price_history: pd.DataFrame, fundamentals: dict
) -> dict:
    """Generate an investment recommendation.

    Returns:
        dict with keys: signal, score, technical_score, fundamental_score,
                       technical_reasons, fundamental_reasons
    """
    tech_score, tech_reasons = _score_technicals(price_history)
    fund_score, fund_reasons = _score_fundamentals(fundamentals)

    # Combined score: 50% technical, 50% fundamental
    combined = (tech_score + fund_score) / 2

    if combined >= 25:
        signal = "Strong Buy"
    elif combined >= 10:
        signal = "Buy"
    elif combined >= -10:
        signal = "Hold"
    elif combined >= -25:
        signal = "Sell"
    else:
        signal = "Strong Sell"

    return {
        "signal": signal,
        "score": round(combined, 1),
        "technical_score": round(tech_score, 1),
        "fundamental_score": round(fund_score, 1),
        "technical_reasons": tech_reasons,
        "fundamental_reasons": fund_reasons,
    }
