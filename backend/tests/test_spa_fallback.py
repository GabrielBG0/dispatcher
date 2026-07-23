import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

pytestmark = pytest.mark.skipif(
    not settings.frontend_dist_dir.exists(), reason="frontend/dist not built"
)

client = TestClient(app)


def test_deep_link_route_serves_index_html_not_404():
    resp = client.get("/batches")
    assert resp.status_code == 200
    assert "<div id=\"root\">" in resp.text


def test_root_serves_index_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<div id=\"root\">" in resp.text


def test_api_routes_are_not_shadowed_by_the_spa_fallback():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_real_static_asset_is_served_directly_not_index_html():
    # Whatever hashed JS bundle vite produced should be served as itself.
    asset_files = list((settings.frontend_dist_dir / "assets").glob("*.js"))
    assert asset_files, "expected at least one built JS asset"
    resp = client.get(f"/assets/{asset_files[0].name}")
    assert resp.status_code == 200
    assert "<div id=\"root\">" not in resp.text
