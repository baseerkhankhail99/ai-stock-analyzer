"""Authentication, roles (RBAC), user management and audit log.

Server-side session auth (signed cookie). Everything except /login, /healthz
and the dashboard's static assets requires a signed-in, active user. The
permission map below is enforced for every /api route and by the Dash
callbacks (via ``require_permission``), never only by hiding UI.
"""

import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from html import escape
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    session,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from models import AuditLog, User, db

logger = logging.getLogger(__name__)

ROLES = ("admin", "analyst", "viewer")
PERMISSIONS = {
    "admin": {
        "view",
        "analysis",
        "forecast",
        "compare",
        "export",
        "personal",
        "users",
        "settings",
        "diagnostics",
        "audit",
    },
    "analyst": {"view", "analysis", "forecast", "compare", "export", "personal"},
    "viewer": {"view", "analysis"},
}

MIN_PASSWORD_LENGTH = 10
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
IP_LIMIT_ATTEMPTS = 20
IP_LIMIT_WINDOW_SECONDS = 600
SESSION_HOURS = 8
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
GENERIC_LOGIN_ERROR = (
    "Invalid username or password, or the account is temporarily locked."
)
INSECURE_KEYS = {"", "your-secret-key-change-in-production", "dev", "changeme"}
PUBLIC_PATHS = {"/login", "/healthz", "/favicon.ico"}
PUBLIC_PREFIXES = ("/dashboard/assets/",)
CSRF_EXEMPT_METHODS = {"GET", "HEAD", "OPTIONS"}

# (path regex, permission) in match order for /api routes; default is "view".
API_RULES = [
    (r"^/api/admin/audit", "audit"),
    (r"^/api/admin/", "users"),
    (r"^/api/health/data", "diagnostics"),
    (r"^/api/stocks/sync", "settings"),
    (r"^/api/stocks/indicators/calculate", "settings"),
    (r"^/api/export/", "export"),
    (r"^/api/me/(watchlist|alerts|portfolio)", "personal"),
    (r"^/api/stocks/compare", "compare"),
    (r"^/api/market/compare", "compare"),
    (r"^/api/stocks/[^/]+/(forecast|forecasts|price-targets)", "forecast"),
    (r"^/api/market/(recommendation|forecast)/", "forecast"),
    (
        r"^/api/(market/analysis|stocks/[^/]+/(indicators|signals|analytics))",
        "analysis",
    ),
]

auth_bp = Blueprint("auth", __name__)

_ip_attempts = defaultdict(deque)
_ip_lock = threading.Lock()
_DUMMY_HASH = generate_password_hash(secrets.token_hex(8))


# ---------------------------------------------------------------- helpers


def has_permission(role, permission):
    return permission in PERMISSIONS.get(role or "", set())


def current_user():
    return getattr(g, "user", None)


def can(permission):
    user = current_user()
    return bool(user) and has_permission(user.role, permission)


def require_permission(permission):
    """True when the signed-in user holds the permission (for Dash callbacks)."""
    return can(permission)


def permission_required(permission):
    """Decorator for Flask views: 401 JSON when anonymous, 403 when not allowed."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                return jsonify({"error": "Authentication required"}), 401
            if not can(permission):
                return jsonify({"error": "Forbidden"}), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


def client_ip():
    return (request.remote_addr or "unknown")[:64]


def audit(action, actor=None, target=None):
    """Best-effort audit row; never raises and never records passwords."""
    try:
        db.session.add(
            AuditLog(
                actor=(actor or "")[:64] or None,
                action=action,
                target=(target or "")[:128] or None,
                ip=client_ip() if request else None,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Audit write failed: %s", type(exc).__name__)


def is_production(config_name=None):
    return (
        config_name == "production"
        or os.getenv("RENDER", "").lower() == "true"
        or bool(os.getenv("SPACE_ID"))
    )


def safe_next(target):
    """Only same-site relative paths are allowed as post-login redirects."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    parts = urlsplit(target)
    return target if not parts.scheme and not parts.netloc else "/"


def validate_password(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def validate_username(username):
    if not USERNAME_PATTERN.match(username or ""):
        return "Username must be 3-32 characters (letters, digits, . _ -)."
    return None


def get_user_by_name(username):
    key = (username or "").strip().lower()
    return User.query.filter_by(username_key=key).first() if key else None


# ------------------------------------------------------------ user service


def active_admin_count():
    return User.query.filter_by(role="admin", is_active=True).count()


def create_user(username, password, role, actor=None, must_change=True):
    """Returns (user, error)."""
    username = (username or "").strip()
    error = validate_username(username) or validate_password(password)
    if error:
        return None, error
    if role not in ROLES:
        return None, "Invalid role."
    if get_user_by_name(username):
        return None, "Username already exists."
    user = User(
        username=username,
        username_key=username.lower(),
        password_hash=generate_password_hash(password),
        role=role,
        must_change_password=must_change,
    )
    db.session.add(user)
    db.session.commit()
    audit("user_created", actor, f"{username} ({role})")
    return user, None


def _guard_last_admin(user, actor_name, action):
    """Error text when the change would remove the last active admin / self."""
    if user.username_key == (actor_name or "").lower() and action != "reset":
        return "You cannot change your own role, status or account here."
    if user.role == "admin" and user.is_active and active_admin_count() <= 1:
        return "At least one active admin must remain."
    return None


def set_role(user_id, role, actor):
    user = db.session.get(User, user_id)
    if user is None or role not in ROLES:
        return "User or role not found."
    if user.role == role:
        return None
    if role != "admin":
        error = _guard_last_admin(user, actor, "role")
        if error:
            return error
    user.role = role
    db.session.commit()
    audit("role_changed", actor, f"{user.username} -> {role}")
    return None


def set_active(user_id, active, actor):
    user = db.session.get(User, user_id)
    if user is None:
        return "User not found."
    if not active:
        error = _guard_last_admin(user, actor, "deactivate")
        if error:
            return error
    user.is_active = bool(active)
    if active:
        user.failed_attempts, user.locked_until = 0, None
    db.session.commit()
    audit("user_activated" if active else "user_deactivated", actor, user.username)
    return None


def reset_password(user_id, new_password, actor):
    user = db.session.get(User, user_id)
    if user is None:
        return "User not found."
    error = validate_password(new_password)
    if error:
        return error
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = True
    user.failed_attempts, user.locked_until = 0, None
    db.session.commit()
    audit("password_reset", actor, user.username)
    return None


def delete_user(user_id, actor):
    user = db.session.get(User, user_id)
    if user is None:
        return "User not found."
    error = _guard_last_admin(user, actor, "delete")
    if error:
        return error
    name = user.username
    for model in _owned_models():
        model.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    audit("user_deleted", actor, name)
    return None


def _owned_models():
    from models import PortfolioHolding, PriceAlert, WatchlistItem

    return (WatchlistItem, PriceAlert, PortfolioHolding)


def change_password(user, current, new, confirm=None):
    if not check_password_hash(user.password_hash, current or ""):
        audit("password_change_failed", user.username)
        return "Current password is incorrect."
    error = validate_password(new)
    if error:
        return error
    if confirm is not None and confirm != new:
        return "New passwords do not match."
    if check_password_hash(user.password_hash, new):
        return "Choose a password different from the current one."
    user.password_hash = generate_password_hash(new)
    user.must_change_password = False
    db.session.commit()
    audit("password_changed", user.username)
    return None


def list_users():
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "must_change_password": u.must_change_password,
            "locked": bool(u.locked_until and u.locked_until > datetime.utcnow()),
        }
        for u in User.query.order_by(User.username_key).all()
    ]


def recent_audit(limit=100):
    rows = AuditLog.query.order_by(AuditLog.id.desc()).limit(limit).all()
    return [
        {
            "when": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "who": r.actor,
            "action": r.action,
            "target": r.target,
            "ip": r.ip,
        }
        for r in rows
    ]


def seed_master_user(app):
    """Create the master admin once; an existing user is never modified."""
    username = (os.getenv("MASTER_USERNAME") or "Baseer").strip()
    with app.app_context():
        try:
            if get_user_by_name(username):
                return False
            password = os.getenv("MASTER_PASSWORD")
            generated = not password
            if generated:
                password = secrets.token_urlsafe(18)
            elif len(password) < MIN_PASSWORD_LENGTH:
                logger.warning(
                    "MASTER_PASSWORD is shorter than %d", MIN_PASSWORD_LENGTH
                )
            db.session.add(
                User(
                    username=username,
                    username_key=username.lower(),
                    password_hash=generate_password_hash(password),
                    role="admin",
                    must_change_password=generated,
                )
            )
            db.session.commit()
            if generated:
                logger.warning(
                    "MASTER_PASSWORD is not set. Created master user '%s' with a "
                    "random password, shown ONCE: %s (you must change it at first "
                    "login)",
                    username,
                    password,
                )
            else:
                logger.info("Created master user '%s' from MASTER_PASSWORD", username)
            return True
        except Exception as exc:
            db.session.rollback()
            logger.error("Could not seed master user: %s", exc)
            return False


# ----------------------------------------------------------- login throttle


def ip_rate_limited(ip, now=None):
    now = time.monotonic() if now is None else now
    with _ip_lock:
        attempts = _ip_attempts[ip]
        while attempts and now - attempts[0] > IP_LIMIT_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= IP_LIMIT_ATTEMPTS:
            return True
        attempts.append(now)
        return False


def reset_ip_limits():
    with _ip_lock:
        _ip_attempts.clear()


def authenticate(username, password):
    """Returns (user, error). One generic error for every failure mode."""
    if ip_rate_limited(client_ip()):
        audit("login_rate_limited", None, (username or "")[:64])
        return None, "Too many attempts. Please wait a few minutes."
    user = get_user_by_name(username)
    now = datetime.utcnow()
    if user is None:
        check_password_hash(_DUMMY_HASH, password or "")  # constant-ish timing
        audit("login_failed", None, (username or "")[:64])
        return None, GENERIC_LOGIN_ERROR
    if user.locked_until and user.locked_until > now:
        audit("login_blocked_locked", user.username)
        return None, GENERIC_LOGIN_ERROR
    if (
        not check_password_hash(user.password_hash, password or "")
        or not user.is_active
    ):
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + LOCKOUT
            user.failed_attempts = 0
            audit("account_locked", user.username)
        db.session.commit()
        audit("login_failed", user.username)
        return None, GENERIC_LOGIN_ERROR
    user.failed_attempts, user.locked_until, user.last_login_at = 0, None, now
    db.session.commit()
    audit("login_success", user.username)
    return user, None


# ------------------------------------------------------------------- pages

PAGE_CSS = """
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(circle at 20% 10%,#1e293b,#0b1120 60%);color:#e2e8f0;
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.card{width:min(380px,92vw);padding:32px;border-radius:16px;background:rgba(30,41,59,.72);
border:1px solid rgba(148,163,184,.25);box-shadow:0 20px 50px rgba(0,0,0,.45)}
h1{margin:0 0 4px;font-size:22px}p.sub{margin:0 0 20px;color:#94a3b8;font-size:14px}
label{display:block;margin:14px 0 6px;font-size:13px;color:#cbd5e1}
input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:11px 12px;
border-radius:8px;border:1px solid #334155;background:#0f172a;color:#f8fafc;font-size:15px}
input:focus{outline:2px solid #3b82f6;border-color:#3b82f6}
button{width:100%;margin-top:20px;padding:12px;border:0;border-radius:8px;background:#3b82f6;
color:#fff;font-size:15px;font-weight:600;cursor:pointer}button:hover{background:#2563eb}
.err{margin:14px 0 0;padding:10px;border-radius:8px;background:rgba(239,68,68,.15);
color:#fca5a5;font-size:14px}.ok{background:rgba(16,185,129,.15);color:#6ee7b7}
.chk{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:13px;color:#94a3b8}
small{display:block;margin-top:18px;color:#64748b;font-size:12px;text-align:center}
"""


def _page(title, body):
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · AI Market Analyzer</title>"
        f"<style>{PAGE_CSS}</style></head><body><main class='card'>{body}</main>"
        "</body></html>"
    )
    return Response(html, mimetype="text/html")


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token
    return token


def csrf_valid(supplied):
    if not current_app.config.get("CSRF_ENABLED", True):
        return True
    expected = session.get("csrf")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _csrf_field():
    return f'<input type="hidden" name="csrf_token" value="{escape(csrf_token())}">'


def _message(text, ok=False):
    return (
        f'<div class="err{" ok" if ok else ""}" role="alert">{escape(text)}</div>'
        if text
        else ""
    )


def _login_page(error="", next_url="/", status=200):
    body = (
        "<h1>📈 AI Market Analyzer</h1><p class='sub'>Sign in to continue</p>"
        f'<form method="post" action="/login">{_csrf_field()}'
        f'<input type="hidden" name="next" value="{escape(next_url)}">'
        '<label for="u">Username</label><input id="u" name="username" type="text" '
        'autocomplete="username" required autofocus>'
        '<label for="p">Password</label><input id="p" name="password" '
        'type="password" autocomplete="current-password" required>'
        f"{_message(error)}<button type='submit'>Sign in</button></form>"
        "<small>Educational tool. Not financial advice.</small>"
    )
    response = _page("Sign in", body)
    response.status_code = status
    return response


def _password_page(error="", ok=False, forced=False):
    intro = (
        "You must change your password before continuing."
        if forced
        else "Choose a new password (min 10 characters)."
    )
    body = (
        f"<h1>Change password</h1><p class='sub'>{intro}</p>"
        f'<form method="post" action="/change-password">{_csrf_field()}'
        '<label for="c">Current password</label><input id="c" name="current" '
        'type="password" autocomplete="current-password" required>'
        '<label for="n">New password</label><input id="n" name="new" '
        'type="password" autocomplete="new-password" required minlength="10">'
        '<label for="r">Repeat new password</label><input id="r" name="confirm" '
        'type="password" autocomplete="new-password" required minlength="10">'
        f"{_message(error, ok)}<button type='submit'>Update password</button>"
        "</form><small><a style='color:#94a3b8' href='/'>Back to dashboard</a></small>"
    )
    return _page("Change password", body)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(safe_next(request.args.get("next")))
        return _login_page(next_url=safe_next(request.args.get("next")))
    next_url = safe_next(request.form.get("next"))
    if not csrf_valid(request.form.get("csrf_token")):
        return _login_page("Session expired. Please try again.", next_url, 400)
    user, error = authenticate(
        request.form.get("username"), request.form.get("password")
    )
    if error:
        status = 429 if error.startswith("Too many") else 401
        return _login_page(error, next_url, status)
    session.clear()
    session["uid"] = user.id
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    return redirect("/change-password" if user.must_change_password else next_url)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if current_user() and not csrf_valid(request.form.get("csrf_token")):
        return jsonify({"error": "Invalid CSRF token"}), 400
    if current_user():
        audit("logout", current_user().username)
    session.clear()
    return redirect("/login")


@auth_bp.route("/change-password", methods=["GET", "POST"])
def change_password_page():
    user = current_user()
    if user is None:
        return redirect("/login?next=/change-password")
    forced = user.must_change_password
    if request.method == "GET":
        return _password_page(forced=forced)
    if not csrf_valid(request.form.get("csrf_token")):
        return _password_page("Session expired. Please try again.", forced=forced)
    error = change_password(
        user,
        request.form.get("current"),
        request.form.get("new"),
        request.form.get("confirm"),
    )
    if error:
        return _password_page(error, forced=forced)
    return redirect("/")


@auth_bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------- gating


def _required_api_permission(path):
    for pattern, permission in API_RULES:
        if re.match(pattern, path):
            return permission
    return "view"


def _is_public(path):
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _wants_json(path):
    return path.startswith("/api/") or "/_dash-" in path


def load_user_and_gate():
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        try:
            user = db.session.get(User, uid)
        except Exception as exc:
            db.session.rollback()
            logger.error("Session user lookup failed: %s", exc)
            user = None
        if user is not None and user.is_active:
            g.user = user
        else:
            session.clear()
    path = request.path
    if _is_public(path):
        return None
    if g.user is None:
        if _wants_json(path):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(f"/login?next={safe_next(request.full_path.rstrip('?'))}")
    if g.user.must_change_password and path not in ("/change-password", "/logout"):
        if _wants_json(path):
            return jsonify({"error": "Password change required"}), 403
        return redirect("/change-password")
    if path.startswith("/api/"):
        permission = _required_api_permission(path)
        if not has_permission(g.user.role, permission):
            return jsonify({"error": "Forbidden"}), 403
        if request.method not in CSRF_EXEMPT_METHODS and not csrf_valid(
            request.headers.get("X-CSRF-Token")
        ):
            return jsonify({"error": "Invalid CSRF token"}), 403
    return None


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path.startswith("/api/") or request.path in (
        "/login",
        "/change-password",
    ):
        response.headers["Cache-Control"] = "no-store"
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


def init_auth(app, config_name=None):
    production = is_production(config_name)
    key = app.config.get("SECRET_KEY") or ""
    if key in INSECURE_KEYS or len(key) < 16:
        if production:
            raise RuntimeError(
                "SECRET_KEY must be set to a strong random value in production"
            )
        logger.warning("SECRET_KEY not set: using a random per-process key (dev only)")
        app.config["SECRET_KEY"] = secrets.token_urlsafe(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )
    if production:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.register_blueprint(auth_bp)
    app.before_request(load_user_and_gate)
    app.after_request(add_security_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
