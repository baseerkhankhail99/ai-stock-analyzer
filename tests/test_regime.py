import numpy as np
import pandas as pd
from helpers import ensure, ensure_equal

from services.regime import classify_regime, support_resistance


def _price_frame(close_values):
    index = pd.date_range("2024-01-01", periods=len(close_values), freq="D")
    close = pd.Series(close_values, index=index)
    return pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
        },
        index=index,
    )


def test_classify_regime_detects_uptrend_downtrend_and_range():
    steps = np.linspace(100, 180, 260)
    uptrend = _price_frame(steps + np.sin(np.linspace(0, 10, 260)))
    downtrend = _price_frame(steps[::-1] + np.sin(np.linspace(0, 10, 260)))
    range_frame = _price_frame(120 + np.sin(np.linspace(0, 24, 260)) * 3)

    up = classify_regime(uptrend)
    down = classify_regime(downtrend)
    sideways = classify_regime(range_frame)

    ensure_equal(up["trend"], "uptrend")
    ensure_equal(down["trend"], "downtrend")
    ensure_equal(sideways["trend"], "range")
    ensure(up["returns"]["30d"] is not None)
    ensure(down["atr"] is not None)
    ensure(sideways["rsi"] is not None)


def test_support_resistance_levels_wrap_price_and_sort_by_proximity():
    close = [100, 104, 98, 106, 99, 108, 101, 110, 103, 107]
    frame = _price_frame(close)
    frame.loc[frame.index[3], "high"] = 113
    frame.loc[frame.index[4], "low"] = 96
    frame.loc[frame.index[7], "high"] = 115
    frame.loc[frame.index[8], "low"] = 97

    levels = support_resistance(frame, n=3)
    last_close = float(frame["close"].iloc[-1])

    ensure(levels["support"])
    ensure(levels["resistance"])
    ensure(all(level < last_close for level in levels["support"]))
    ensure(all(level > last_close for level in levels["resistance"]))
    ensure(
        levels["support"]
        == sorted(levels["support"], key=lambda x: abs(last_close - x))
    )
    ensure(
        levels["resistance"]
        == sorted(levels["resistance"], key=lambda x: abs(last_close - x))
    )


def test_empty_and_short_frames_return_safe_defaults():
    empty = classify_regime(
        pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    )
    short = classify_regime(_price_frame([100, 101, 102, 103, 104]))

    ensure_equal(
        empty,
        {
            "trend": None,
            "momentum": None,
            "volatility": None,
            "atr": None,
            "atr_pct": None,
            "rsi": None,
            "macd_hist": None,
            "bollinger_pct_b": None,
            "dist_52w_high_pct": None,
            "dist_52w_low_pct": None,
            "sma_cross": None,
            "returns": {"1d": None, "7d": None, "30d": None, "90d": None},
        },
    )
    ensure_equal(short["trend"], None)
    ensure_equal(short["returns"]["1d"], 0.9709)
    ensure_equal(short["returns"]["7d"], None)
    ensure_equal(
        support_resistance(pd.DataFrame(), n=3), {"support": [], "resistance": []}
    )
