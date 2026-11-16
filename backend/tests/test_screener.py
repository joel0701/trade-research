import numpy as np
import pandas as pd

from app.screeners.engine import evaluate, results_to_markdown, screen
from app.tools.indicators import rsi, snapshot


def make_df(prices):
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    p = pd.Series(prices, index=idx, dtype=float)
    return pd.DataFrame({"Open": p, "High": p * 1.01, "Low": p * 0.99, "Close": p, "Volume": 1_000_000.0})


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(up).iloc[-1] == 100.0
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert rsi(down).iloc[-1] < 1


def test_snapshot_fields():
    s = snapshot(make_df(np.linspace(100, 200, 300)))
    assert s["close"] == 200
    assert s["sma_200"] < s["sma_50"] < s["close"]
    assert s["pct_from_52w_high"] == 0
    assert s["rel_volume"] == 1


def test_evaluate_rules():
    row = {"close": 110, "sma_200": 100, "rsi_14": 35, "missing": None}
    assert evaluate({"all": [{"field": "close", "op": ">", "field2": "sma_200"},
                             {"field": "rsi_14", "op": "<", "value": 40}]}, row)
    assert not evaluate({"field": "rsi_14", "op": ">", "value": 70}, row)
    assert evaluate({"any": [{"field": "rsi_14", "op": ">", "value": 70},
                             {"field": "close", "op": "between", "value": [100, 120]}]}, row)
    assert evaluate({"not": {"field": "rsi_14", "op": ">", "value": 70}}, row)
    assert not evaluate({"field": "missing", "op": ">", "value": 0}, row)


def test_screen_sort_and_limit():
    snaps = {"A": {"rsi_14": 30}, "B": {"rsi_14": 20}, "C": {"rsi_14": 50}, "D": {"rsi_14": None}}
    rows = screen(snaps, {"field": "rsi_14", "op": "<", "value": 45}, sort_by="rsi_14", limit=5)
    assert [r["ticker"] for r in rows] == ["B", "A"]
    rows = screen(snaps, {}, sort_by="-rsi_14", limit=2)
    assert [r["ticker"] for r in rows] == ["C", "A"]
    assert "| A |" in results_to_markdown(rows, ["ticker"])
