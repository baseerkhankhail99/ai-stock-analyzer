"""Indicator, signal, analytics and insight helpers working on in-memory data."""

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from ta import momentum, trend, volatility, volume

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Not financial advice; forecasts are statistical estimates and can be unreliable."
)

INDICATOR_TIPS = {
    "RSI": "Relative Strength Index (0-100). Above 70 is often seen as overbought, "
    "below 30 as oversold.",
    "MACD": "Moving Average Convergence Divergence. MACD above its signal line "
    "suggests bullish momentum.",
    "Bollinger": "Bollinger Bands show volatility around a 20-day average. Prices "
    "near the upper band can be stretched, near the lower band depressed.",
    "Trend": "Compares the 50-day and 200-day simple moving averages. 50 above 200 "
    "is a bullish trend.",
    "ATR": "Average True Range measures typical daily price movement (volatility).",
    "OBV": "On-Balance Volume accumulates volume on up days and subtracts it on "
    "down days, showing buying/selling pressure.",
}


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA, Bollinger, RSI, MACD, ATR and OBV columns to a lower-case OHLCV frame."""
    out = df.copy()
    close = out["close"].astype(float)
    for window in (20, 50, 200):
        out[f"sma_{window}"] = close.rolling(window).mean()
    bands = volatility.BollingerBands(close, window=20, window_dev=2)
    out["bb_upper"] = bands.bollinger_hband()
    out["bb_middle"] = bands.bollinger_mavg()
    out["bb_lower"] = bands.bollinger_lband()
    out["rsi"] = momentum.RSIIndicator(close, window=14).rsi()
    macd = trend.MACD(close)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    if {"high", "low"} <= set(out.columns):
        out["atr"] = volatility.AverageTrueRange(
            out["high"].astype(float), out["low"].astype(float), close, window=14
        ).average_true_range()
    else:
        out["atr"] = np.nan
    if "volume" in out.columns:
        out["obv"] = volume.OnBalanceVolumeIndicator(
            close, out["volume"].astype(float)
        ).on_balance_volume()
    else:
        out["obv"] = np.nan
    return out


def _last(series: pd.Series) -> Optional[float]:
    series = series.dropna()
    return float(series.iloc[-1]) if not series.empty else None


def trading_signal(df: pd.DataFrame) -> Dict:
    """Combine indicator votes (-1..1 each) into a BUY/HOLD/SELL signal."""
    ind = add_indicators(df)
    price = _last(ind["close"])
    rsi = _last(ind["rsi"])
    macd_v, macd_s = _last(ind["macd"]), _last(ind["macd_signal"])
    upper, lower = _last(ind["bb_upper"]), _last(ind["bb_lower"])
    sma50, sma200 = _last(ind["sma_50"]), _last(ind["sma_200"])
    sma20 = _last(ind["sma_20"])

    votes: List[float] = []
    breakdown: List[Dict] = []

    if rsi is not None:
        if rsi >= 70:
            vote, label = -1.0, "overbought"
        elif rsi <= 30:
            vote, label = 1.0, "oversold"
        else:
            vote, label = (50 - rsi) / 40, "neutral"
        votes.append(vote)
        breakdown.append(
            {
                "name": "RSI",
                "text": f"RSI {rsi:.0f} ({label})",
                "tip": INDICATOR_TIPS["RSI"],
            }
        )
    if macd_v is not None and macd_s is not None:
        bullish = macd_v > macd_s
        votes.append(1.0 if bullish else -1.0)
        breakdown.append(
            {
                "name": "MACD",
                "text": f"MACD ({'bullish' if bullish else 'bearish'})",
                "tip": INDICATOR_TIPS["MACD"],
            }
        )
    if None not in (price, upper, lower) and upper > lower:
        position = (price - lower) / (upper - lower)
        if position > 0.8:
            vote, label = -0.5, "near upper band"
        elif position < 0.2:
            vote, label = 0.5, "near lower band"
        else:
            vote, label = 0.0, "mid-range"
        votes.append(vote)
        breakdown.append(
            {
                "name": "Bollinger",
                "text": f"Bollinger ({label})",
                "tip": INDICATOR_TIPS["Bollinger"],
            }
        )
    long_ma = sma200 if sma200 is not None else sma50
    short_ma = sma50 if sma200 is not None else sma20
    if long_ma is not None and short_ma is not None:
        bullish = short_ma > long_ma
        votes.append(1.0 if bullish else -1.0)
        breakdown.append(
            {
                "name": "Trend",
                "text": f"Trend ({'bullish' if bullish else 'bearish'}, "
                f"{'50/200' if sma200 is not None else '20/50'} SMA)",
                "tip": INDICATOR_TIPS["Trend"],
            }
        )

    if not votes:
        return {"signal": "HOLD", "strength": 0.0, "score": 0.0, "breakdown": []}
    score = float(np.mean(votes))
    label = "BUY" if score > 0.25 else "SELL" if score < -0.25 else "HOLD"
    return {
        "signal": label,
        "strength": round(abs(score) * 100, 1),
        "score": round(score, 3),
        "breakdown": breakdown,
    }


def analytics_metrics(close: pd.Series) -> Dict:
    """Risk/return metrics and chart series from a daily close series."""
    close = close.dropna().astype(float)
    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return {}
    std = float(returns.std())
    cumulative = (1 + returns).cumprod()
    drawdown = cumulative / cumulative.cummax() - 1
    monthly = close.resample("ME").last().pct_change().dropna()
    metrics = {
        "sharpe_ratio": round(float(returns.mean() / std * np.sqrt(252)), 2)
        if std > 0
        else 0.0,
        "max_drawdown": round(float(drawdown.min()) * 100, 2),
        "annual_volatility": round(std * np.sqrt(252) * 100, 2),
        "var_95": round(float(-np.percentile(returns, 5)) * 100, 2),
        "cumulative_return": round(float(cumulative.iloc[-1] - 1) * 100, 2),
    }
    return {
        "metrics": metrics,
        "drawdown": [
            {"date": ts.isoformat(), "value": round(float(v) * 100, 3)}
            for ts, v in drawdown.items()
        ],
        "monthly_returns": [
            {"year": ts.year, "month": ts.month, "value": round(float(v) * 100, 2)}
            for ts, v in monthly.items()
        ],
        "returns": [round(float(v) * 100, 3) for v in returns],
        "recommendation": recommendation(metrics),
    }


def recommendation(metrics: Dict) -> Dict:
    """Rule-based summary of the risk metrics."""
    points = 0
    notes = []
    sharpe = metrics["sharpe_ratio"]
    if sharpe >= 1:
        points += 1
        notes.append(f"strong risk-adjusted return (Sharpe {sharpe})")
    elif sharpe < 0:
        points -= 1
        notes.append(f"negative risk-adjusted return (Sharpe {sharpe})")
    else:
        notes.append(f"moderate risk-adjusted return (Sharpe {sharpe})")
    if metrics["max_drawdown"] <= -35:
        points -= 1
        notes.append(f"deep historical drawdown ({metrics['max_drawdown']}%)")
    if metrics["annual_volatility"] >= 50:
        points -= 1
        notes.append(f"very high volatility ({metrics['annual_volatility']}%)")
    elif metrics["annual_volatility"] <= 20:
        points += 1
        notes.append(f"low volatility ({metrics['annual_volatility']}%)")
    rating = "Favorable" if points > 0 else "Cautious" if points < 0 else "Mixed"
    return {
        "rating": rating,
        "summary": f"{rating} risk profile: " + "; ".join(notes) + ". " + DISCLAIMER,
    }


def generate_insight(
    forecasts: Dict, history: pd.DataFrame, horizon: int, symbol: str = ""
) -> str:
    """Plain-language summary built from the forecast and recent indicators."""
    parts = []
    last_close = _last(history["close"]) if not history.empty else None
    for model in ("ensemble", "arima", "prophet"):
        series = forecasts.get(model) or []
        if series and last_close:
            end = series[-1]["predicted_price"]
            pct = (end - last_close) / last_close * 100
            parts.append(
                f"The {model} model projects a {abs(pct):.1f}% "
                f"{'gain' if pct >= 0 else 'decline'} over {horizon} days."
            )
            break
    if not parts:
        parts.append("No forecast model could be computed for this asset.")

    if not history.empty and len(history) >= 20:
        ind = add_indicators(history)
        rsi = _last(ind["rsi"])
        if rsi is not None:
            if rsi >= 70:
                text = "indicates overbought conditions"
            elif rsi >= 60:
                text = "shows momentum but is nearing overbought"
            elif rsi <= 30:
                text = "indicates oversold conditions"
            elif rsi <= 40:
                text = "shows weak momentum"
            else:
                text = "is neutral"
            parts.append(f"Current RSI ({rsi:.0f}) {text}.")
        sma50, sma200, sma20 = (_last(ind[c]) for c in ("sma_50", "sma_200", "sma_20"))
        if sma50 is not None and sma200 is not None:
            word = "bullish" if sma50 > sma200 else "bearish"
            parts.append(f"Trend is {word} based on 50/200 SMA.")
        elif sma20 is not None and sma50 is not None:
            word = "bullish" if sma20 > sma50 else "bearish"
            parts.append(f"Short-term trend is {word} based on 20/50 SMA.")
    parts.append(DISCLAIMER)
    return " ".join(parts)


def momentum_summary(closes: pd.DataFrame) -> Dict:
    """Computed (unofficial) market momentum score from daily closes of many assets."""
    above, rsis, gainers, losers = [], [], 0, 0
    for symbol in closes.columns:
        series = closes[symbol].dropna()
        if len(series) < 51:
            continue
        above.append(float(series.iloc[-1] > series.tail(50).mean()))
        rsi = _last(momentum.RSIIndicator(series, window=14).rsi())
        if rsi is not None:
            rsis.append(rsi)
        if series.iloc[-1] > series.iloc[-2]:
            gainers += 1
        elif series.iloc[-1] < series.iloc[-2]:
            losers += 1
    if not above:
        return {}
    pct_above = float(np.mean(above)) * 100
    avg_rsi = float(np.mean(rsis)) if rsis else 50.0
    total = gainers + losers
    gain_pct = gainers / total * 100 if total else 50.0
    score = round(0.4 * pct_above + 0.3 * avg_rsi + 0.3 * gain_pct)
    label = "Bullish" if score >= 60 else "Bearish" if score <= 40 else "Neutral"
    return {
        "score": int(score),
        "label": label,
        "pct_above_sma50": round(pct_above, 1),
        "avg_rsi": round(avg_rsi, 1),
        "gainers": gainers,
        "losers": losers,
        "assets": len(above),
        "note": "This is a computed indicator, not an official index.",
    }


def momentum_from_overview(overview: Dict) -> Dict:
    """Lighter momentum score from today's moves in an overview snapshot."""
    moves = [
        item["change_pct"]
        for category in ("commodities", "cryptocurrencies", "stocks", "indices")
        for item in overview.get(category) or []
        if item.get("change_pct") is not None and not item.get("error")
    ]
    if not moves:
        return {}
    gainers = sum(1 for m in moves if m > 0)
    losers = sum(1 for m in moves if m < 0)
    gain_pct = gainers / (gainers + losers) * 100 if gainers + losers else 50.0
    tilt = min(100.0, max(0.0, 50 + float(np.mean(moves)) * 10))
    score = round(0.6 * gain_pct + 0.4 * tilt)
    label = "Bullish" if score >= 60 else "Bearish" if score <= 40 else "Neutral"
    return {
        "score": int(score),
        "label": label,
        "pct_above_sma50": None,
        "avg_rsi": None,
        "gainers": gainers,
        "losers": losers,
        "assets": len(moves),
        "note": "Based on today's moves only; open asset charts to refine it. "
        "This is a computed indicator, not an official index.",
    }


def compare_assets(closes: pd.DataFrame, primary: str) -> Dict:
    """Normalized series, correlation, beta vs primary and metrics for a close matrix."""
    closes = closes.dropna(how="all").ffill().dropna()
    if closes.empty or primary not in closes.columns:
        return {}
    normalized = closes / closes.iloc[0] * 100
    returns = closes.pct_change().dropna()
    corr = returns.corr().round(3).fillna(0)
    beta = {}
    primary_var = returns[primary].var()
    for symbol in closes.columns:
        beta[symbol] = (
            round(float(returns[symbol].cov(returns[primary]) / primary_var), 2)
            if primary_var
            else None
        )
    metrics = {}
    for symbol in closes.columns:
        r = returns[symbol]
        std = float(r.std())
        cumulative = (1 + r).cumprod()
        metrics[symbol] = {
            "cumulative_return": round(float(cumulative.iloc[-1] - 1) * 100, 2),
            "annual_volatility": round(std * np.sqrt(252) * 100, 2),
            "sharpe_ratio": round(float(r.mean() / std * np.sqrt(252)), 2)
            if std
            else 0.0,
            "max_drawdown": round(
                float((cumulative / cumulative.cummax() - 1).min()) * 100, 2
            ),
        }
    return {
        "symbols": list(closes.columns),
        "dates": [ts.isoformat() for ts in closes.index],
        "normalized": {s: [round(float(v), 2) for v in normalized[s]] for s in closes},
        "correlation": {
            "labels": list(corr.columns),
            "matrix": corr.values.tolist(),
        },
        "beta": beta,
        "metrics": metrics,
    }
