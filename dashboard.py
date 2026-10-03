import logging
import os
from datetime import datetime
from urllib.parse import parse_qs, quote

import dash
import plotly.graph_objects as go
import requests
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots

from services.market_analysis import INDICATOR_TIPS
from services.market_data import ASSET_NAMES

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
REFRESH_INTERVAL_MS = 45_000
LIVE_MAX_AGE_SECONDS = 60
DISCLAIMER_FOOTER = "Data may be delayed 10-15 minutes. Not financial advice."
FORECAST_DISCLAIMER = (
    "Not financial advice; forecasts are statistical estimates and can be unreliable."
)

DASH_BASE_PATH = os.getenv("DASH_BASE_PATH", "/dashboard/")

CATEGORY_TITLES = {
    "commodities": "🛢️ Commodities",
    "cryptocurrencies": "₿ Cryptocurrencies",
    "stocks": "📈 Stocks",
    "indices": "🏛️ Indices",
}
RANGES = ["1D", "5D", "1M", "6M", "1Y", "5Y"]
GRAPH_CONFIG = {"displaylogo": False, "responsive": True}
UP, DOWN, ACCENT = "#10b981", "#ef4444", "#3b82f6"

# Initialize Dash app (attached to a Flask server later via init_dashboard)
app = dash.Dash(
    __name__,
    server=False,
    url_base_pathname=DASH_BASE_PATH,
    suppress_callback_exceptions=True,
    title="AI Market Analyzer",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)

# API base URL (same origin as the dashboard by default)
API_BASE_URL = os.getenv(
    "API_BASE_URL",
    f"http://127.0.0.1:{os.getenv('PORT', '7860')}/api",
)


def init_dashboard(flask_app):
    """Mount the dashboard on an existing Flask app."""
    app.init_app(flask_app)
    return app


def fetch_api_response(endpoint, params=None):
    """Fetch API responses with a bounded timeout for dashboard requests."""
    try:
        return requests.get(
            f"{API_BASE_URL}{endpoint}",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        logger.warning("Dashboard request to %s timed out", endpoint)
    except requests.RequestException as exc:
        logger.error("Dashboard request to %s failed: %s", endpoint, exc)

    return None


def api_json(endpoint, params=None):
    """Return the JSON body of a successful API call, otherwise None."""
    response = fetch_api_response(endpoint, params)
    if response is None or response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


# ---------------------------------------------------------------- formatting


def format_price(value):
    if value is None:
        return "—"
    return f"{value:,.4f}" if abs(value) < 1 else f"{value:,.2f}"


def change_class(change):
    if change is None or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def format_change(change, pct):
    if change is None or pct is None:
        return "—"
    sign = "+" if change > 0 else ""
    return f"{sign}{format_price(change)} ({sign}{pct:.2f}%)"


def parse_asset(search):
    """Extract the asset symbol from a URL query string such as ?asset=BTC-USD."""
    if not search:
        return None
    values = parse_qs(search.lstrip("?")).get("asset")
    return values[0].strip().upper() if values and values[0].strip() else None


def asset_name(symbol):
    return ASSET_NAMES.get(symbol, symbol)


def format_updated(timestamp):
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        return f"Last updated {parsed:%H:%M:%S} UTC"
    except (TypeError, ValueError):
        return "Last updated —"


def asset_href(symbol):
    return f"{DASH_BASE_PATH}?asset={quote(symbol, safe='')}"


# ------------------------------------------------------------------- figures


def style_figure(fig, height=380):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 40, "r": 20, "t": 40, "b": 30},
        autosize=True,
        legend={"orientation": "h", "y": 1.12},
        hovermode="x unified",
    )
    return fig


def empty_figure(message):
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return style_figure(fig, 260)


def sparkline_figure(values, change):
    color = UP if (change or 0) >= 0 else DOWN
    fig = go.Figure(
        go.Scatter(
            y=values,
            mode="lines",
            line={"color": color, "width": 2},
            hoverinfo="skip",
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


def momentum_gauge(summary):
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=summary["score"],
            title={"text": f"Market Momentum: {summary['label']}"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": ACCENT},
                "steps": [
                    {"range": [0, 40], "color": "rgba(239,68,68,0.35)"},
                    {"range": [40, 60], "color": "rgba(148,163,184,0.3)"},
                    {"range": [60, 100], "color": "rgba(16,185,129,0.35)"},
                ],
            },
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        height=240,
        margin={"l": 30, "r": 30, "t": 60, "b": 10},
    )
    return fig


# ---------------------------------------------------------------- components


def asset_card(item, updated_text):
    change = item.get("change")
    pct = item.get("change_pct")
    cls = change_class(change)
    if pct is None:
        badge = "—"
    else:
        badge = f"{'UP' if pct > 0 else 'DOWN' if pct < 0 else 'FLAT'} {abs(pct):.1f}%"
    spark = item.get("sparkline") or []
    graph = (
        dcc.Graph(
            figure=sparkline_figure(spark, change),
            config={"staticPlot": True, "displayModeBar": False},
            style={"height": "60px"},
        )
        if len(spark) > 1
        else html.Div("No chart data", className="asset-updated")
    )
    return dcc.Link(
        href=asset_href(item["symbol"]),
        className="glass asset-card",
        children=[
            html.Div(
                [
                    html.Span(item["name"], className="asset-name"),
                    html.Span(item["symbol"], className="asset-symbol"),
                ]
            ),
            html.Div(format_price(item.get("price")), className="asset-price"),
            html.Div(
                [
                    html.Span(format_change(change, pct), className=f"change {cls}"),
                    html.Span(badge, className=f"badge {cls}"),
                ]
            ),
            graph,
            html.Div(updated_text, className="asset-updated"),
        ],
    )


def build_home_grid(overview):
    """Category sections with a card grid each; failed assets render as '—'."""
    updated = format_updated(overview.get("timestamp"))
    sections = [
        html.P(
            f"⚠️ {overview.get('delayed_warning', 'Data may be delayed')}",
            className="warning-banner",
        )
    ]
    for category, title in CATEGORY_TITLES.items():
        items = overview.get(category) or []
        sections.append(html.H3(title, className="section-title"))
        sections.append(
            html.Div([asset_card(i, updated) for i in items], className="asset-grid")
        )
    return sections


def kpi(label, value, suffix=""):
    return html.Div(
        [
            html.Div(label, className="metric-label"),
            html.Div(f"{value}{suffix}", className="metric-value"),
        ]
    )


def simple_table(header, rows):
    return html.Table(
        [
            html.Thead(html.Tr([html.Th(h) for h in header])),
            html.Tbody([html.Tr([html.Td(c) for c in row]) for row in rows]),
        ],
        className="data-table",
    )


def known_options():
    return [
        {"label": f"{name} ({symbol})", "value": symbol}
        for symbol, name in ASSET_NAMES.items()
    ]


def with_free_text(search_value, selected=None):
    """Known assets plus whatever the user typed/selected (free-text symbols)."""
    options = known_options()
    known = {o["value"] for o in options}
    extras = list(selected or [])
    if search_value and search_value.strip():
        extras.append(search_value.strip().upper())
    for symbol in dict.fromkeys(extras):
        if symbol not in known:
            options.append({"label": symbol, "value": symbol})
    return options


# -------------------------------------------------------------------- layout

home_layout = html.Div(
    [
        html.Div(
            [
                html.Div(id="momentum-panel"),
            ],
            className="glass",
        ),
        dcc.Loading(html.Div(id="home-grid"), type="circle"),
    ]
)

app.layout = html.Div(
    [
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="overview-store"),
        dcc.Interval(id="refresh-interval", interval=REFRESH_INTERVAL_MS),
        dcc.Interval(id="clock-interval", interval=1000),
        html.Div(
            [
                html.Div(
                    [
                        dcc.Link(
                            "🤖 AI Market Analyzer",
                            href=DASH_BASE_PATH,
                            className="brand",
                            style={"textDecoration": "none", "color": "inherit"},
                        )
                    ]
                ),
                html.Div(
                    [
                        dcc.Link("🏠 Home", href=DASH_BASE_PATH, className="nav-btn"),
                        dcc.Dropdown(
                            id="asset-search",
                            options=known_options(),
                            placeholder="Search asset or type a symbol…",
                            className="search-box",
                        ),
                        html.Span("⚙️", title="Settings (coming soon)"),
                        html.Span(id="clock", className="clock"),
                        html.Span("📊", id="live-badge", className="live-badge"),
                    ],
                    className="header-tools",
                ),
            ],
            className="app-header",
        ),
        html.Div(id="page-content"),
        html.P(DISCLAIMER_FOOTER, className="footer-note"),
    ],
    className="app-shell",
)

app.clientside_callback(
    f"""
    function(n, data) {{
        var now = new Date();
        var clock = now.toLocaleTimeString([], {{hour12: false}});
        var text = '📊 Waiting for data…', cls = 'live-badge';
        if (data && data.timestamp) {{
            var age = Math.max(0, (now - new Date(data.timestamp)) / 1000);
            if (age < {LIVE_MAX_AGE_SECONDS}) {{
                text = '🔴 Live'; cls = 'live-badge live';
            }} else {{
                text = '📊 Updated ' + Math.floor(age / 60) + 'm ago';
            }}
        }}
        return [clock, text, cls];
    }}
    """,
    Output("clock", "children"),
    Output("live-badge", "children"),
    Output("live-badge", "className"),
    Input("clock-interval", "n_intervals"),
    Input("overview-store", "data"),
)


def detail_layout(symbol):
    tab_kwargs = {"className": "dash-tab", "selected_className": "dash-tab--selected"}
    return html.Div(
        [
            dcc.Link("← Back to Market", href=DASH_BASE_PATH, className="back-btn"),
            html.Div(
                [
                    html.H2(
                        f"{asset_name(symbol)} ({symbol})",
                        style={"margin": "16px 0 4px"},
                    ),
                    html.Div(id="detail-price"),
                ],
                className="glass",
                style={"marginTop": "12px"},
            ),
            dcc.Tabs(
                id="detail-tabs",
                value="tab-chart",
                parent_className="tab-bar",
                children=[
                    dcc.Tab(label="📊 Price Chart", value="tab-chart", **tab_kwargs),
                    dcc.Tab(label="🔮 Forecast", value="tab-forecast", **tab_kwargs),
                    dcc.Tab(label="🧪 Technical", value="tab-tech", **tab_kwargs),
                    dcc.Tab(label="📈 Analytics", value="tab-analytics", **tab_kwargs),
                    dcc.Tab(label="⚖️ Compare", value="tab-compare", **tab_kwargs),
                ],
            ),
            dcc.Loading(html.Div(id="tab-content", style={"paddingTop": "16px"})),
        ]
    )


# ----------------------------------------------------------------- callbacks


@app.callback(Output("page-content", "children"), Input("url", "search"))
def render_page(search):
    symbol = parse_asset(search)
    return detail_layout(symbol) if symbol else home_layout


@app.callback(
    Output("asset-search", "options"),
    Input("asset-search", "search_value"),
    State("asset-search", "value"),
)
def update_search_options(search_value, value):
    return with_free_text(search_value, [value] if value else None)


@app.callback(Output("url", "search"), Input("asset-search", "value"))
def navigate_to_asset(value):
    if not value:
        raise PreventUpdate
    return f"?asset={quote(value, safe='')}"


@app.callback(
    Output("overview-store", "data"), Input("refresh-interval", "n_intervals")
)
def refresh_overview(_):
    return api_json("/market/overview")


@app.callback(
    Output("home-grid", "children"),
    Input("overview-store", "data"),
    Input("url", "search"),
)
def update_home_grid(overview, _search):
    if not overview:
        return html.Div(
            "Market data is temporarily unavailable. Retrying automatically…",
            className="glass",
        )
    return build_home_grid(overview)


@app.callback(
    Output("momentum-panel", "children"),
    Input("url", "search"),
    Input("refresh-interval", "n_intervals"),
)
def update_momentum(_search, _n):
    summary = api_json("/market/momentum")
    if not summary:
        return html.Div("Market momentum unavailable right now.", className="flat")
    return [
        dcc.Graph(
            figure=momentum_gauge(summary),
            config={"displayModeBar": False, "responsive": True},
        ),
        html.P(
            f"{summary['pct_above_sma50']}% of assets above their 50-day SMA · "
            f"average RSI {summary['avg_rsi']} · "
            f"{summary['gainers']} gainers vs {summary['losers']} losers. "
            f"{summary['note']}",
            className="asset-updated",
        ),
    ]


@app.callback(
    Output("detail-price", "children"),
    Input("url", "search"),
    Input("refresh-interval", "n_intervals"),
)
def update_detail_price(search, _n):
    symbol = parse_asset(search)
    if not symbol:
        raise PreventUpdate
    body = api_json(f"/market/history/{quote(symbol, safe='')}", {"period": "1M"})
    rows = (body or {}).get("data") or []
    if not rows:
        return html.Div(
            "Unable to load data for this symbol. Check the ticker and try again.",
            className="down",
        )
    price = rows[-1]["close"]
    change = price - rows[-2]["close"] if len(rows) > 1 else 0
    pct = change / rows[-2]["close"] * 100 if len(rows) > 1 else 0
    cls = change_class(change)
    return [
        html.Span(format_price(price), className="asset-price"),
        html.Span(format_change(change, pct), className=f"change {cls}"),
    ]


@app.callback(
    Output("tab-content", "children"),
    Input("detail-tabs", "value"),
    Input("url", "search"),
)
def render_tab(tab, search):
    symbol = parse_asset(search)
    if not symbol:
        raise PreventUpdate
    if tab == "tab-chart":
        return chart_tab()
    if tab == "tab-forecast":
        return forecast_tab()
    if tab == "tab-tech":
        return technical_tab(symbol)
    if tab == "tab-analytics":
        return analytics_tab(symbol)
    return compare_tab(symbol)


# ------------------------------------------------------------------ chart tab


def chart_tab():
    return html.Div(
        [
            dcc.RadioItems(
                id="chart-range",
                options=RANGES,
                value="6M",
                inline=True,
                className="range-buttons",
            ),
            dcc.Checklist(
                id="chart-overlays",
                options=[
                    {"label": "SMA 20", "value": "sma_20"},
                    {"label": "SMA 50", "value": "sma_50"},
                    {"label": "SMA 200", "value": "sma_200"},
                    {"label": "Bollinger Bands", "value": "bb"},
                ],
                value=["sma_20", "sma_50"],
                inline=True,
                className="overlay-toggles",
            ),
            html.Div(
                dcc.Graph(id="chart-graph", config=GRAPH_CONFIG), className="glass"
            ),
        ]
    )


def _fmt(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def build_price_figure(rows, overlays):
    dates = [r["date"] for r in rows]
    hover = [
        f"O {_fmt(r['open'])} H {_fmt(r['high'])} L {_fmt(r['low'])} "
        f"C {_fmt(r['close'])}<br>Vol {_fmt(r.get('volume'), 0)} · "
        f"RSI {_fmt(r.get('rsi'), 0)} · SMA20 {_fmt(r.get('sma_20'))} · "
        f"SMA50 {_fmt(r.get('sma_50'))}"
        for r in rows
    ]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=[r["open"] for r in rows],
            high=[r["high"] for r in rows],
            low=[r["low"] for r in rows],
            close=[r["close"] for r in rows],
            text=hover,
            hoverinfo="text",
            increasing_line_color=UP,
            decreasing_line_color=DOWN,
            name="Price",
        ),
        row=1,
        col=1,
    )
    colors = {"sma_20": "#f59e0b", "sma_50": ACCENT, "sma_200": "#a855f7"}
    for key in ("sma_20", "sma_50", "sma_200"):
        if key in overlays:
            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=[r.get(key) for r in rows],
                    mode="lines",
                    name=key.upper().replace("_", " "),
                    line={"color": colors[key], "width": 1.5},
                ),
                row=1,
                col=1,
            )
    if "bb" in overlays:
        for key, name in (("bb_upper", "BB upper"), ("bb_lower", "BB lower")):
            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=[r.get(key) for r in rows],
                    mode="lines",
                    name=name,
                    line={"color": "#94a3b8", "width": 1, "dash": "dot"},
                ),
                row=1,
                col=1,
            )
    fig.add_trace(
        go.Bar(
            x=dates,
            y=[r.get("volume") for r in rows],
            name="Volume",
            marker_color="rgba(59,130,246,0.5)",
        ),
        row=2,
        col=1,
    )
    style_figure(fig, 560)
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="closest")
    return fig


@app.callback(
    Output("chart-graph", "figure"),
    Input("chart-range", "value"),
    Input("chart-overlays", "value"),
    Input("url", "search"),
)
def update_price_chart(period, overlays, search):
    symbol = parse_asset(search)
    if not symbol:
        raise PreventUpdate
    body = api_json(f"/market/history/{quote(symbol, safe='')}", {"period": period})
    rows = (body or {}).get("data") or []
    if not rows:
        return empty_figure("Unable to load price history")
    return build_price_figure(rows, overlays or [])


# --------------------------------------------------------------- forecast tab


def forecast_tab():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Forecast horizon (days)", className="metric-label"),
                    dcc.Slider(
                        id="forecast-days",
                        min=7,
                        max=90,
                        step=1,
                        value=30,
                        marks={7: "7", 30: "30", 60: "60", 90: "90"},
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup",
                    ),
                ],
                className="glass",
            ),
            html.Div(
                dcc.Graph(id="forecast-graph", config=GRAPH_CONFIG), className="glass"
            ),
            html.Div(id="forecast-metrics", className="glass"),
            html.Div(id="forecast-insight", className="glass insight"),
        ]
    )


def build_forecast_figure(history_rows, forecast):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[r["date"] for r in history_rows[-120:]],
            y=[r["close"] for r in history_rows[-120:]],
            name="History",
            line={"color": "#e2e8f0", "width": 2},
        )
    )
    palette = {"ensemble": ACCENT, "arima": "#f59e0b", "prophet": "#a855f7"}
    for model, color in palette.items():
        series = forecast.get(model) or []
        if not series:
            continue
        dates = [p["forecast_date"] for p in series]
        lower = [p.get("lower_bound") for p in series]
        upper = [p.get("upper_bound") for p in series]
        if all(v is not None for v in lower + upper):
            fig.add_trace(
                go.Scatter(
                    x=dates + dates[::-1],
                    y=upper + lower[::-1],
                    fill="toself",
                    fillcolor="rgba(148,163,184,0.12)",
                    line={"width": 0},
                    hoverinfo="skip",
                    showlegend=False,
                    name=f"{model} band",
                )
            )
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=[p["predicted_price"] for p in series],
                name=f"{model.title()} forecast",
                line={"color": color, "width": 2, "dash": "dash"},
            )
        )
    return style_figure(fig, 460)


@app.callback(
    Output("forecast-graph", "figure"),
    Output("forecast-metrics", "children"),
    Output("forecast-insight", "children"),
    Input("forecast-days", "value"),
    Input("url", "search"),
)
def update_forecast(days, search):
    symbol = parse_asset(search)
    if not symbol:
        raise PreventUpdate
    safe = quote(symbol, safe="")
    forecast = api_json(f"/stocks/{safe}/forecast", {"days": days or 30})
    if not forecast:
        return (
            empty_figure("Unable to compute"),
            "Unable to compute",
            "Unable to compute a forecast right now. Please try again shortly. "
            + FORECAST_DISCLAIMER,
        )
    history = api_json(f"/market/history/{safe}", {"period": "6M"})
    rows = (history or {}).get("data") or []
    metrics = forecast.get("metrics") or {}
    items = []
    for model in ("ensemble", "arima", "prophet"):
        if forecast.get(model):
            error = metrics.get(model)
            items.append(
                kpi(
                    f"{model.title()} MAPE (30-day backtest)",
                    "—" if error is None else f"{error:.1f}",
                    "" if error is None else "%",
                )
            )
    return (
        build_forecast_figure(rows, forecast),
        html.Div(items, className="metric-grid"),
        [html.H4("🤖 AI Insight", style={"marginTop": 0}), html.P(forecast["insight"])],
    )


# ------------------------------------------------------------- technical tab


def indicator_figure(rows, traces, title, bars=None, hlines=()):
    dates = [r["date"] for r in rows]
    fig = go.Figure()
    if bars:
        fig.add_trace(
            go.Bar(
                x=dates,
                y=[r.get(bars[0]) for r in rows],
                name=bars[1],
                marker_color="rgba(148,163,184,0.5)",
            )
        )
    for key, name, color in traces:
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=[r.get(key) for r in rows],
                name=name,
                line={"color": color, "width": 1.6},
            )
        )
    for level in hlines:
        fig.add_hline(y=level, line_dash="dot", line_color="#64748b")
    fig.update_layout(title=title)
    return style_figure(fig, 300)


def signal_gauge(signal):
    score = signal["score"] * 100
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": "%"},
            title={"text": f"{signal['signal']} (strength {signal['strength']}%)"},
            gauge={
                "axis": {"range": [-100, 100]},
                "bar": {"color": ACCENT},
                "steps": [
                    {"range": [-100, -25], "color": "rgba(239,68,68,0.4)"},
                    {"range": [-25, 25], "color": "rgba(245,158,11,0.35)"},
                    {"range": [25, 100], "color": "rgba(16,185,129,0.4)"},
                ],
            },
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        height=260,
        margin={"l": 30, "r": 30, "t": 60, "b": 10},
    )
    return fig


def chart_card(figure, tip):
    return html.Div(
        [
            html.Span("ⓘ", className="info-tip", title=tip),
            dcc.Graph(figure=figure, config=GRAPH_CONFIG),
        ],
        className="glass",
    )


def technical_tab(symbol):
    safe = quote(symbol, safe="")
    history = api_json(f"/market/history/{safe}", {"period": "1Y"})
    analysis = api_json(f"/market/analysis/{safe}")
    rows = (history or {}).get("data") or []
    if not rows or not analysis:
        return html.Div("Unable to load technical analysis.", className="glass down")
    signal = analysis["signal"]
    breakdown = html.Ul(
        [
            html.Li(item["text"], title=item["tip"], style={"cursor": "help"})
            for item in signal["breakdown"]
        ]
    )
    return html.Div(
        [
            html.Div(
                [
                    dcc.Graph(figure=signal_gauge(signal), config=GRAPH_CONFIG),
                    breakdown,
                    html.P(FORECAST_DISCLAIMER, className="asset-updated"),
                ],
                className="glass",
            ),
            html.Div(
                [
                    chart_card(
                        indicator_figure(
                            rows, [("rsi", "RSI", "#f59e0b")], "RSI", hlines=(30, 70)
                        ),
                        INDICATOR_TIPS["RSI"],
                    ),
                    chart_card(
                        indicator_figure(
                            rows,
                            [("macd", "MACD", ACCENT), ("macd_signal", "Signal", DOWN)],
                            "MACD",
                            bars=("macd_hist", "Histogram"),
                        ),
                        INDICATOR_TIPS["MACD"],
                    ),
                    chart_card(
                        indicator_figure(
                            rows,
                            [
                                ("close", "Close", "#e2e8f0"),
                                ("bb_upper", "Upper", "#94a3b8"),
                                ("bb_middle", "Middle", ACCENT),
                                ("bb_lower", "Lower", "#94a3b8"),
                            ],
                            "Bollinger Bands",
                        ),
                        INDICATOR_TIPS["Bollinger"],
                    ),
                    chart_card(
                        indicator_figure(rows, [("atr", "ATR", "#a855f7")], "ATR"),
                        INDICATOR_TIPS["ATR"],
                    ),
                    chart_card(
                        indicator_figure(rows, [("obv", "OBV", UP)], "OBV"),
                        INDICATOR_TIPS["OBV"],
                    ),
                ],
                className="chart-grid",
            ),
        ]
    )


# ------------------------------------------------------------- analytics tab

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


def analytics_tab(symbol):
    analysis = api_json(f"/market/analysis/{quote(symbol, safe='')}")
    if not analysis:
        return html.Div("Unable to load analytics.", className="glass down")
    data = analysis["analytics"]
    m = data["metrics"]

    drawdown = go.Figure(
        go.Scatter(
            x=[p["date"] for p in data["drawdown"]],
            y=[p["value"] for p in data["drawdown"]],
            fill="tozeroy",
            line={"color": DOWN},
            name="Drawdown %",
        )
    )
    drawdown.update_layout(title="Drawdown (%)")

    years = sorted({p["year"] for p in data["monthly_returns"]})
    z = [[None] * 12 for _ in years]
    for p in data["monthly_returns"]:
        z[years.index(p["year"])][p["month"] - 1] = p["value"]
    heat = go.Figure(
        go.Heatmap(
            z=z,
            x=MONTHS,
            y=[str(y) for y in years],
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "%"},
        )
    )
    heat.update_layout(title="Monthly returns (%)")

    hist = go.Figure(go.Histogram(x=data["returns"], nbinsx=40, marker_color=ACCENT))
    hist.update_layout(title="Daily return distribution (%)", hovermode="closest")

    return html.Div(
        [
            html.Div(
                [
                    kpi("Sharpe ratio", m["sharpe_ratio"]),
                    kpi("Max drawdown", m["max_drawdown"], "%"),
                    kpi("Annual volatility", m["annual_volatility"], "%"),
                    kpi("VaR (95%, 1-day)", m["var_95"], "%"),
                    kpi("Cumulative return", m["cumulative_return"], "%"),
                ],
                className="glass metric-grid",
            ),
            html.Div(
                [
                    html.H4("Recommendation summary", style={"marginTop": 0}),
                    html.P(data["recommendation"]["summary"]),
                ],
                className="glass insight",
            ),
            html.Div(
                [
                    chart_card(style_figure(drawdown, 320), "Peak-to-trough decline."),
                    chart_card(
                        style_figure(heat, 320), "Return of each calendar month."
                    ),
                    chart_card(
                        style_figure(hist, 320), "How often each daily return occurs."
                    ),
                ],
                className="chart-grid",
            ),
        ]
    )


# --------------------------------------------------------------- compare tab


def compare_tab(symbol):
    default = next((s for s in ("AAPL", "BTC-USD", "GC=F") if s != symbol), None)
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Compare {symbol} with (1-4 assets; type to add any symbol)",
                        className="metric-label",
                    ),
                    dcc.Dropdown(
                        id="compare-select",
                        options=known_options(),
                        value=[default] if default else [],
                        multi=True,
                        placeholder="Select assets…",
                        className="search-box",
                        style={"width": "100%"},
                    ),
                ],
                className="glass",
            ),
            html.Div(id="compare-content"),
        ]
    )


@app.callback(
    Output("compare-select", "options"),
    Input("compare-select", "search_value"),
    State("compare-select", "value"),
)
def update_compare_options(search_value, value):
    return with_free_text(search_value, value)


@app.callback(
    Output("compare-content", "children"),
    Input("compare-select", "value"),
    Input("url", "search"),
)
def update_compare(selected, search):
    symbol = parse_asset(search)
    if not symbol:
        raise PreventUpdate
    others = [s for s in dict.fromkeys(selected or []) if s != symbol][:4]
    if not others:
        return html.Div("Select at least one asset to compare.", className="glass")
    result = api_json(
        "/market/compare", {"symbols": ",".join([symbol] + others), "period": "1Y"}
    )
    if not result:
        return html.Div("Unable to compare these assets.", className="glass down")

    perf = go.Figure()
    for sym in result["symbols"]:
        perf.add_trace(
            go.Scatter(x=result["dates"], y=result["normalized"][sym], name=sym)
        )
    perf.update_layout(title="Performance (rebased to 100)")

    corr = result["correlation"]
    heat = go.Figure(
        go.Heatmap(
            z=corr["matrix"],
            x=corr["labels"],
            y=corr["labels"],
            zmin=-1,
            zmax=1,
            colorscale="RdBu",
            text=corr["matrix"],
            texttemplate="%{text:.2f}",
        )
    )
    heat.update_layout(title="Correlation matrix (daily returns)")

    rows = [
        [
            sym,
            f"{result['metrics'][sym]['cumulative_return']}%",
            f"{result['metrics'][sym]['annual_volatility']}%",
            result["metrics"][sym]["sharpe_ratio"],
            f"{result['metrics'][sym]['max_drawdown']}%",
            _fmt(result["beta"].get(sym)),
        ]
        for sym in result["symbols"]
    ]
    children = [
        chart_card(style_figure(perf, 400), "All assets rebased to 100 at the start."),
        html.Div(
            [
                chart_card(
                    style_figure(heat, 360), "1 = move together, -1 = opposite."
                ),
                html.Div(
                    [
                        html.H4(f"Metrics (beta vs {symbol})", style={"marginTop": 0}),
                        simple_table(
                            [
                                "Asset",
                                "Return",
                                "Volatility",
                                "Sharpe",
                                "Max DD",
                                "Beta",
                            ],
                            rows,
                        ),
                    ],
                    className="glass",
                ),
            ],
            className="chart-grid",
        ),
    ]
    if result.get("missing"):
        children.append(
            html.P(f"No data for: {', '.join(result['missing'])}", className="down")
        )
    return children
