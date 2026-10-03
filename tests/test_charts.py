import pandas as pd
import plotly.graph_objects as go
from helpers import ensure, ensure_equal

from services import charts


def _price_frame(rows=90, freq="D"):
    index = pd.date_range("2024-01-01", periods=rows, freq=freq)
    close = pd.Series(range(100, 100 + rows), index=index, dtype=float)
    frame = pd.DataFrame(
        {
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": 1000 + (close * 10),
            "rsi": 45 + (close % 20),
            "macd": 0.5,
            "macd_signal": 0.3,
            "macd_hist": 0.2,
            "bb_upper": close + 3,
            "bb_lower": close - 3,
            "bb_middle": close,
            "sma20": close.rolling(20, min_periods=1).mean(),
            "sma50": close.rolling(50, min_periods=1).mean(),
        }
    )
    return frame


def _close_series(rows=420):
    index = pd.date_range("2023-01-01", periods=rows, freq="D")
    return pd.Series(range(100, 100 + rows), index=index, dtype=float)


def _ensure_axes(fig):
    ensure(isinstance(fig, go.Figure))
    ensure(bool(fig.layout.xaxis.title.text))
    ensure(bool(fig.layout.yaxis.title.text))


def _ensure_heatmap_text(fig):
    ensure(any(getattr(trace, "text", None) is not None for trace in fig.data))


def test_chart_builders_return_figures_with_titles_and_horizontal_legends():
    frame = _price_frame()
    closes = pd.DataFrame({"AAPL": frame["close"], "MSFT": frame["close"] * 1.03})
    corr = closes.pct_change().dropna().corr()
    close = _close_series()
    forecast_dates = pd.date_range("2025-01-01", periods=5, freq="D")

    figures = [
        charts.price_chart(
            frame,
            "AAPL",
            overlays=("sma20", "sma50", "bb"),
            support=(95,),
            resistance=(135,),
            live_price=140,
        ),
        charts.indicator_chart(frame, "rsi"),
        charts.indicator_chart(frame, "macd"),
        charts.indicator_chart(frame, "bollinger"),
        charts.compare_chart(closes),
        charts.correlation_heatmap(corr),
        charts.drawdown_chart(close),
        charts.monthly_returns_heatmap(close),
        charts.returns_histogram(close, var_pct=-2.5),
        charts.forecast_chart(
            close.tail(30),
            forecast_dates,
            [140, 141, 142, 143, 144],
            [138, 139, 140, 141, 142],
            [142, 143, 144, 145, 146],
            backtest=[("2024-12-28", 139)],
            symbol="AAPL",
        ),
        charts.signal_gauge(72, "positive"),
        charts.intraday_chart(_price_frame(24, freq="15min"), "AAPL"),
    ]

    for fig in figures:
        _ensure_axes(fig)
        ensure_equal(fig.layout.legend.orientation, "h")

    _ensure_heatmap_text(figures[5])
    _ensure_heatmap_text(figures[7])
    ensure(isinstance(charts.sparkline([1, 2, 3], 1.2), go.Figure))


def test_chart_builders_handle_empty_and_short_inputs_without_exceptions():
    empty_frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    short_frame = _price_frame(1)
    empty_close = pd.Series(dtype=float)
    short_close = _close_series(1)
    short_dates = pd.date_range("2025-01-01", periods=1, freq="D")
    short_compare = pd.DataFrame({"AAPL": [100.0]}, index=short_dates)
    short_corr = pd.DataFrame([[1.0]], index=["AAPL"], columns=["AAPL"])

    figures = [
        charts.price_chart(empty_frame, "AAPL"),
        charts.price_chart(short_frame, "AAPL"),
        charts.indicator_chart(empty_frame, "rsi"),
        charts.indicator_chart(short_frame, "macd"),
        charts.compare_chart(pd.DataFrame()),
        charts.compare_chart(short_compare),
        charts.correlation_heatmap(pd.DataFrame()),
        charts.correlation_heatmap(short_corr),
        charts.drawdown_chart(empty_close),
        charts.drawdown_chart(short_close),
        charts.monthly_returns_heatmap(empty_close),
        charts.monthly_returns_heatmap(short_close),
        charts.returns_histogram(empty_close),
        charts.returns_histogram(short_close),
        charts.forecast_chart(empty_close, [], [], [], [], symbol="AAPL"),
        charts.forecast_chart(
            short_close, short_dates, [100.0], [99.0], [101.0], symbol="AAPL"
        ),
        charts.signal_gauge(None, None),
        charts.intraday_chart(empty_frame, "AAPL"),
        charts.intraday_chart(short_frame, "AAPL"),
    ]

    for fig in figures:
        _ensure_axes(fig)
        ensure_equal(fig.layout.legend.orientation, "h")
