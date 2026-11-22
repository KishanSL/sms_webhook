"""Basic frontend checks: the static scripts are served and referenced by the pages."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import init_db
from app.main import app

PAGES = {"dashboard": "/dashboard", "transactions": "/transactions", "settings": "/settings", "cards": "/cards"}


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "frontend.db"))
    await init_db(settings.DB_PATH)
    yield


@pytest.mark.anyio
async def test_static_scripts_are_served():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        common = await client.get("/static/js/common.js")
        assert common.status_code == 200
        assert "function escapeHtml" in common.text
        assert "async function api(" in common.text

        for script in ("dashboard", "transactions", "settings", "cards", "gnucash"):
            res = await client.get(f"/static/js/{script}.js")
            assert res.status_code == 200, script
            assert "function" in res.text


@pytest.mark.anyio
async def test_pages_load_common_and_their_script():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for page, path in PAGES.items():
            html = (await client.get(path)).text
            assert '/static/js/common.js?v=' in html, page
            assert f'/static/js/{page}.js?v=' in html, page
        # The gnucash pages also load the shared picker script.
        for path in ("/transactions", "/settings"):
            assert '/static/js/gnucash.js?v=' in (await client.get(path)).text


@pytest.mark.anyio
async def test_common_js_has_no_template_placeholders():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for script in (*PAGES, "gnucash"):
            js = (await client.get(f"/static/js/{script}.js")).text
            assert "{%" not in js and "{{" not in js, script


@pytest.mark.anyio
async def test_dashboard_has_type_filter():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        html = (await client.get("/dashboard")).text
        assert 'id="typeFilter"' in html
        assert 'Type: Transaction' in html
        assert 'Type: OTP' in html
        assert 'Type: Statement' in html
        assert 'Type: Toll / FASTag' in html
