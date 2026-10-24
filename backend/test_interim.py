"""
test_interim.py — โหมด "ระดับความเสี่ยงกำลังปรับปรุง" (docs/v2/risk-display-interim.md)
"""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

HIGH_DATA = {
    "lat": 13.5, "lng": 99.8, "ndvi_now": 0.6, "ndvi_prev": 0.6, "ndvi_change": 0.0,
    "soil_moisture_vv": -7.0, "elevation": 5.0, "elevation_diff": -2.0, "bsi": 0.1,
    "overall_risk_level": "high",
    "swab": {"status": "waterlogged", "status_th": "น้ำขังวิกฤต ⚠️ — รากเล็กๆ ขาดอากาศหายใจ",
             "severity": "high", "swab_index": 0.6, "swab_level": 4, "soil_water_pct": 48,
             "soil_air_pct": 0, "soil_solid_pct": 52, "ndwi": 0.0},
}
POINTS = [{"grid_label": "A1", "position_desc": "ฝั่งเหนือของแปลง", "status": "waterlogged",
           "label_th": "น้ำขังหนัก", "swab_index": 0.6, "lat": 1, "lng": 1, "color_hint": None}]


def _flex_text(flex) -> str:
    return json.dumps(flex, ensure_ascii=False)


class TestModeSwitch:
    def test_defaults(self, monkeypatch):
        import risk_display
        monkeypatch.delenv("RISK_DISPLAY_MODE", raising=False)
        monkeypatch.delenv("RISK_ALERTS_ENABLED", raising=False)
        assert risk_display.interim() is True
        assert risk_display.risk_alerts_enabled() is False

    def test_legacy_restores_alerts(self, monkeypatch):
        import risk_display
        monkeypatch.setenv("RISK_DISPLAY_MODE", "legacy")
        monkeypatch.delenv("RISK_ALERTS_ENABLED", raising=False)
        assert risk_display.interim() is False and risk_display.risk_alerts_enabled() is True

    def test_relative_labels(self):
        import risk_display
        assert risk_display.relative_label("waterlogged") == "ชื้นกว่าส่วนอื่นของแปลง"
        assert risk_display.relative_label("drought") == "แห้งกว่าส่วนอื่นของแปลง"


class TestFlexInterim:
    def test_card_hides_old_labels(self, monkeypatch):
        monkeypatch.delenv("RISK_DISPLAY_MODE", raising=False)
        from flex_messages import build_result_flex
        text = _flex_text(build_result_flex(HIGH_DATA, 13.5, 99.8, "m", problem_points=POINTS, plot_name="A"))
        assert "กำลังปรับปรุง" in text
        assert "เสี่ยงสูง" not in text
        assert "น้ำขังวิกฤต" not in text and "น้ำขังหนัก" not in text
        assert "ชื้นกว่าส่วนอื่นของแปลง" in text and "A1" in text

    def test_legacy_card_unchanged(self, monkeypatch):
        monkeypatch.setenv("RISK_DISPLAY_MODE", "legacy")
        from flex_messages import build_result_flex
        text = _flex_text(build_result_flex(HIGH_DATA, 13.5, 99.8, "m", plot_name="A"))
        assert "เสี่ยงสูง" in text and "กำลังปรับปรุง" not in text

    def test_digest_text(self, monkeypatch):
        monkeypatch.delenv("RISK_DISPLAY_MODE", raising=False)
        from flex_messages import build_daily_digest_message
        text = build_daily_digest_message([{"name": "แปลง A", "data": HIGH_DATA}])
        assert "กำลังปรับปรุง" in text and "น้ำขังวิกฤต" not in text and "แปลง A" in text


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestConfigAndScan:
    async def test_config_js_exposes_mode(self, client, monkeypatch):
        monkeypatch.delenv("RISK_DISPLAY_MODE", raising=False)
        assert 'RISK_DISPLAY: "interim"' in (await client.get("/liff/config.js")).text
        monkeypatch.setenv("RISK_DISPLAY_MODE", "legacy")
        assert 'RISK_DISPLAY: "legacy"' in (await client.get("/liff/config.js")).text

    async def test_risk_alerts_paused_by_default(self, monkeypatch):
        monkeypatch.delenv("RISK_DISPLAY_MODE", raising=False)
        monkeypatch.delenv("RISK_ALERTS_ENABLED", raising=False)
        from test_messaging import _scan_patches
        from contextlib import ExitStack
        from scheduler import daily_scan_job
        patches = [p for p in _scan_patches(prev_level="medium") if p.attribute != "risk_alerts_enabled"]
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            send = st.enter_context(patch("scheduler.send_line_message", new_callable=AsyncMock))
            await daily_scan_job(hour=None)
        send.assert_not_called()
