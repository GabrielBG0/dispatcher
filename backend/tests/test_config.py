import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine)

    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


PAYLOAD = {
    "start_date": "2026-07-27",
    "total_weeks": 19,
    "new_card_weeks": 16,
    "review_weeks": 3,
    "daily_minimum": 18,
}


def test_get_config_returns_none_when_unset(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() is None


def test_put_then_get_config_roundtrips(client):
    put_resp = client.put("/api/config", json=PAYLOAD)
    assert put_resp.status_code == 200
    assert put_resp.json() == PAYLOAD

    get_resp = client.get("/api/config")
    assert get_resp.json() == PAYLOAD


def test_put_config_is_upsert_not_duplicate(client):
    client.put("/api/config", json=PAYLOAD)
    updated = {**PAYLOAD, "daily_minimum": 25}
    client.put("/api/config", json=updated)

    resp = client.get("/api/config")
    assert resp.json()["daily_minimum"] == 25
