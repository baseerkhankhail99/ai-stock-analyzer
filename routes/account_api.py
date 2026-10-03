"""Admin (user management, audit) and per-user (watchlist, alerts, portfolio) APIs.

Permissions are enforced centrally in ``auth.load_user_and_gate`` (API_RULES).
"""

import csv
import io

from flask import Blueprint, Response, jsonify, request

import auth
from services import market_data, user_data

account_api = Blueprint("account_api", __name__, url_prefix="/api")


def _json():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _result(error, ok_body=None, status=200):
    if error:
        return jsonify({"error": error}), 400
    return jsonify(ok_body or {"ok": True}), status


@account_api.route("/me", methods=["GET"])
def me():
    user = auth.current_user()
    return jsonify(
        {
            "username": user.username,
            "role": user.role,
            "permissions": sorted(auth.PERMISSIONS[user.role]),
        }
    )


@account_api.route("/me/password", methods=["POST"])
def me_password():
    body = _json()
    error = auth.change_password(
        auth.current_user(), body.get("current"), body.get("new"), body.get("confirm")
    )
    return _result(error)


# ------------------------------------------------------------------- admin


@account_api.route("/admin/users", methods=["GET"])
def admin_list_users():
    return jsonify({"users": auth.list_users()})


@account_api.route("/admin/users", methods=["POST"])
def admin_create_user():
    body = _json()
    user, error = auth.create_user(
        body.get("username"),
        body.get("password"),
        body.get("role", "viewer"),
        auth.current_user().username,
    )
    return _result(error, {"id": user.id if user else None}, 201)


@account_api.route("/admin/users/<int:user_id>", methods=["PATCH"])
def admin_update_user(user_id):
    body = _json()
    actor = auth.current_user().username
    error = None
    if "role" in body:
        error = auth.set_role(user_id, body["role"], actor)
    if not error and "is_active" in body:
        error = auth.set_active(user_id, bool(body["is_active"]), actor)
    return _result(error)


@account_api.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
def admin_reset_password(user_id):
    return _result(
        auth.reset_password(
            user_id, _json().get("password"), auth.current_user().username
        )
    )


@account_api.route("/admin/users/<int:user_id>", methods=["DELETE"])
def admin_delete_user(user_id):
    return _result(auth.delete_user(user_id, auth.current_user().username))


@account_api.route("/admin/audit", methods=["GET"])
def admin_audit():
    return jsonify({"entries": auth.recent_audit()})


# ----------------------------------------------------------- personal data


@account_api.route("/me/watchlist", methods=["GET"])
def watchlist_get():
    return jsonify({"symbols": user_data.get_watchlist(auth.current_user().id)})


@account_api.route("/me/watchlist", methods=["POST"])
def watchlist_toggle():
    pinned, error = user_data.toggle_watchlist(
        auth.current_user().id, _json().get("symbol")
    )
    return _result(error, {"pinned": pinned})


@account_api.route("/me/alerts", methods=["GET"])
def alerts_get():
    return jsonify({"alerts": user_data.list_alerts(auth.current_user().id)})


@account_api.route("/me/alerts", methods=["POST"])
def alerts_add():
    body = _json()
    alert_id, error = user_data.add_alert(
        auth.current_user().id,
        body.get("symbol"),
        body.get("direction"),
        body.get("threshold"),
    )
    return _result(error, {"id": alert_id}, 201)


@account_api.route("/me/alerts/<int:alert_id>", methods=["DELETE"])
def alerts_delete(alert_id):
    ok = user_data.delete_alert(auth.current_user().id, alert_id)
    return _result(None if ok else "Alert not found")


@account_api.route("/me/portfolio", methods=["GET"])
def portfolio_get():
    return jsonify({"holdings": user_data.list_holdings(auth.current_user().id)})


@account_api.route("/me/portfolio", methods=["POST"])
def portfolio_add():
    body = _json()
    holding_id, error = user_data.add_holding(
        auth.current_user().id,
        body.get("symbol"),
        body.get("quantity"),
        body.get("cost_basis"),
    )
    return _result(error, {"id": holding_id}, 201)


@account_api.route("/me/portfolio/<int:holding_id>", methods=["DELETE"])
def portfolio_delete(holding_id):
    ok = user_data.delete_holding(auth.current_user().id, holding_id)
    return _result(None if ok else "Holding not found")


# ------------------------------------------------------------------ export


@account_api.route("/export/history/<symbol>.csv", methods=["GET"])
def export_history(symbol):
    symbol = market_data.normalize_symbol(symbol)
    if not market_data.is_valid_symbol(symbol):
        return jsonify({"error": "Invalid symbol"}), 400
    frame = market_data.fetch_history(symbol)["frame"]
    if frame.empty:
        return jsonify({"error": "No data found"}), 404
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "open", "high", "low", "close", "volume"])
    for ts, row in frame.iterrows():
        writer.writerow(
            [ts.date().isoformat()]
            + [row[c] for c in ("open", "high", "low", "close", "volume")]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{symbol}.csv"'},
    )
