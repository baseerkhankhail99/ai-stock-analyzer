from typing import Iterable, Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP = "#10b981"
DOWN = "#ef4444"
ACCENT = "#3b82f6"
NEUTRAL = "#94a3b8"
MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _frame(data) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if data is None:
        return pd.DataFrame()
    try:
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()


def _series(data) -> pd.Series:
    if isinstance(data, pd.Series):
        return data.copy()
    if data is None:
        return pd.Series(dtype=float)
    try:
        return pd.Series(data)
    except Exception:
        return pd.Series(dtype=float)


def _x_values(data) -> list:
    if isinstance(data, pd.DataFrame):
        if "date" in data.columns:
            return pd.to_datetime(data["date"], errors="coerce").tolist()
        return pd.to_datetime(data.index, errors="coerce").tolist()
    series = _series(data)
    return pd.to_datetime(series.index, errors="coerce").tolist()


def _non_empty_title(value: Optional[str], fallback: str) -> str:
    return value or fallback


def apply_theme(
    fig: go.Figure,
    title: str,
    height: int,
    xaxis_title: str,
    yaxis_title: str,
    uirevision: str = "keep",
) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title={"text": title or "Chart", "x": 0.01, "xanchor": "left"},
        height=height,
        margin={"l": 60, "r": 30, "t": 105, "b": 60},
        legend={
            "orientation": "h",
            "y": 1.16,
            "yanchor": "bottom",
            "x": 0,
            "xanchor": "left",
        },
        hovermode="x unified",
        uirevision=uirevision,
        autosize=True,
    )
    fig.update_xaxes(
        title_text=_non_empty_title(xaxis_title, "Date"),
        automargin=True,
        showgrid=True,
        gridcolor="rgba(148,163,184,0.15)",
        tickformat="%Y-%m-%d",
    )
    fig.update_yaxes(
        title_text=_non_empty_title(yaxis_title, "Value"),
        automargin=True,
        showgrid=True,
        gridcolor="rgba(148,163,184,0.15)",
        tickformat=",.2f",
    )
    return fig


def _empty_figure(
    title: str,
    xaxis_title: str,
    yaxis_title: str,
    message: str = "No data available",
    height: int = 360,
    uirevision: str = "keep",
) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper"
    )
    apply_theme(fig, title, height, xaxis_title, yaxis_title, uirevision=uirevision)
    return fig


def _column(frame: pd.DataFrame, *names: str) -> Optional[str]:
    for name in names:
        if name in frame.columns:
            return name
    return None


def _price_frame(data) -> pd.DataFrame:
    frame = _frame(data)
    if frame.empty:
        return frame
    frame.columns = [str(col).lower() for col in frame.columns]
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.set_index("date")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")
    return frame.sort_index()


def price_chart(
    frame,
    symbol,
    overlays: Sequence[str] = (),
    log: bool = False,
    support: Sequence[float] = (),
    resistance: Sequence[float] = (),
    live_price: Optional[float] = None,
) -> go.Figure:
    price = _price_frame(frame)
    if price.empty or any(
        col not in price.columns for col in ("open", "high", "low", "close")
    ):
        return _empty_figure(f"{symbol} Price", "Date", "Price (USD)")

    has_volume = "volume" in price.columns and price["volume"].notna().any()
    fig = make_subplots(
        rows=2 if has_volume else 1,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3] if has_volume else None,
        vertical_spacing=0.06,
    )
    fig.add_trace(
        go.Candlestick(
            x=price.index,
            open=price["open"],
            high=price["high"],
            low=price["low"],
            close=price["close"],
            increasing_line_color=UP,
            decreasing_line_color=DOWN,
            name=symbol or "Price",
            text=[
                (
                    f"Date {stamp:%Y-%m-%d}<br>"
                    f"Open ${open_:,.2f}<br>High ${high:,.2f}<br>"
                    f"Low ${low:,.2f}<br>Close ${close:,.2f}"
                )
                for stamp, open_, high, low, close in zip(
                    price.index,
                    price["open"],
                    price["high"],
                    price["low"],
                    price["close"],
                )
            ],
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )

    overlay_map = {
        "sma20": ("sma20", "sma_20", "SMA 20", "#f59e0b"),
        "sma50": ("sma50", "sma_50", "SMA 50", ACCENT),
        "bollinger": ("bb_upper", "bb_lower", "Bollinger", NEUTRAL),
    }
    selected = {str(item).lower().replace("_", "") for item in overlays or ()}
    if "sma20" in selected:
        column = _column(price, overlay_map["sma20"][0], overlay_map["sma20"][1])
        if column:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[column],
                    name="SMA 20",
                    line={"color": "#f59e0b"},
                ),
                row=1,
                col=1,
            )
    if "sma50" in selected:
        column = _column(price, overlay_map["sma50"][0], overlay_map["sma50"][1])
        if column:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[column],
                    name="SMA 50",
                    line={"color": ACCENT},
                ),
                row=1,
                col=1,
            )
    if "bb" in selected or "bollinger" in selected:
        upper = _column(price, "bb_upper")
        lower = _column(price, "bb_lower")
        if upper:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[upper],
                    name="BB Upper",
                    line={"color": NEUTRAL, "dash": "dot"},
                ),
                row=1,
                col=1,
            )
        if lower:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[lower],
                    name="BB Lower",
                    line={"color": NEUTRAL, "dash": "dot"},
                    fill="tonexty",
                    fillcolor="rgba(148,163,184,0.08)",
                ),
                row=1,
                col=1,
            )

    for level in support or ():
        fig.add_hline(
            y=level, line_dash="dot", line_color=UP, opacity=0.5, row=1, col=1
        )
    for level in resistance or ():
        fig.add_hline(
            y=level, line_dash="dot", line_color=DOWN, opacity=0.5, row=1, col=1
        )
    if live_price is not None:
        fig.add_hline(
            y=live_price, line_dash="dash", line_color=ACCENT, opacity=0.8, row=1, col=1
        )

    if has_volume:
        colors = [
            UP if close >= open_ else DOWN
            for close, open_ in zip(price["close"], price["open"])
        ]
        fig.add_trace(
            go.Bar(
                x=price.index,
                y=price["volume"],
                name="Volume",
                marker_color=colors,
                opacity=0.45,
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text="Volume", row=2, col=1, tickformat=",.0f")
        fig.update_xaxes(title_text="Date", row=2, col=1)
    else:
        fig.update_xaxes(title_text="Date", row=1, col=1)

    fig.update_yaxes(
        title_text="Price (USD)", row=1, col=1, type="log" if log else "linear"
    )
    fig.update_xaxes(
        rangeselector={
            "buttons": [
                {"count": 1, "label": "1M", "step": "month", "stepmode": "backward"},
                {"count": 3, "label": "3M", "step": "month", "stepmode": "backward"},
                {"count": 6, "label": "6M", "step": "month", "stepmode": "backward"},
                {"count": 1, "label": "1Y", "step": "year", "stepmode": "backward"},
                {"step": "all", "label": "All"},
            ]
        },
        rangeslider_visible=False,
        row=1,
        col=1,
    )
    apply_theme(fig, f"{symbol} Price", 620, "Date", "Price (USD)")
    return fig


def indicator_chart(frame, kind) -> go.Figure:
    price = _price_frame(frame)
    kind = (kind or "").lower()
    titles = {
        "rsi": ("RSI", "Date", "RSI"),
        "macd": ("MACD", "Date", "MACD"),
        "bollinger": ("Bollinger Bands", "Date", "Price (USD)"),
    }
    title, xaxis_title, yaxis_title = titles.get(kind, ("Indicator", "Date", "Value"))
    if price.empty or "close" not in price.columns:
        return _empty_figure(title, xaxis_title, yaxis_title)

    if kind == "rsi":
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.55, 0.45],
            vertical_spacing=0.08,
        )
        fig.add_trace(
            go.Scatter(
                x=price.index, y=price["close"], name="Close", line={"color": ACCENT}
            ),
            row=1,
            col=1,
        )
        rsi_col = _column(price, "rsi")
        if rsi_col:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[rsi_col],
                    name="RSI",
                    line={"color": "#f59e0b"},
                ),
                row=2,
                col=1,
            )
        fig.add_hline(
            y=70, line_dash="dash", line_color=DOWN, opacity=0.6, row=2, col=1
        )
        fig.add_hline(y=30, line_dash="dash", line_color=UP, opacity=0.6, row=2, col=1)
        fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
        fig.update_yaxes(title_text="RSI", row=2, col=1)
        fig.update_xaxes(title_text="Date", row=2, col=1)
        apply_theme(fig, title, 520, "Date", "Price (USD)")
        return fig

    if kind == "macd":
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.55, 0.45],
            vertical_spacing=0.08,
        )
        fig.add_trace(
            go.Scatter(
                x=price.index, y=price["close"], name="Close", line={"color": ACCENT}
            ),
            row=1,
            col=1,
        )
        macd_col = _column(price, "macd")
        signal_col = _column(price, "macd_signal", "signal")
        hist_col = _column(price, "macd_hist", "histogram")
        if hist_col:
            colors = [UP if value >= 0 else DOWN for value in price[hist_col].fillna(0)]
            fig.add_trace(
                go.Bar(
                    x=price.index,
                    y=price[hist_col],
                    name="Histogram",
                    marker_color=colors,
                    opacity=0.45,
                ),
                row=2,
                col=1,
            )
        if macd_col:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[macd_col],
                    name="MACD",
                    line={"color": "#f59e0b"},
                ),
                row=2,
                col=1,
            )
        if signal_col:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[signal_col],
                    name="Signal",
                    line={"color": NEUTRAL},
                ),
                row=2,
                col=1,
            )
        fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
        fig.update_yaxes(title_text="MACD", row=2, col=1)
        fig.update_xaxes(title_text="Date", row=2, col=1)
        apply_theme(fig, title, 520, "Date", "Price (USD)")
        return fig

    if kind == "bollinger":
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=price.index, y=price["close"], name="Close", line={"color": ACCENT}
            )
        )
        upper = _column(price, "bb_upper")
        middle = _column(price, "bb_middle", "sma20", "sma_20")
        lower = _column(price, "bb_lower")
        if upper:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[upper],
                    name="BB Upper",
                    line={"color": NEUTRAL, "dash": "dot"},
                )
            )
        if middle:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[middle],
                    name="BB Middle",
                    line={"color": "#f59e0b"},
                )
            )
        if lower:
            fig.add_trace(
                go.Scatter(
                    x=price.index,
                    y=price[lower],
                    name="BB Lower",
                    line={"color": NEUTRAL, "dash": "dot"},
                    fill="tonexty",
                    fillcolor="rgba(148,163,184,0.08)",
                )
            )
        apply_theme(fig, title, 420, "Date", "Price (USD)")
        return fig

    return _empty_figure(
        title, xaxis_title, yaxis_title, message="Unsupported indicator"
    )


def compare_chart(closes_df, rebase: float = 100) -> go.Figure:
    closes = _frame(closes_df)
    if closes.empty:
        return _empty_figure("Performance Comparison", "Date", "Indexed Value")
    if "date" in closes.columns:
        closes["date"] = pd.to_datetime(closes["date"], errors="coerce")
        closes = closes.set_index("date")
    else:
        closes.index = pd.to_datetime(closes.index, errors="coerce")
    fig = go.Figure()
    plotted = 0
    for column in closes.columns:
        series = pd.to_numeric(closes[column], errors="coerce").dropna()
        if series.empty:
            continue
        first = series.iloc[0]
        if first == 0:
            continue
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=(series / first) * rebase,
                mode="lines",
                name=str(column),
            )
        )
        plotted += 1
    if not plotted:
        return _empty_figure("Performance Comparison", "Date", "Indexed Value")
    apply_theme(
        fig,
        "Performance Comparison",
        420,
        "Date",
        f"Rebased ({rebase:g}=100)" if rebase != 100 else "Rebased Value",
    )
    return fig


def correlation_heatmap(corr_df) -> go.Figure:
    corr = _frame(corr_df)
    if corr.empty:
        return _empty_figure("Correlation Heatmap", "Asset", "Asset")
    text = corr.round(2).astype(str).values
    fig = go.Figure(
        go.Heatmap(
            z=corr.values,
            x=[str(col) for col in corr.columns],
            y=[str(idx) for idx in corr.index],
            colorscale="RdBu",
            zmin=-1,
            zmax=1,
            text=text,
            texttemplate="%{text}",
            hovertemplate="%{y} vs %{x}: %{z:.2f}<extra></extra>",
        )
    )
    apply_theme(fig, "Correlation Heatmap", 420, "Asset", "Asset")
    fig.update_yaxes(autorange="reversed")
    return fig


def drawdown_chart(close) -> go.Figure:
    series = pd.to_numeric(_series(close), errors="coerce").dropna()
    series.index = pd.to_datetime(series.index, errors="coerce")
    if series.empty:
        return _empty_figure("Drawdown", "Date", "Drawdown (%)")
    drawdown = ((series / series.cummax()) - 1.0) * 100.0
    fig = go.Figure(
        go.Scatter(
            x=drawdown.index,
            y=drawdown,
            fill="tozeroy",
            name="Drawdown",
            line={"color": DOWN},
        )
    )
    apply_theme(fig, "Drawdown", 360, "Date", "Drawdown (%)")
    return fig


def monthly_returns_heatmap(close) -> go.Figure:
    series = pd.to_numeric(_series(close), errors="coerce").dropna()
    series.index = pd.to_datetime(series.index, errors="coerce")
    if len(series) < 2:
        return _empty_figure("Monthly Returns", "Month", "Year")
    monthly = series.resample("ME").last().pct_change().dropna() * 100.0
    if monthly.empty:
        return _empty_figure("Monthly Returns", "Month", "Year")
    table = pd.DataFrame(
        {
            "year": monthly.index.year,
            "month": monthly.index.month,
            "value": monthly.values,
        }
    )
    pivot = table.pivot(index="year", columns="month", values="value").sort_index()
    pivot = pivot.reindex(columns=range(1, 13))
    text = (
        pivot.astype(object)
        .apply(
            lambda column: column.map(
                lambda value: "" if pd.isna(value) else f"{value:.1f}%"
            )
        )
        .values
    )
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=MONTHS,
            y=[str(year) for year in pivot.index],
            colorscale="RdBu",
            zmid=0,
            text=text,
            texttemplate="%{text}",
            hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
        )
    )
    apply_theme(fig, "Monthly Returns", 420, "Month", "Year")
    return fig


def returns_histogram(close, var_pct: Optional[float] = None) -> go.Figure:
    series = pd.to_numeric(_series(close), errors="coerce").dropna()
    series.index = pd.to_datetime(series.index, errors="coerce")
    returns = series.pct_change().dropna() * 100.0
    if returns.empty:
        return _empty_figure("Return Distribution", "Daily Return (%)", "Frequency")
    fig = go.Figure(
        go.Histogram(x=returns, nbinsx=40, marker_color=ACCENT, name="Returns")
    )
    if var_pct is not None:
        fig.add_vline(x=var_pct, line_dash="dash", line_color=DOWN, opacity=0.8)
    apply_theme(fig, "Return Distribution", 360, "Daily Return (%)", "Frequency")
    fig.update_yaxes(tickformat=",.0f")
    return fig


def forecast_chart(
    history_close,
    forecast_dates,
    median,
    lower,
    upper,
    backtest: Optional[Iterable[tuple]] = None,
    symbol: str = "",
) -> go.Figure:
    history = pd.to_numeric(_series(history_close), errors="coerce").dropna()
    history.index = pd.to_datetime(history.index, errors="coerce")
    date_values = [] if forecast_dates is None else list(forecast_dates)
    dates = list(pd.to_datetime(date_values, errors="coerce"))
    median_values = list(median or [])
    lower_values = list(lower or [])
    upper_values = list(upper or [])
    points = min(len(dates), len(median_values), len(lower_values), len(upper_values))
    if history.empty and points == 0:
        return _empty_figure(
            f"{symbol} Forecast".strip() or "Forecast", "Date", "Price (USD)"
        )

    fig = go.Figure()
    if not history.empty:
        fig.add_trace(
            go.Scatter(
                x=history.index,
                y=history.values,
                mode="lines",
                name="History",
                line={"color": NEUTRAL},
            )
        )
    if points:
        band_x = dates[:points] + list(reversed(dates[:points]))
        band_y = list(upper_values[:points]) + list(reversed(lower_values[:points]))
        fig.add_trace(
            go.Scatter(
                x=band_x,
                y=band_y,
                fill="toself",
                fillcolor="rgba(59,130,246,0.12)",
                line={"width": 0},
                name="Interval",
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=dates[:points],
                y=median_values[:points],
                mode="lines",
                name="Median",
                line={"color": ACCENT, "width": 2},
            )
        )
    if backtest:
        backtest_frame = pd.DataFrame(list(backtest), columns=["date", "predicted"])
        if not backtest_frame.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_datetime(backtest_frame["date"], errors="coerce"),
                    y=pd.to_numeric(backtest_frame["predicted"], errors="coerce"),
                    mode="lines+markers",
                    name="Backtest",
                    line={"color": "#f59e0b", "dash": "dot"},
                )
            )
    apply_theme(
        fig, f"{symbol} Forecast".strip() or "Forecast", 420, "Date", "Price (USD)"
    )
    return fig


def signal_gauge(score, label) -> go.Figure:
    value = max(0.0, min(100.0, float(score or 0)))
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            title={"text": f"Signal: {label or 'neutral'}"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": ACCENT},
                "steps": [
                    {"range": [0, 40], "color": "rgba(239,68,68,0.35)"},
                    {"range": [40, 60], "color": "rgba(148,163,184,0.30)"},
                    {"range": [60, 100], "color": "rgba(16,185,129,0.35)"},
                ],
            },
        )
    )
    apply_theme(fig, "Signal Gauge", 260, "Score", "Signal")
    fig.update_xaxes(visible=False, title_text="Score")
    fig.update_yaxes(visible=False, title_text="Signal")
    return fig


def sparkline(values, change) -> go.Figure:
    points = list(values or [0])
    color = UP if (change or 0) >= 0 else DOWN
    fig = go.Figure(
        go.Scatter(
            y=points,
            mode="lines",
            line={"color": color, "width": 2},
            hoverinfo="skip",
            name="Sparkline",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=60,
        xaxis={"visible": False},
        yaxis={"visible": False},
        showlegend=False,
    )
    return fig


def intraday_chart(frame, symbol) -> go.Figure:
    intraday = _price_frame(frame)
    if intraday.empty or any(
        col not in intraday.columns for col in ("open", "high", "low", "close")
    ):
        return _empty_figure(f"{symbol} Intraday", "Time", "Price (USD)")
    fig = go.Figure(
        go.Candlestick(
            x=intraday.index,
            open=intraday["open"],
            high=intraday["high"],
            low=intraday["low"],
            close=intraday["close"],
            increasing_line_color=UP,
            decreasing_line_color=DOWN,
            name=symbol or "Intraday",
        )
    )
    apply_theme(fig, f"{symbol} Intraday", 380, "Time", "Price (USD)")
    fig.update_xaxes(tickformat="%H:%M")
    return fig
