"""Per-user data: watchlist, price alerts and portfolio holdings."""

from datetime import datetime
from typing import Dict, List, Optional

from models import PortfolioHolding, PriceAlert, WatchlistItem, db
from services import market_data

MAX_ITEMS = 100


def _symbol(symbol: str) -> Optional[str]:
    symbol = market_data.normalize_symbol(symbol)
    return symbol if market_data.is_valid_symbol(symbol) else None


# ---------------------------------------------------------------- watchlist


def get_watchlist(user_id: int) -> List[str]:
    rows = WatchlistItem.query.filter_by(user_id=user_id).order_by(WatchlistItem.id)
    return [r.symbol for r in rows]


def toggle_watchlist(user_id: int, symbol: str):
    """Returns (pinned: bool, error)."""
    symbol = _symbol(symbol)
    if not symbol:
        return False, "Invalid symbol"
    row = WatchlistItem.query.filter_by(user_id=user_id, symbol=symbol).first()
    if row:
        db.session.delete(row)
        db.session.commit()
        return False, None
    if WatchlistItem.query.filter_by(user_id=user_id).count() >= MAX_ITEMS:
        return False, "Watchlist is full"
    db.session.add(WatchlistItem(user_id=user_id, symbol=symbol))
    db.session.commit()
    return True, None


# ------------------------------------------------------------------- alerts


def list_alerts(user_id: int) -> List[Dict]:
    rows = PriceAlert.query.filter_by(user_id=user_id).order_by(PriceAlert.id.desc())
    return [
        {
            "id": a.id,
            "symbol": a.symbol,
            "direction": a.direction,
            "threshold": a.threshold,
            "active": a.active,
            "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
        }
        for a in rows
    ]


def add_alert(user_id: int, symbol: str, direction: str, threshold):
    symbol = _symbol(symbol)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return None, "Threshold must be a number"
    if not symbol or direction not in ("above", "below") or threshold <= 0:
        return None, "Invalid alert"
    if PriceAlert.query.filter_by(user_id=user_id).count() >= MAX_ITEMS:
        return None, "Too many alerts"
    alert = PriceAlert(
        user_id=user_id, symbol=symbol, direction=direction, threshold=threshold
    )
    db.session.add(alert)
    db.session.commit()
    return alert.id, None


def delete_alert(user_id: int, alert_id: int) -> bool:
    alert = PriceAlert.query.filter_by(user_id=user_id, id=alert_id).first()
    if not alert:
        return False
    db.session.delete(alert)
    db.session.commit()
    return True


def check_alerts(user_id: int, prices: Dict[str, float]) -> List[Dict]:
    """Trigger active alerts crossed by the given prices; returns the new hits."""
    hits = []
    for alert in PriceAlert.query.filter_by(user_id=user_id, active=True):
        price = prices.get(alert.symbol)
        if price is None:
            continue
        crossed = (alert.direction == "above" and price >= alert.threshold) or (
            alert.direction == "below" and price <= alert.threshold
        )
        if crossed:
            alert.active, alert.triggered_at = False, datetime.utcnow()
            hits.append(
                {
                    "symbol": alert.symbol,
                    "direction": alert.direction,
                    "threshold": alert.threshold,
                    "price": price,
                }
            )
    if hits:
        db.session.commit()
    return hits


# ---------------------------------------------------------------- portfolio


def list_holdings(user_id: int) -> List[Dict]:
    rows = PortfolioHolding.query.filter_by(user_id=user_id).order_by(
        PortfolioHolding.id
    )
    return [
        {
            "id": h.id,
            "symbol": h.symbol,
            "quantity": h.quantity,
            "cost_basis": h.cost_basis,
        }
        for h in rows
    ]


def add_holding(user_id: int, symbol: str, quantity, cost_basis):
    symbol = _symbol(symbol)
    try:
        quantity, cost_basis = float(quantity), float(cost_basis)
    except (TypeError, ValueError):
        return None, "Quantity and cost must be numbers"
    if not symbol or quantity <= 0 or cost_basis < 0:
        return None, "Invalid holding"
    if PortfolioHolding.query.filter_by(user_id=user_id).count() >= MAX_ITEMS:
        return None, "Too many holdings"
    holding = PortfolioHolding(
        user_id=user_id, symbol=symbol, quantity=quantity, cost_basis=cost_basis
    )
    db.session.add(holding)
    db.session.commit()
    return holding.id, None


def delete_holding(user_id: int, holding_id: int) -> bool:
    row = PortfolioHolding.query.filter_by(user_id=user_id, id=holding_id).first()
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def portfolio_summary(holdings: List[Dict], prices: Dict[str, float]) -> Dict:
    """Live P/L and allocation from holdings and the latest prices."""
    rows, total_value, total_cost = [], 0.0, 0.0
    for h in holdings:
        price = prices.get(h["symbol"])
        cost = h["quantity"] * h["cost_basis"]
        value = h["quantity"] * price if price is not None else None
        rows.append(
            {
                **h,
                "price": price,
                "value": value,
                "pnl": value - cost if value is not None else None,
                "pnl_pct": (value - cost) / cost * 100
                if value is not None and cost
                else None,
            }
        )
        total_cost += cost
        total_value += value or 0.0
    for row in rows:
        row["allocation_pct"] = (
            row["value"] / total_value * 100 if row["value"] and total_value else 0.0
        )
    return {
        "rows": rows,
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl": total_value - total_cost,
        "pnl_pct": (total_value - total_cost) / total_cost * 100 if total_cost else 0.0,
    }
