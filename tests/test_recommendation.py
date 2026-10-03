from helpers import ensure, ensure_equal

from services.recommendation import score_recommendation


def _bullish_inputs():
    return {
        "regime": {
            "trend": "uptrend",
            "momentum": "strengthening",
            "volatility": "normal",
            "atr_pct": 2.1,
            "rsi": 58.0,
            "macd_hist": 0.8,
            "bollinger_pct_b": 0.62,
            "sma_cross": "bullish",
        },
        "forecast": {
            "horizon_days": 14,
            "expected_return_pct": 8.0,
            "lower_pct": -3.0,
            "upper_pct": 12.0,
            "directional_accuracy": 64.0,
            "mape": 4.0,
            "agreement": 0.8,
        },
        "sentiment": 0.45,
        "market_context": {"momentum_score": 62.0, "beta": 1.1, "correlation": 0.55},
        "last_price": 100.0,
        "atr": 2.5,
        "support": [96.0, 92.0],
        "resistance": [108.0, 114.0],
        "drawdown_pct": 8.0,
    }


def test_score_recommendation_handles_bullish_bearish_and_determinism():
    bullish = score_recommendation(_bullish_inputs(), holding=False)
    bearish = score_recommendation(
        {
            "regime": {
                "trend": "downtrend",
                "momentum": "weakening",
                "volatility": "high",
                "atr_pct": 6.4,
                "rsi": 74.0,
                "macd_hist": -1.2,
                "bollinger_pct_b": 1.04,
                "sma_cross": "bearish",
            },
            "forecast": {
                "horizon_days": 10,
                "expected_return_pct": -7.5,
                "lower_pct": -11.0,
                "upper_pct": 2.0,
                "directional_accuracy": 67.0,
                "mape": 5.0,
                "agreement": 0.76,
            },
            "sentiment": -0.5,
            "market_context": {
                "momentum_score": 38.0,
                "beta": 1.6,
                "correlation": 0.91,
            },
            "last_price": 100.0,
            "atr": 4.0,
            "support": [91.0, 86.0],
            "resistance": [105.0, 109.0],
            "drawdown_pct": 24.0,
        },
        holding=True,
    )

    ensure(bullish["action"] in ("BUY", "STRONG BUY"))
    ensure(bullish["score"] > 0)
    ensure_equal(bullish["horizon_days"], 14)
    ensure("Wait for entry near" in bullish["position_advice"])
    ensure_equal(bullish, score_recommendation(_bullish_inputs(), holding=False))

    ensure(bearish["action"] in ("SELL", "STRONG SELL"))
    ensure(bearish["score"] < 0)
    ensure_equal(bearish["risk_level"], "High")
    ensure(
        "reducing exposure" in bearish["position_advice"]
        or "taking profits" in bearish["position_advice"]
    )


def test_poor_backtest_pushes_recommendation_to_hold():
    poor = score_recommendation(
        {
            "regime": {
                "trend": "uptrend",
                "momentum": "strengthening",
                "volatility": "normal",
                "atr_pct": 2.0,
                "rsi": 74.0,
                "macd_hist": 0.9,
                "bollinger_pct_b": 0.96,
                "sma_cross": "bullish",
            },
            "forecast": {
                "horizon_days": 7,
                "expected_return_pct": 6.0,
                "lower_pct": -5.0,
                "upper_pct": 8.0,
                "directional_accuracy": 52.0,
                "mape": 6.0,
                "agreement": 0.42,
            },
            "sentiment": 0.3,
            "last_price": 100.0,
            "atr": 2.2,
            "support": [97.0],
            "resistance": [106.0],
        }
    )

    ensure_equal(poor["action"], "HOLD")
    ensure(poor["confidence"] <= 45)
    ensure(any("52%" in reason for reason in poor["reasons"]))
    ensure(any("HOLD" in reason for reason in poor["reasons"]))


def test_missing_data_and_holding_wording_are_safe():
    sparse = score_recommendation({"last_price": 50.0}, holding=None)
    held = score_recommendation(_bullish_inputs(), holding=True)

    ensure_equal(sparse["action"], "HOLD")
    ensure(sparse["entry"] == 50.0)
    ensure(
        any(
            factor["detail"] == "Unavailable from inputs."
            for factor in sparse["factors"]
        )
    )
    ensure(
        "Balanced setup" in sparse["position_advice"]
        or "Patience" in sparse["position_advice"]
        or "Balanced" in sparse["position_advice"]
    )

    ensure(held["action"] in ("BUY", "STRONG BUY"))
    ensure("Hold existing shares" in held["position_advice"])
    ensure_equal(
        held["disclaimer"],
        "Educational only, not financial advice; forecasts can be wrong.",
    )


def test_mixed_setup_can_land_on_hold_with_factor_breakdown():
    mixed = score_recommendation(
        {
            "regime": {
                "trend": "range",
                "momentum": "neutral",
                "volatility": "normal",
                "atr_pct": 3.0,
                "rsi": 51.0,
                "macd_hist": 0.02,
                "bollinger_pct_b": 0.55,
                "sma_cross": None,
            },
            "forecast": {
                "horizon_days": 30,
                "expected_return_pct": 1.2,
                "lower_pct": -4.5,
                "upper_pct": 4.8,
                "directional_accuracy": 58.0,
                "mape": 7.0,
                "agreement": 0.54,
            },
            "sentiment": 0.0,
            "market_context": {"momentum_score": 50.0, "beta": 1.0, "correlation": 0.4},
            "last_price": 100.0,
            "atr": 2.0,
            "support": [97.5],
            "resistance": [103.0],
            "drawdown_pct": 11.0,
        }
    )

    ensure_equal(mixed["action"], "HOLD")
    ensure_equal(
        [factor["name"] for factor in mixed["factors"]],
        [
            "trend",
            "momentum",
            "mean_reversion",
            "forecast",
            "model_agreement",
            "sentiment",
            "volatility_risk",
            "market_context",
        ],
    )
    ensure(len(mixed["reasons"]) >= 2)
