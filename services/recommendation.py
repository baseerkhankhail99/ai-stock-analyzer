from __future__ import annotations

from typing import Dict, Iterable, List, Optional

DISCLAIMER = "Educational only, not financial advice; forecasts can be wrong."
BASE_WEIGHTS = {
    "trend": 18.0,
    "momentum": 12.0,
    "mean_reversion": 10.0,
    "forecast": 28.0,
    "model_agreement": 8.0,
    "sentiment": 8.0,
    "volatility_risk": 8.0,
    "market_context": 8.0,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_or_none(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _as_number(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nearest_level(
    levels: object, last_price: Optional[float], side: str
) -> Optional[float]:
    if last_price is None:
        return None
    if isinstance(levels, (int, float)):
        values = [float(levels)]
    elif isinstance(levels, Iterable) and not isinstance(levels, (str, bytes, dict)):
        values = [float(level) for level in levels if _as_number(level) is not None]
    else:
        return None
    if side == "support":
        candidates = [value for value in values if value < last_price]
        candidates.sort(key=lambda value: last_price - value)
    else:
        candidates = [value for value in values if value > last_price]
        candidates.sort(key=lambda value: value - last_price)
    return candidates[0] if candidates else None


def _factor_template(name: str, detail: str) -> Dict[str, object]:
    return {
        "name": name,
        "weight": 0.0,
        "score": 0.0,
        "contribution": 0.0,
        "detail": detail,
    }


def _trend_factor(regime: Dict[str, object]) -> Optional[Dict[str, object]]:
    trend = regime.get("trend")
    cross = regime.get("sma_cross")
    score = 0.0
    if trend == "uptrend":
        score += 0.7
    elif trend == "downtrend":
        score -= 0.7
    elif trend == "range":
        score += 0.0
    else:
        return None
    if cross == "golden":
        score += 0.3
    elif cross == "bullish":
        score += 0.15
    elif cross == "death":
        score -= 0.3
    elif cross == "bearish":
        score -= 0.15
    return {
        "score": _clamp(score, -1.0, 1.0),
        "detail": f"Trend is {trend}; moving-average state is {cross or 'unavailable'}.",
    }


def _momentum_factor(regime: Dict[str, object]) -> Optional[Dict[str, object]]:
    momentum = regime.get("momentum")
    hist = _as_number(regime.get("macd_hist"))
    if momentum is None and hist is None:
        return None
    score = 0.0
    if momentum == "strengthening":
        score += 0.6
    elif momentum == "weakening":
        score -= 0.6
    if hist is not None:
        score += 0.2 if hist > 0 else -0.2 if hist < 0 else 0.0
    return {
        "score": _clamp(score, -1.0, 1.0),
        "detail": (
            f"Momentum is {momentum or 'mixed'}; MACD histogram is "
            f"{round(hist, 4) if hist is not None else 'unavailable'}."
        ),
    }


def _mean_reversion_factor(regime: Dict[str, object]) -> Optional[Dict[str, object]]:
    rsi = _as_number(regime.get("rsi"))
    pct_b = _as_number(regime.get("bollinger_pct_b"))
    if rsi is None and pct_b is None:
        return None
    score = 0.0
    notes: List[str] = []
    if rsi is not None:
        if rsi >= 75:
            score -= 0.9
            notes.append(f"RSI {rsi:.1f} overbought")
        elif rsi >= 65:
            score -= 0.5
            notes.append(f"RSI {rsi:.1f} elevated")
        elif rsi <= 25:
            score += 0.9
            notes.append(f"RSI {rsi:.1f} oversold")
        elif rsi <= 35:
            score += 0.5
            notes.append(f"RSI {rsi:.1f} soft")
        else:
            notes.append(f"RSI {rsi:.1f} neutral")
    if pct_b is not None:
        if pct_b >= 1.0:
            score -= 0.4
            notes.append(f"%B {pct_b:.2f} above upper band")
        elif pct_b >= 0.85:
            score -= 0.2
            notes.append(f"%B {pct_b:.2f} near upper band")
        elif pct_b <= 0.0:
            score += 0.4
            notes.append(f"%B {pct_b:.2f} below lower band")
        elif pct_b <= 0.15:
            score += 0.2
            notes.append(f"%B {pct_b:.2f} near lower band")
    return {"score": _clamp(score, -1.0, 1.0), "detail": "; ".join(notes)}


def _forecast_factor(forecast: Dict[str, object]) -> Optional[Dict[str, object]]:
    expected = _as_number(forecast.get("expected_return_pct"))
    lower = _as_number(forecast.get("lower_pct"))
    upper = _as_number(forecast.get("upper_pct"))
    if expected is None:
        return None
    score = _clamp(expected / 12.0, -1.0, 1.0)
    if lower is not None and upper is not None and upper - lower > 0:
        width = upper - lower
        score *= _clamp(1.0 - (width / 40.0), 0.4, 1.0)
    return {
        "score": _clamp(score, -1.0, 1.0),
        "detail": (
            f"Forecast expects {expected:.2f}% over "
            f"{int(forecast.get('horizon_days') or 30)}d with interval "
            f"{lower if lower is not None else 'n/a'} to {upper if upper is not None else 'n/a'}."
        ),
    }


def _agreement_factor(forecast: Dict[str, object]) -> Optional[Dict[str, object]]:
    agreement = _as_number(forecast.get("agreement"))
    expected = _as_number(forecast.get("expected_return_pct"))
    if agreement is None:
        return None
    direction = 1.0 if (expected or 0.0) >= 0 else -1.0
    score = direction * max(0.0, (agreement - 0.5) * 2.0)
    if agreement < 0.45:
        score = direction * -0.2
    return {
        "score": _clamp(score, -1.0, 1.0),
        "detail": f"Model agreement is {agreement:.2f}.",
    }


def _sentiment_factor(value: object) -> Optional[Dict[str, object]]:
    sentiment = _as_number(value)
    if sentiment is None:
        return None
    return {
        "score": _clamp(sentiment, -1.0, 1.0),
        "detail": f"Sentiment score is {sentiment:.2f}.",
    }


def _volatility_factor(
    regime: Dict[str, object], atr: Optional[float], drawdown_pct: Optional[float]
) -> Optional[Dict[str, object]]:
    volatility = regime.get("volatility")
    atr_pct = _as_number(regime.get("atr_pct"))
    score = 0.0
    notes: List[str] = []
    available = False
    if volatility is not None:
        available = True
        if volatility == "low":
            score += 0.5
        elif volatility == "normal":
            score += 0.0
        elif volatility == "high":
            score -= 0.8
        notes.append(f"volatility {volatility}")
    if atr_pct is not None:
        available = True
        if atr_pct >= 5:
            score -= 0.4
        elif atr_pct <= 2:
            score += 0.2
        notes.append(f"ATR {atr_pct:.2f}%")
    if drawdown_pct is not None:
        available = True
        if drawdown_pct >= 20:
            score -= 0.5
        elif drawdown_pct >= 10:
            score -= 0.2
        notes.append(f"drawdown {drawdown_pct:.1f}%")
    if atr is not None:
        available = True
        notes.append(f"ATR ${atr:.2f}")
    if not available:
        return None
    return {"score": _clamp(score, -1.0, 1.0), "detail": ", ".join(notes)}


def _market_context_factor(context: Dict[str, object]) -> Optional[Dict[str, object]]:
    momentum_score = _as_number(context.get("momentum_score"))
    beta = _as_number(context.get("beta"))
    correlation = _as_number(context.get("correlation"))
    if momentum_score is None and beta is None and correlation is None:
        return None
    score = 0.0
    notes: List[str] = []
    if momentum_score is not None:
        score += _clamp((momentum_score - 50.0) / 50.0, -1.0, 1.0) * 0.8
        notes.append(f"market momentum {momentum_score:.0f}/100")
    if beta is not None:
        if beta > 1.4:
            score -= 0.2
        elif beta < 0.9:
            score += 0.1
        notes.append(f"beta {beta:.2f}")
    if correlation is not None:
        if correlation > 0.85:
            score -= 0.15
        notes.append(f"correlation {correlation:.2f}")
    return {"score": _clamp(score, -1.0, 1.0), "detail": ", ".join(notes)}


def _build_factors(inputs: Dict[str, object]) -> Dict[str, object]:
    regime = inputs.get("regime") or {}
    forecast = inputs.get("forecast") or {}
    market_context = inputs.get("market_context") or {}
    atr_input = _as_number(inputs.get("atr"))
    drawdown_pct = _as_number(inputs.get("drawdown_pct"))
    factors = {
        "trend": _trend_factor(regime),
        "momentum": _momentum_factor(regime),
        "mean_reversion": _mean_reversion_factor(regime),
        "forecast": _forecast_factor(forecast),
        "model_agreement": _agreement_factor(forecast),
        "sentiment": _sentiment_factor(inputs.get("sentiment")),
        "volatility_risk": _volatility_factor(regime, atr_input, drawdown_pct),
        "market_context": _market_context_factor(market_context),
    }
    available_weight = sum(
        BASE_WEIGHTS[name] for name, data in factors.items() if data is not None
    )
    output_factors: List[Dict[str, object]] = []
    raw_score = 0.0
    missing = 0
    for name in (
        "trend",
        "momentum",
        "mean_reversion",
        "forecast",
        "model_agreement",
        "sentiment",
        "volatility_risk",
        "market_context",
    ):
        data = factors[name]
        if data is None:
            missing += 1
            output_factors.append(_factor_template(name, "Unavailable from inputs."))
            continue
        weight = (
            BASE_WEIGHTS[name] * 100.0 / available_weight if available_weight else 0.0
        )
        contribution = weight * float(data["score"])
        raw_score += contribution
        output_factors.append(
            {
                "name": name,
                "weight": round(weight, 2),
                "score": round(float(data["score"]), 4),
                "contribution": round(contribution, 4),
                "detail": str(data["detail"]),
            }
        )
    return {"factors": output_factors, "raw_score": raw_score, "missing": missing}


def _confidence(inputs: Dict[str, object], missing_factors: int) -> int:
    forecast = inputs.get("forecast") or {}
    base = 72 - (missing_factors * 5)
    accuracy = _as_number(forecast.get("directional_accuracy"))
    agreement = _as_number(forecast.get("agreement"))
    if accuracy is None:
        base -= 10
    else:
        base += int((accuracy - 55) * 0.8)
        if accuracy < 55:
            base -= 12
    if agreement is None:
        base -= 5
    else:
        base += int((agreement - 0.5) * 25)
        if agreement < 0.45:
            base -= 8
    if inputs.get("regime") is None:
        base -= 6
    confidence = int(_clamp(float(base), 15.0, 95.0))
    if accuracy is not None and accuracy < 55:
        confidence = min(confidence, 45)
    return confidence


def _risk_level(
    regime: Dict[str, object],
    market_context: Dict[str, object],
    drawdown_pct: Optional[float],
) -> str:
    volatility = regime.get("volatility")
    atr_pct = _as_number(regime.get("atr_pct"))
    beta = _as_number(market_context.get("beta"))
    risk_score = 0
    if volatility == "high":
        risk_score += 2
    elif volatility == "low":
        risk_score -= 1
    if atr_pct is not None and atr_pct >= 5:
        risk_score += 2
    elif atr_pct is not None and atr_pct <= 2:
        risk_score -= 1
    if drawdown_pct is not None and drawdown_pct >= 20:
        risk_score += 2
    elif drawdown_pct is not None and drawdown_pct <= 8:
        risk_score -= 1
    if beta is not None and beta >= 1.4:
        risk_score += 1
    if risk_score >= 3:
        return "High"
    if risk_score <= -1:
        return "Low"
    return "Medium"


def _levels(
    inputs: Dict[str, object], action: str, final_score: float, holding: Optional[bool]
) -> Dict[str, Optional[float]]:
    last_price = _as_number(inputs.get("last_price"))
    if last_price is None:
        return {
            "entry": None,
            "target": None,
            "stop_loss": None,
            "risk_reward": None,
            "downside_pct": None,
        }
    forecast = inputs.get("forecast") or {}
    expected = _as_number(forecast.get("expected_return_pct"))
    lower = _as_number(forecast.get("lower_pct"))
    upper = _as_number(forecast.get("upper_pct"))
    atr = _as_number(inputs.get("atr"))
    support = _nearest_level(inputs.get("support"), last_price, "support")
    resistance = _nearest_level(inputs.get("resistance"), last_price, "resistance")

    if holding is False and final_score > 0 and support is not None:
        entry = support
    else:
        entry = last_price

    target_candidates = []
    if expected is not None:
        target_candidates.append(last_price * (1 + expected / 100.0))
    if upper is not None:
        target_candidates.append(last_price * (1 + upper / 100.0))
    if resistance is not None:
        target_candidates.append(resistance)
    target = max(target_candidates) if target_candidates else None

    stop_candidates = []
    if support is not None:
        stop_candidates.append(support - (0.5 * atr if atr is not None else 0.0))
    if atr is not None:
        stop_candidates.append(last_price - (1.5 * atr))
    if lower is not None:
        stop_candidates.append(last_price * (1 + lower / 100.0))
    stop_loss = min(stop_candidates) if stop_candidates else None

    if action in ("SELL", "STRONG SELL"):
        target = support if support is not None else target
        if resistance is not None and atr is not None:
            stop_loss = resistance + (0.5 * atr)

    upside_pct = (
        ((target / entry) - 1) * 100
        if target is not None and entry not in (None, 0)
        else None
    )
    downside_pct = (
        ((entry - stop_loss) / entry) * 100
        if stop_loss is not None and entry not in (None, 0)
        else None
    )
    risk_reward = (
        upside_pct / downside_pct
        if upside_pct is not None and downside_pct not in (None, 0)
        else None
    )
    return {
        "entry": _round_or_none(entry, 4),
        "target": _round_or_none(target, 4),
        "stop_loss": _round_or_none(stop_loss, 4),
        "risk_reward": _round_or_none(risk_reward, 4),
        "downside_pct": _round_or_none(downside_pct, 4),
    }


def _action_from_score(score: float, confidence: int) -> str:
    if confidence < 40 and abs(score) < 55:
        return "HOLD"
    if confidence < 55 and abs(score) < 35:
        return "HOLD"
    if score >= 55:
        return "STRONG BUY"
    if score >= 25:
        return "BUY"
    if score <= -55:
        return "STRONG SELL"
    if score <= -25:
        return "SELL"
    return "HOLD"


def _position_advice(
    action: str,
    holding: Optional[bool],
    entry: Optional[float],
    support: Optional[float],
) -> str:
    wait_level = support if support is not None else entry
    if holding is True:
        if action in ("STRONG BUY", "BUY"):
            return "Hold existing shares and consider adding on controlled pullbacks."
        if action == "HOLD":
            return "Hold current position; wait for clearer confirmation before changing exposure."
        return "Consider reducing exposure or taking profits to manage downside risk."
    if holding is False:
        if action in ("STRONG BUY", "BUY"):
            if wait_level is not None:
                return f"Wait for entry near {wait_level:.2f} or better before adding new exposure."
            return "Consider scaling in gradually rather than chasing strength."
        if action == "HOLD":
            return "Wait for a better setup before opening a new position."
        return "Stay on the sidelines unless conditions improve materially."
    if action in ("STRONG BUY", "BUY"):
        return "Constructive setup, but size positions prudently around support."
    if action == "HOLD":
        return "Balanced setup; patience is reasonable until conviction improves."
    return "Defensive posture is appropriate until the trend stabilizes."


def _reasons(
    inputs: Dict[str, object], action: str, confidence: int, score: float
) -> List[str]:
    regime = inputs.get("regime") or {}
    forecast = inputs.get("forecast") or {}
    reasons: List[str] = []
    trend = regime.get("trend")
    momentum = regime.get("momentum")
    if trend:
        reasons.append(f"Trend reads {trend} with momentum {momentum or 'mixed'}.")
    rsi = _as_number(regime.get("rsi"))
    if rsi is not None:
        if rsi >= 70:
            reasons.append(
                f"RSI {rsi:.0f} overbought, so upside may need a pullback first."
            )
        elif rsi <= 30:
            reasons.append(f"RSI {rsi:.0f} oversold, supporting rebound potential.")
    expected = _as_number(forecast.get("expected_return_pct"))
    horizon = int(forecast.get("horizon_days") or 30)
    accuracy = _as_number(forecast.get("directional_accuracy"))
    if expected is not None:
        text = f"Ensemble projects {expected:+.1f}% over {horizon}d."
        if accuracy is not None:
            text += f" Backtest directional accuracy is {accuracy:.0f}%."
        reasons.append(text)
    if confidence < 55:
        reasons.append(
            f"Confidence is only {confidence}%, which tempers conviction and pulls the call toward HOLD."
        )
    reasons.append(f"Composite score is {score:.1f}, mapping to {action}.")
    return reasons[:4]


def score_recommendation(
    inputs: Dict[str, object], holding: Optional[bool] = None
) -> Dict[str, object]:
    inputs = inputs or {}
    factor_data = _build_factors(inputs)
    confidence = _confidence(inputs, int(factor_data["missing"]))
    score = float(factor_data["raw_score"])

    forecast = inputs.get("forecast") or {}
    accuracy = _as_number(forecast.get("directional_accuracy"))
    agreement = _as_number(forecast.get("agreement"))
    score *= confidence / 100.0
    if accuracy is not None and accuracy < 55:
        score *= 0.55
    if agreement is not None and agreement < 0.45:
        score *= 0.8
    score = _clamp(score, -100.0, 100.0)
    action = _action_from_score(score, confidence)

    regime = inputs.get("regime") or {}
    market_context = inputs.get("market_context") or {}
    drawdown_pct = _as_number(inputs.get("drawdown_pct"))
    risk_level = _risk_level(regime, market_context, drawdown_pct)
    levels = _levels(inputs, action, score, holding)
    support = _nearest_level(
        inputs.get("support"), _as_number(inputs.get("last_price")), "support"
    )

    return {
        "action": action,
        "score": round(score, 4),
        "confidence": confidence,
        "horizon_days": int(forecast.get("horizon_days") or 30),
        "risk_level": risk_level,
        "entry": levels["entry"],
        "target": levels["target"],
        "stop_loss": levels["stop_loss"],
        "risk_reward": levels["risk_reward"],
        "expected_return_pct": _round_or_none(
            _as_number(forecast.get("expected_return_pct"))
        ),
        "downside_pct": levels["downside_pct"],
        "factors": factor_data["factors"],
        "reasons": _reasons(inputs, action, confidence, score),
        "position_advice": _position_advice(action, holding, levels["entry"], support),
        "disclaimer": DISCLAIMER,
    }
