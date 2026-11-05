"""ตัววัดการใช้ Earth Engine (EECU-วินาที) — ee_meter.py · ไม่เรียก Earth Engine จริง"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, fake_verify_line_token

import ee_meter

PROFILE = """ EECU·s PeakMem Count  Description
  0.069    318k   819  (plumbing)
  0.026    313k   240  Algorithm Image.select
  1.5k     1.2M     4  Algorithm reduce.median
  0.005    106k     6  Loading assets: COPERNICUS/S2_SR/20260602T034531_20260602T035704_T47PNR@1780400535307371
  0.000    3.2k     9  Algorithm Image.mask
"""


class TestParse:
    def test_sums_first_column_and_handles_units(self):
        assert ee_meter.parse_eecu_seconds(PROFILE) == pytest.approx(0.069 + 0.026 + 1500 + 0.005)

    def test_empty_or_garbage(self):
        assert ee_meter.parse_eecu_seconds("") == 0
        assert ee_meter.parse_eecu_seconds("ไม่มีตาราง\nabc 1 2 3") == 0


class TestMetered:
    def test_disabled_is_passthrough(self, monkeypatch):
        monkeypatch.delenv("EE_USAGE_METER", raising=False)

        @ee_meter.metered("x")
        def f(a, b=2):
            return a + b
        with patch.object(ee_meter, "record") as rec:
            assert f(1, b=3) == 4
        rec.assert_not_called()
        assert f.__name__ == "f"

    def test_enabled_records_once_and_nested_not_double_counted(self, monkeypatch):
        monkeypatch.setenv("EE_USAGE_METER", "1")
        fake_ee = MagicMock()
        fake_ee.data.profiling.return_value.__enter__.return_value = None

        @ee_meter.metered("inner")
        def inner():
            return 1

        @ee_meter.metered("outer")
        def outer():
            return inner() + 1
        with patch.dict(sys.modules, {"ee": fake_ee}), patch.object(ee_meter, "record") as rec:
            assert outer() == 2
        assert rec.call_count == 1 and rec.call_args.args[0] == "outer"

    def test_failure_is_recorded_not_swallowed(self, monkeypatch):
        monkeypatch.setenv("EE_USAGE_METER", "1")
        fake_ee = MagicMock()

        @ee_meter.metered("boom")
        def boom():
            raise ValueError("x")
        with patch.dict(sys.modules, {"ee": fake_ee}), patch.object(ee_meter, "record") as rec:
            with pytest.raises(ValueError):
                boom()
        assert rec.call_args.args[3] is False

    def test_record_skips_when_no_profile_ids_and_never_raises(self):
        assert ee_meter.record("x", [], 1.0, True) is None
        with patch.object(ee_meter, "_fetch_profile_text", side_effect=RuntimeError("down")):
            assert ee_meter.record("x", ["p1"], 1.0, True) is None


class TestSummarize:
    def test_per_plot_day_uses_daily_ops_plus_weekly_share(self):
        rows = [{"op": "analyze", "eecu_seconds": 2.0, "wall_seconds": 3, "created_at": "2026-11-06T01:00:00Z"},
                {"op": "analyze", "eecu_seconds": 4.0, "wall_seconds": 5, "created_at": "2026-11-06T01:01:00Z"},
                {"op": "wetness_anomaly", "eecu_seconds": 1.0, "created_at": "2026-11-06T01:02:00Z"},
                {"op": "moisture_grid", "eecu_seconds": 3.0, "created_at": "2026-11-07T01:00:00Z"},
                {"op": "risk_profile", "eecu_seconds": 7.0, "created_at": "2026-11-07T01:00:00Z"},
                {"op": "analyze", "eecu_seconds": 99, "ok": False, "created_at": "2026-11-07T01:00:00Z"}]
        s = ee_meter.summarize(rows)
        assert s["ops"]["analyze"]["n"] == 2 and s["ops"]["analyze"]["mean_eecu_s"] == 3.0
        assert s["per_plot_day_eecu_s"] == pytest.approx(3.0 + 1.0 + 3.0 + 7.0 / 7)
        assert s["by_day_eecu_s"] == {"2026-11-06": 7.0, "2026-11-07": 10.0}

    def test_empty(self):
        s = ee_meter.summarize([])
        assert s["ops"] == {} and s["per_plot_day_eecu_s"] is None


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestAdminEndpoint:
    async def test_needs_key_and_summarizes(self, client):
        assert (await client.get("/admin/ee-usage")).status_code == 403
        rows = [{"op": "analyze", "eecu_seconds": 2.5, "wall_seconds": 3, "created_at": "2026-11-06T01:00:00Z"}]
        with patch("main.list_ee_usage", new_callable=AsyncMock, return_value=rows):
            j = (await client.get("/admin/ee-usage?days=7", headers=ADMIN_HEADERS)).json()
        assert j["rows"] == 1 and j["ops"]["analyze"]["mean_eecu_s"] == 2.5 and "meter_enabled" in j
