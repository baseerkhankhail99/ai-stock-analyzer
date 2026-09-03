import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import requests
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Initialize Dash app
app = dash.Dash(__name__)

# API base URL
API_BASE_URL = 'http://localhost:5000/api'

# App styles
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>AI Stock Analyzer</title>
        {%favicon%}
        {%css%}
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: #333;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 20px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            .container {
                max-width: 1400px;
                margin: 0 auto;
                padding: 20px;
            }
            .card {
                background: white;
                border-radius: 8px;
                padding: 20px;
                margin: 10px 0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .metric {
                display: inline-block;
                margin: 10px 20px;
                font-size: 14px;
            }
            .metric-value {
                font-size: 24px;
                font-weight: bold;
                color: #667eea;
            }
            .buy { color: #27ae60; }
            .sell { color: #e74c3c; }
            .hold { color: #f39c12; }
            .input-section {
                background: white;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 20px;
            }
            .button {
                background: #667eea;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
            }
            .button:hover {
                background: #764ba2;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''

# App layout
app.layout = html.Div([
    # Header
    html.Div([
        html.H1('📈 AI Stock Analyzer Dashboard', style={'margin': 0}),
        html.P('Advanced Stock Analysis & ML Forecasting Platform', style={'margin': '5px 0'})
    ], className='header'),
    
    # Main container
    html.Div([
        # Input Section
        html.Div([
            html.H3('Search & Analyze'),
            html.Div([
                dcc.Input(
                    id='symbol-input',
                    type='text',
                    placeholder='Enter stock symbol (e.g., AAPL)',
                    style={'padding': '10px', 'width': '300px', 'marginRight': '10px', 'borderRadius': '4px', 'border': '1px solid #ddd'}
                ),
                html.Button('Analyze', id='analyze-button', n_clicks=0, className='button', style={'marginRight': '10px'}),
                dcc.Loading(
                    id='loading',
                    type='default',
                    children=html.Div(id='loading-output')
                )
            ]),
        ], className='input-section'),
        
        # Price & Info Section
        html.Div([
            html.Div([
                html.Div(id='price-info-output', className='card')
            ], style={'display': 'grid', 'gridTemplateColumns': '1fr', 'gap': '20px'})
        ]),
        
        # Tabs for different analyses
        dcc.Tabs(id='analysis-tabs', value='tab-1', children=[
            # Price Chart Tab
            dcc.Tab(label='📊 Price Chart', value='tab-1', children=[
                html.Div([
                    dcc.Graph(id='price-chart')
                ], className='card')
            ]),
            
            # Forecast Tab
            dcc.Tab(label='🔮 Forecast', value='tab-2', children=[
                html.Div([
                    html.Div([
                        html.Label('Forecast Days:'),
                        dcc.Slider(
                            id='forecast-days-slider',
                            min=7,
                            max=90,
                            step=7,
                            value=30,
                            marks={7: '7d', 30: '30d', 60: '60d', 90: '90d'}
                        )
                    ], style={'marginBottom': '20px'}),
                    dcc.Graph(id='forecast-chart')
                ], className='card')
            ]),
            
            # Technical Analysis Tab
            dcc.Tab(label='📉 Technical Analysis', value='tab-3', children=[
                html.Div([
                    html.Div(id='technical-signals', className='card', style={'marginBottom': '20px'}),
                    html.Div(style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '20px'}, children=[
                        html.Div([
                            dcc.Graph(id='rsi-chart')
                        ], className='card'),
                        html.Div([
                            dcc.Graph(id='macd-chart')
                        ], className='card')
                    ])
                ])
            ]),
            
            # Analytics Tab
            dcc.Tab(label='📈 Analytics', value='tab-4', children=[
                html.Div([
                    html.Div(id='analytics-output', className='card', style={'marginBottom': '20px'}),
                    html.Div(style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '20px'}, children=[
                        html.Div([
                            dcc.Graph(id='returns-chart')
                        ], className='card'),
                        html.Div([
                            dcc.Graph(id='volatility-chart')
                        ], className='card')
                    ])
                ])
            ]),
            
            # Comparison Tab
            dcc.Tab(label='🔄 Compare', value='tab-5', children=[
                html.Div([
                    html.Div([
                        dcc.Input(
                            id='compare-symbol-input',
                            type='text',
                            placeholder='Enter second symbol',
                            style={'padding': '10px', 'width': '300px', 'marginRight': '10px', 'borderRadius': '4px', 'border': '1px solid #ddd'}
                        ),
                        html.Button('Compare', id='compare-button', n_clicks=0, className='button')
                    ], style={'marginBottom': '20px'}),
                    html.Div(id='comparison-output', className='card')
                ])
            ])
        ]),
        
        # Store for caching data
        dcc.Store(id='stock-data-store'),
        dcc.Store(id='forecast-data-store'),
        dcc.Store(id='analytics-data-store')
        
    ], className='container')
], style={'minHeight': '100vh', 'backgroundColor': '#f5f5f5'})

# Callbacks
@app.callback(
    [Output('price-info-output', 'children'),
     Output('stock-data-store', 'data'),
     Output('forecast-data-store', 'data'),
     Output('analytics-data-store', 'data')],
    Input('analyze-button', 'n_clicks'),
    State('symbol-input', 'value'),
    prevent_initial_call=True
)
def update_stock_analysis(n_clicks, symbol):
    """Main analysis callback"""
    if not symbol:
        return html.Div('Please enter a stock symbol'), {}, {}, {}
    
    try:
        # Fetch price data
        price_response = requests.get(f'{API_BASE_URL}/stocks/{symbol}/price')
        price_data = price_response.json() if price_response.status_code == 200 else {}
        
        # Fetch forecast data
        forecast_response = requests.get(f'{API_BASE_URL}/stocks/{symbol}/forecasts/stored')
        forecast_data = forecast_response.json() if forecast_response.status_code == 200 else {}
        
        # Fetch analytics
        analytics_response = requests.get(f'{API_BASE_URL}/stocks/{symbol}/analytics')
        analytics_data = analytics_response.json() if analytics_response.status_code == 200 else {}
        
        # Create price info display
        if price_data:
            price_info = html.Div([
                html.Div([
                    html.Div([
                        html.H2(f"{price_data.get('symbol', symbol)}", style={'margin': '0 0 10px 0'}),
                        html.Div([
                            html.Span(f"Price: ${price_data.get('price', 'N/A'):.2f}", className='metric'),
                            html.Span(f"Change: {price_data.get('change_percent', 0):.2f}%", className='metric'),
                            html.Span(f"Volume: {price_data.get('volume', 0):,}", className='metric'),
                            html.Span(f"P/E: {price_data.get('pe_ratio', 'N/A')}", className='metric'),
                        ])
                    ])
                ], className='card')
            ])
            return price_info, price_data, forecast_data, analytics_data
        else:
            return html.Div(f'Could not find data for {symbol}'), {}, {}, {}
    
    except Exception as e:
        logger.error(f"Error in update_stock_analysis: {str(e)}")
        return html.Div(f'Error: {str(e)}'), {}, {}, {}

@app.callback(
    Output('price-chart', 'figure'),
    Input('stock-data-store', 'data'),
    State('symbol-input', 'value')
)
def update_price_chart(stock_data, symbol):
    """Update price chart"""
    if not symbol:
        return go.Figure()
    
    try:
        history_response = requests.get(f'{API_BASE_URL}/stocks/{symbol}/history?days=365')
        if history_response.status_code == 200:
            history_data = history_response.json()
            prices = history_data.get('data', [])
            
            if prices:
                df = pd.DataFrame(prices)
                df['date'] = pd.to_datetime(df['date'])
                
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df['date'],
                    open=df['open'],
                    high=df['high'],
                    low=df['low'],
                    close=df['close']
                ))
                
                fig.update_layout(
                    title=f'{symbol} - Price Chart (1 Year)',
                    yaxis_title='Stock Price (USD)',
                    template='plotly_white',
                    xaxis_rangeslider_visible=False,
                    hovermode='x unified'
                )
                
                return fig
    except Exception as e:
        logger.error(f"Error in update_price_chart: {str(e)}")
    
    return go.Figure()

@app.callback(
    Output('forecast-chart', 'figure'),
    Input('forecast-data-store', 'data'),
    State('symbol-input', 'value'),
    State('forecast-days-slider', 'value')
)
def update_forecast_chart(forecast_data, symbol, days):
    """Update forecast chart"""
    if not symbol or not forecast_data:
        return go.Figure()
    
    try:
        forecasts = forecast_data.get('forecasts', [])
        if forecasts:
            df = pd.DataFrame(forecasts)
            df['date'] = pd.to_datetime(df['date'])
            
            fig = go.Figure()
            
            # Add different models
            for model in df['model'].unique():
                model_data = df[df['model'] == model]
                fig.add_trace(go.Scatter(
                    x=model_data['date'],
                    y=model_data['predicted_price'],
                    mode='lines',
                    name=f'{model.upper()} Forecast',
                    line=dict(width=2)
                ))
                
                # Add confidence interval
                if model_data['lower_bound'].notna().any():
                    fig.add_trace(go.Scatter(
                        x=model_data['date'].tolist() + model_data['date'].tolist()[::-1],
                        y=model_data['upper_bound'].tolist() + model_data['lower_bound'].tolist()[::-1],
                        fill='toself',
                        fillcolor=f'rgba(0,100,200,0.1)',
                        line=dict(color='rgba(255,255,255,0)'),
                        showlegend=False,
                        name=f'{model} CI'
                    ))
            
            fig.update_layout(
                title=f'{symbol} - Price Forecast ({days} days)',
                yaxis_title='Predicted Price (USD)',
                template='plotly_white',
                hovermode='x unified',
                height=500
            )
            
            return fig
    except Exception as e:
        logger.error(f"Error in update_forecast_chart: {str(e)}")
    
    return go.Figure()

@app.callback(
    Output('technical-signals', 'children'),
    Input('stock-data-store', 'data'),
    State('symbol-input', 'value')
)
def update_technical_signals(stock_data, symbol):
    """Update technical signals"""
    if not symbol:
        return html.Div('No data available')
    
    try:
        signals_response = requests.get(f'{API_BASE_URL}/stocks/{symbol}/signals')
        if signals_response.status_code == 200:
            signals_data = signals_response.json()
            signal_info = signals_data.get('signal', {})
            
            signal = signal_info.get('signal', 'HOLD')
            strength = signal_info.get('strength', 0)
            signals_list = signal_info.get('signals', [])
            
            signal_color = 'buy' if signal == 'BUY' else 'sell' if signal == 'SELL' else 'hold'
            
            return html.Div([
                html.H3(f'Trading Signal: ', style={'display': 'inline'}),
                html.Span(signal, className=signal_color, style={'fontSize': '24px', 'fontWeight': 'bold'}),
                html.Div(f'Signal Strength: {strength:.2%}', style={'marginTop': '10px'}),
                html.Div([
                    html.Div([
                        html.Strong(s['type']),
                        f": {s['signal']} (Strength: {s['strength']:.2%})"
                    ], style={'margin': '5px 0'}) for s in signals_list
                ], style={'marginTop': '15px'})
            ])
    except Exception as e:
        logger.error(f"Error in update_technical_signals: {str(e)}")
        return html.Div(f'Error: {str(e)}')

@app.callback(
    Output('analytics-output', 'children'),
    Input('analytics-data-store', 'data'),
    State('symbol-input', 'value')
)
def update_analytics_output(analytics_data, symbol):
    """Update analytics output"""
    if not analytics_data:
        return html.Div('No analytics data available')
    
    try:
        return html.Div([
            html.H3('Analytics Report'),
            html.Div([
                html.Div(f"Recommendation: {analytics_data.get('recommendation', 'N/A')}", className='metric'),
                html.Div(f"Sharpe Ratio: {analytics_data.get('sharpe_ratio', 0):.2f}", className='metric'),
                html.Div(f"Max Drawdown: {analytics_data.get('max_drawdown', 0):.2%}", className='metric'),
                html.Div(f"Volatility: {analytics_data.get('volatility_annual', 0):.2%}", className='metric'),
            ])
        ])
    except Exception as e:
        logger.error(f"Error in update_analytics_output: {str(e)}")
        return html.Div(f'Error: {str(e)}')

@app.callback(
    Output('comparison-output', 'children'),
    Input('compare-button', 'n_clicks'),
    State('symbol-input', 'value'),
    State('compare-symbol-input', 'value'),
    prevent_initial_call=True
)
def update_comparison(n_clicks, symbol1, symbol2):
    """Update comparison"""
    if not symbol1 or not symbol2:
        return html.Div('Please enter both symbols')
    
    try:
        compare_response = requests.get(f'{API_BASE_URL}/stocks/compare?symbol1={symbol1}&symbol2={symbol2}')
        if compare_response.status_code == 200:
            comp_data = compare_response.json()
            
            return html.Div([
                html.H3(f'Comparing {symbol1} vs {symbol2}'),
                html.Div([
                    html.Div(f"Correlation: {comp_data.get('correlation', 0):.3f}", className='metric'),
                    html.Div(f"Beta: {comp_data.get('beta', 0):.3f}", className='metric'),
                    html.Div(f"Performance {symbol1}: {comp_data.get('performance_1', 0):.2%}", className='metric'),
                    html.Div(f"Performance {symbol2}: {comp_data.get('performance_2', 0):.2%}", className='metric'),
                    html.Div(f"Volatility Difference: {comp_data.get('volatility_difference', 0):.2%}", className='metric'),
                ])
            ])
    except Exception as e:
        logger.error(f"Error in update_comparison: {str(e)}")
        return html.Div(f'Error: {str(e)}')

if __name__ == '__main__':
    app.run_server(debug=True, port=8050)
