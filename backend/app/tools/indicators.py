"""Technical indicators computed with pandas. Pure functions, easy to test."""
import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift()
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def snapshot(df: pd.DataFrame) -> dict[str, float | None]:
    """Latest values of the fields screeners and analysts use, from daily OHLCV."""
    close, vol = df["Close"], df["Volume"]

    def last(s: pd.Series) -> float | None:
        s = s.dropna()
        return None if s.empty else round(float(s.iloc[-1]), 4)

    def pct(n: int) -> float | None:
        return None if len(close) <= n else round(float(close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2)

    m = macd(close)
    hi52, lo52 = close.tail(252).max(), close.tail(252).min()
    price = float(close.iloc[-1])
    return {
        "close": round(price, 4),
        "volume": last(vol),
        "avg_volume_20": last(vol.rolling(20).mean()),
        "rel_volume": last(vol / vol.rolling(20).mean()),
        "sma_20": last(close.rolling(20).mean()),
        "sma_50": last(close.rolling(50).mean()),
        "sma_200": last(close.rolling(200).mean()),
        "rsi_14": last(rsi(close)),
        "macd": last(m["macd"]),
        "macd_signal": last(m["macd_signal"]),
        "macd_hist": last(m["macd_hist"]),
        "atr_14": last(atr(df)),
        "pct_change_1d": pct(1),
        "pct_change_5d": pct(5),
        "pct_change_20d": pct(20),
        "high_52w": round(float(hi52), 4),
        "low_52w": round(float(lo52), 4),
        "pct_from_52w_high": round((price / hi52 - 1) * 100, 2),
    }
