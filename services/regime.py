from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _empty_regime() -> Dict[str, object]:
    return {
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
    }


def _sanitize_frame(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    if any(column not in frame.columns for column in REQUIRED_COLUMNS):
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    clean = frame.loc[:, REQUIRED_COLUMNS].copy()
    clean = clean.apply(pd.to_numeric, errors="coerce")
    clean = clean.dropna(subset=["open", "high", "low", "close"])
    if clean.empty:
        return clean
    return clean.sort_index()


def _safe_float(value: object) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    cast = float(value)
    if not np.isfinite(cast):
        return None
    return round(cast, 4)


def _series_percentile(series: pd.Series) -> Optional[float]:
    clean = series.dropna()
    if len(clean) < 20:
        return None
    latest = clean.iloc[-1]
    if pd.isna(latest):
        return None
    return float((clean <= latest).mean())


def _compute_atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def _compute_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss.notna(), np.nan)


def _compute_macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    return macd - signal


def _compute_bollinger_pct_b(close: pd.Series, window: int = 20) -> pd.Series:
    sma = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper = sma + (2 * std)
    lower = sma - (2 * std)
    width = upper - lower
    pct_b = (close - lower) / width.replace(0, np.nan)
    return pct_b


def _returns(close: pd.Series) -> Dict[str, Optional[float]]:
    output: Dict[str, Optional[float]] = {}
    for label, periods in {"1d": 1, "7d": 7, "30d": 30, "90d": 90}.items():
        if len(close) <= periods:
            output[label] = None
            continue
        base = close.iloc[-(periods + 1)]
        latest = close.iloc[-1]
        if pd.isna(base) or base == 0 or pd.isna(latest):
            output[label] = None
            continue
        output[label] = round(((latest / base) - 1) * 100, 4)
    return output


def _classify_trend(frame: pd.DataFrame) -> Optional[str]:
    if len(frame) < 50:
        return None
    close = frame["close"]
    sma20 = close.rolling(window=20, min_periods=20).mean()
    sma50 = close.rolling(window=50, min_periods=50).mean()
    latest_close = close.iloc[-1]
    latest_sma20 = sma20.iloc[-1]
    latest_sma50 = sma50.iloc[-1]
    ret20 = ((latest_close / close.iloc[-21]) - 1) * 100 if len(close) > 20 else 0.0
    dist_sma20 = ((latest_close / latest_sma20) - 1) * 100 if latest_sma20 else 0.0

    if (
        pd.notna(latest_sma20)
        and pd.notna(latest_sma50)
        and latest_close > latest_sma20 > latest_sma50
        and ret20 > 2.0
    ):
        return "uptrend"
    if (
        pd.notna(latest_sma20)
        and pd.notna(latest_sma50)
        and latest_close < latest_sma20 < latest_sma50
        and ret20 < -2.0
    ):
        return "downtrend"
    if abs(ret20) <= 2.5 or abs(dist_sma20) <= 1.5:
        return "range"
    return "uptrend" if ret20 > 0 else "downtrend"


def _classify_momentum(rsi: pd.Series, macd_hist: pd.Series) -> Optional[str]:
    latest_rsi = rsi.iloc[-1] if len(rsi) else np.nan
    prev_rsi = rsi.iloc[-2] if len(rsi) > 1 else np.nan
    latest_hist = macd_hist.iloc[-1] if len(macd_hist) else np.nan
    prev_hist = macd_hist.iloc[-2] if len(macd_hist) > 1 else np.nan
    if pd.isna(latest_rsi) and pd.isna(latest_hist):
        return None
    if (
        pd.notna(latest_hist)
        and pd.notna(prev_hist)
        and latest_hist > 0
        and latest_hist >= prev_hist
    ) or (
        pd.notna(latest_rsi)
        and pd.notna(prev_rsi)
        and latest_rsi > 55
        and latest_rsi >= prev_rsi
    ):
        return "strengthening"
    if (
        pd.notna(latest_hist)
        and pd.notna(prev_hist)
        and latest_hist < 0
        and latest_hist <= prev_hist
    ) or (
        pd.notna(latest_rsi)
        and pd.notna(prev_rsi)
        and latest_rsi < 45
        and latest_rsi <= prev_rsi
    ):
        return "weakening"
    return "neutral"


def _classify_volatility(close: pd.Series, atr_pct: pd.Series) -> Optional[str]:
    realised = close.pct_change().rolling(window=20, min_periods=20).std(ddof=0)
    realised = realised * np.sqrt(252) * 100
    atr_rank = _series_percentile(atr_pct)
    realised_rank = _series_percentile(realised)
    ranks = [rank for rank in (atr_rank, realised_rank) if rank is not None]
    if not ranks:
        return None
    composite = float(np.mean(ranks))
    if composite <= 0.33:
        return "low"
    if composite >= 0.67:
        return "high"
    return "normal"


def _sma_cross(close: pd.Series) -> Optional[str]:
    sma50 = close.rolling(window=50, min_periods=50).mean()
    sma200 = close.rolling(window=200, min_periods=200).mean()
    if len(close) < 200 or pd.isna(sma50.iloc[-1]) or pd.isna(sma200.iloc[-1]):
        return None
    prev50 = sma50.iloc[-2]
    prev200 = sma200.iloc[-2]
    cur50 = sma50.iloc[-1]
    cur200 = sma200.iloc[-1]
    if pd.notna(prev50) and pd.notna(prev200) and prev50 <= prev200 and cur50 > cur200:
        return "golden"
    if pd.notna(prev50) and pd.notna(prev200) and prev50 >= prev200 and cur50 < cur200:
        return "death"
    if cur50 > cur200:
        return "bullish"
    if cur50 < cur200:
        return "bearish"
    return None


def classify_regime(frame: Optional[pd.DataFrame]) -> Dict[str, object]:
    regime = _empty_regime()
    clean = _sanitize_frame(frame)
    if clean.empty:
        return regime

    close = clean["close"]
    atr_series = _compute_atr(clean)
    atr_pct_series = (atr_series / close.replace(0, np.nan)) * 100
    rsi_series = _compute_rsi(close)
    macd_hist_series = _compute_macd_hist(close)
    bollinger_pct_b_series = _compute_bollinger_pct_b(close)

    lookback = min(len(clean), 252)
    high_52w = clean["high"].tail(lookback).max() if lookback else np.nan
    low_52w = clean["low"].tail(lookback).min() if lookback else np.nan
    latest_close = close.iloc[-1]

    regime.update(
        {
            "trend": _classify_trend(clean),
            "momentum": _classify_momentum(rsi_series, macd_hist_series),
            "volatility": _classify_volatility(close, atr_pct_series),
            "atr": _safe_float(atr_series.iloc[-1] if len(atr_series) else None),
            "atr_pct": _safe_float(
                atr_pct_series.iloc[-1] if len(atr_pct_series) else None
            ),
            "rsi": _safe_float(rsi_series.iloc[-1] if len(rsi_series) else None),
            "macd_hist": _safe_float(
                macd_hist_series.iloc[-1] if len(macd_hist_series) else None
            ),
            "bollinger_pct_b": _safe_float(
                bollinger_pct_b_series.iloc[-1] if len(bollinger_pct_b_series) else None
            ),
            "dist_52w_high_pct": _safe_float(
                ((latest_close / high_52w) - 1) * 100 if high_52w else None
            ),
            "dist_52w_low_pct": _safe_float(
                ((latest_close / low_52w) - 1) * 100 if low_52w else None
            ),
            "sma_cross": _sma_cross(close),
            "returns": _returns(close),
        }
    )
    return regime


def _swing_levels(frame: pd.DataFrame, window: int = 2) -> Dict[str, List[float]]:
    lows: List[float] = []
    highs: List[float] = []
    if len(frame) < (window * 2 + 1):
        return {"support": lows, "resistance": highs}
    for index in range(window, len(frame) - window):
        low_slice = frame["low"].iloc[index - window : index + window + 1]
        high_slice = frame["high"].iloc[index - window : index + window + 1]
        low_value = frame["low"].iloc[index]
        high_value = frame["high"].iloc[index]
        if low_value == low_slice.min() and (low_slice == low_value).sum() == 1:
            lows.append(float(low_value))
        if high_value == high_slice.max() and (high_slice == high_value).sum() == 1:
            highs.append(float(high_value))
    return {"support": lows, "resistance": highs}


def _pivot_levels(frame: pd.DataFrame) -> Dict[str, List[float]]:
    if frame.empty:
        return {"support": [], "resistance": []}
    basis = frame.iloc[-2] if len(frame) > 1 else frame.iloc[-1]
    pivot = (basis["high"] + basis["low"] + basis["close"]) / 3
    spread = basis["high"] - basis["low"]
    supports = [
        (2 * pivot) - basis["high"],
        pivot - spread,
        basis["low"] - 2 * (basis["high"] - pivot),
    ]
    resistances = [
        (2 * pivot) - basis["low"],
        pivot + spread,
        basis["high"] + 2 * (pivot - basis["low"]),
    ]
    return {
        "support": [float(level) for level in supports if np.isfinite(level)],
        "resistance": [float(level) for level in resistances if np.isfinite(level)],
    }


def _dedupe_levels(levels: List[float]) -> List[float]:
    seen = set()
    ordered: List[float] = []
    for level in levels:
        key = round(level, 4)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(round(float(level), 4))
    return ordered


def support_resistance(
    frame: Optional[pd.DataFrame], n: int = 3
) -> Dict[str, List[float]]:
    clean = _sanitize_frame(frame)
    if clean.empty or n <= 0:
        return {"support": [], "resistance": []}

    last_close = float(clean["close"].iloc[-1])
    swing_levels = _swing_levels(clean)
    pivot_levels = _pivot_levels(clean)
    supports = _dedupe_levels(swing_levels["support"] + pivot_levels["support"])
    resistances = _dedupe_levels(
        swing_levels["resistance"] + pivot_levels["resistance"]
    )

    supports = [level for level in supports if level < last_close]
    resistances = [level for level in resistances if level > last_close]
    supports.sort(key=lambda level: abs(last_close - level))
    resistances.sort(key=lambda level: abs(level - last_close))

    return {"support": supports[:n], "resistance": resistances[:n]}
