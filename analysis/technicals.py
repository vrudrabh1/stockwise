"""Technical analysis indicators."""

import pandas as pd


def compute_sma(df: pd.DataFrame, window: int, column: str = "Close") -> pd.Series:
    """Simple Moving Average."""
    return df[column].rolling(window=window).mean()


def compute_ema(df: pd.DataFrame, span: int, column: str = "Close") -> pd.Series:
    """Exponential Moving Average."""
    return df[column].ewm(span=span, adjust=False).mean()


def compute_rsi(df: pd.DataFrame, period: int = 14, column: str = "Close") -> pd.Series:
    """Relative Strength Index."""
    delta = df[column].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = "Close"
) -> pd.DataFrame:
    """MACD (Moving Average Convergence Divergence).

    Returns DataFrame with columns: MACD, Signal, Histogram.
    """
    ema_fast = df[column].ewm(span=fast, adjust=False).mean()
    ema_slow = df[column].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "MACD": macd_line,
        "Signal": signal_line,
        "Histogram": histogram,
    })


def compute_bollinger_bands(
    df: pd.DataFrame, window: int = 20, num_std: float = 2.0, column: str = "Close"
) -> pd.DataFrame:
    """Bollinger Bands.

    Returns DataFrame with columns: BB_Upper, BB_Middle, BB_Lower.
    """
    middle = df[column].rolling(window=window).mean()
    std = df[column].rolling(window=window).std()

    return pd.DataFrame({
        "BB_Upper": middle + (std * num_std),
        "BB_Middle": middle,
        "BB_Lower": middle - (std * num_std),
    })


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators and add them to the dataframe."""
    result = df.copy()

    # Moving averages
    result["SMA_20"] = compute_sma(df, 20)
    result["SMA_50"] = compute_sma(df, 50)
    result["SMA_200"] = compute_sma(df, 200)
    result["EMA_12"] = compute_ema(df, 12)
    result["EMA_26"] = compute_ema(df, 26)

    # RSI
    result["RSI"] = compute_rsi(df)

    # MACD
    macd = compute_macd(df)
    result["MACD"] = macd["MACD"]
    result["MACD_Signal"] = macd["Signal"]
    result["MACD_Histogram"] = macd["Histogram"]

    # Bollinger Bands
    bb = compute_bollinger_bands(df)
    result["BB_Upper"] = bb["BB_Upper"]
    result["BB_Middle"] = bb["BB_Middle"]
    result["BB_Lower"] = bb["BB_Lower"]

    return result
