import pytest

from app import create_app
from models import db


@pytest.fixture()
def app():
    flask_app = create_app("testing")
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_provider_state():
    from services import snapshots
    from services.providers import base, chain

    base.breaker.reset()
    chain._status.clear()
    snapshots._last_write.clear()
    yield
    base.breaker.reset()
