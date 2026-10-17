"""
test_speed.py — Phase 1b ความเร็ว: telemetry จาก LIFF และการโหลดสคริปต์ของหน้า LIFF
"""

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

LIFF_HTML = (Path(__file__).resolve().parent.parent / "liff" / "index.html").read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def client():
    import client_telemetry
    from middleware import reset_limits
    reset_limits()
    client_telemetry.reset_for_tests()
    with patch("scheduler.create_scheduler", return_value=MagicMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestTelemetry:
    async def test_timing_recorded_and_summarised(self, client):
        ua = {"User-Agent": "Mozilla/5.0 (iPhone) Line/14.0"}
        for ready in (1200, 1800, 3000):
            r = await client.post("/client/telemetry", headers=ua, json={
                "kind": "timing", "app_version": "1b",
                "marks": {"liff_init": 400, "plots": 900, "map": ready - 100, "ready": ready}})
            assert r.status_code == 204
        s = (await client.get("/admin/client-telemetry", headers=ADMIN_HEADERS)).json()
        assert s["timing_count"] == 3
        assert s["marks_ms"]["ready"]["p50"] == 1800
        assert s["by_version_platform"]["1b/ios"]["count"] == 3

    async def test_error_recorded(self, client):
        r = await client.post("/client/telemetry", json={"kind": "error", "message": "boom",
                                                         "source": "index.html", "line": 12})
        assert r.status_code == 204
        s = (await client.get("/admin/client-telemetry", headers=ADMIN_HEADERS)).json()
        assert s["errors"][-1]["message"] == "boom"

    async def test_rejects_bad_payload(self, client):
        r = await client.post("/client/telemetry", json={"kind": "timing", "marks": {"x": -5}})
        assert r.status_code == 422
        r = await client.post("/client/telemetry", json={"kind": "other"})
        assert r.status_code == 422

    async def test_summary_is_admin_only(self, client):
        assert (await client.get("/admin/client-telemetry")).status_code == 403

    async def test_per_ip_cap(self, client):
        import client_telemetry
        headers = {"CF-Connecting-IP": "203.0.113.50"}
        for _ in range(40):
            await client.post("/client/telemetry", headers=headers, json={"kind": "error", "message": "x"})
        assert len(client_telemetry._events) == 30


class TestLiffLoading:
    def test_no_blocking_leaflet_or_chart_in_head(self):
        head = LIFF_HTML.split("</head>", 1)[0]
        assert "leaflet.js" not in head and "leaflet.css" not in head
        chart = re.search(r'<script[^>]*chart\.umd\.min\.js[^>]*>', head).group(0)
        assert "defer" in chart

    def test_map_sdk_starts_before_init_finishes(self):
        assert "const mapSdkReady = loadMapSdk();" in LIFF_HTML

    def test_no_fixed_500ms_wait_without_ready(self):
        assert "await new Promise(r => setTimeout(r, 500));\n" not in LIFF_HTML
        assert "Promise.race([mapReady" in LIFF_HTML
