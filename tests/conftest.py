import pytest

import auth
from app import create_app
from models import db


MASTER_TEST_PASSWORD = "master-test-password-1"


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("MASTER_PASSWORD", MASTER_TEST_PASSWORD)
    monkeypatch.delenv("MASTER_USERNAME", raising=False)
    auth.reset_ip_limits()
    flask_app = create_app("testing")
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def login_as(app, username):
    """Test client with a signed-in session for an existing user."""
    test_client = app.test_client()
    with app.app_context():
        user = auth.get_user_by_name(username)
        user_id = user.id
    with test_client.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = "test-csrf"
    return test_client


@pytest.fixture()
def anon_client(app):
    return app.test_client()


@pytest.fixture()
def client(app):
    """Signed-in master admin (most tests exercise features, not auth)."""
    return login_as(app, "Baseer")


@pytest.fixture(autouse=True)
def reset_provider_state():
    from services import snapshots
    from services.providers import base, chain

    from services.tick_store import tick_store

    tick_store.clear()
    base.breaker.reset()
    base.log_throttle.reset()
    chain._status.clear()
    snapshots._last_write.clear()
    yield
    base.breaker.reset()
