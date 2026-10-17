"""
test_risk_v2.py — วิธีวัดความเสี่ยงใหม่ (รันคู่): คำนวณสถานะ, ลักษณะแปลง, สรุปผลรันคู่
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

TODAY = datetime(2026, 10, 15, tzinfo=timezone.utc)


def _series(current_vv, base_by_year, orbit=62, current_days=(2, 8)):
    """ภาพปีก่อน ๆ ช่วงเดียวกันของปี (±20 วัน) + ภาพปัจจุบัน"""
    s = []
    for year, values in base_by_year.items():
        for i, v in enumerate(values):
            s.append((TODAY.replace(year=year) - timedelta(days=20) + timedelta(days=6 * i), v, orbit))
    for d in current_days:
        s.append((TODAY - timedelta(days=d), current_vv, orbit))
    return s


class TestWetnessAnomaly:
    def test_normal(self):
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(
            _series(-9.0, {2023: [-9.2, -8.8, -9.0], 2024: [-9.1, -8.9, -9.0], 2025: [-9.0, -9.2, -8.8]}), TODAY)
        assert r["status"] == "ok" and abs(r["z"]) < 1

    def test_much_wetter_than_usual(self):
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(
            _series(-6.0, {2023: [-9.2, -8.8, -9.0], 2024: [-9.1, -8.9, -9.0], 2025: [-9.0, -9.2, -8.8]}), TODAY)
        assert r["status"] == "high" and r["z"] >= 2

    def test_chronically_wet_plot_is_normal_for_itself(self):
        """จุดบอดที่ต้องมีตัวชี้วัดแยก: แปลงชื้นทุกปีจะถือว่าปกติ"""
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(
            _series(-4.0, {2023: [-4.1, -3.9, -4.0], 2024: [-4.0, -4.2, -3.8]}), TODAY)
        assert r["status"] == "ok"

    def test_years_weighted_equally(self):
        """ปี 2025 มีภาพมากกว่า (Sentinel-1C) ต้องไม่ดึงค่าปกติไปทางปีนั้น"""
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(
            _series(-8.0, {2023: [-10.0, -10.0], 2024: [-10.0, -10.0], 2025: [-6.0] * 8}), TODAY)
        assert r["baseline_mean_db"] == pytest.approx((-10 - 10 - 6) / 3, abs=0.01)

    def test_insufficient_baseline(self):
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(_series(-6.0, {2025: [-9.0, -9.1, -8.9]}), TODAY)
        assert r["status"] == "insufficient"

    def test_no_recent_image(self):
        import risk_v2
        r = risk_v2.compute_wetness_anomaly(
            _series(-6.0, {2023: [-9.0, -9.0], 2024: [-9.0, -9.1]}, current_days=(20,)), TODAY)
        assert r["status"] == "no_recent"

    def test_single_orbit_used(self):
        """ภาพจากวงโคจรอื่น (มุมมองต่าง) ต้องไม่ถูกนำมาปน"""
        import risk_v2
        base = _series(-9.0, {2023: [-9.0, -9.1, -8.9], 2024: [-9.0, -9.1, -8.9], 2025: [-9.0, -9.1, -8.9]})
        other_orbit = [(TODAY - timedelta(days=1), -2.0, 135)]
        r = risk_v2.compute_wetness_anomaly(base + other_orbit, TODAY)
        assert r["orbit"] == 62 and r["status"] == "ok"

    def test_gee_not_ready(self):
        import gee_analysis, risk_v2
        with patch.object(gee_analysis, "_gee_ready", False):
            assert risk_v2.get_wetness_anomaly(13.5, 99.8)["status"] == "unavailable"


class TestProfile:
    def test_flags(self):
        import risk_v2
        assert risk_v2.classify_profile(1.5, 0.9, 0.95)["flags"] == ["low_lying_very", "wetter_than_neighbors"]
        assert risk_v2.classify_profile(4.0, 0.9, 0.5)["flags"] == ["low_lying"]
        assert risk_v2.classify_profile(20.0, 0.05, 0.99)["flags"] == ["mixed_land_cover"]
        assert risk_v2.classify_profile(None, None, None)["flags"] == []


class TestShadowSummary:
    def test_counts_and_alert_events(self):
        import risk_v2
        rows = []
        for i, (old, new) in enumerate([("high", "ok"), ("high", "medium"), ("high", "high"), ("high", "high"),
                                        ("medium", "ok")]):
            rows.append({"plot_id": 1, "created_at": f"2026-10-0{i + 1}", "old": old,
                         "risk_v2": {"status": new, "z": float(i)}, "profile": {"flags": ["low_lying"]}})
        rows.append({"plot_id": 2, "created_at": "2026-10-01", "old": "high", "risk_v2": None, "profile": None})
        s = risk_v2.summarize_shadow(rows)
        assert s["old_levels"] == {"high": 5, "medium": 1}
        assert s["new_status"]["missing"] == 1
        p1 = next(p for p in s["plots"] if p["plot_id"] == 1)
        assert p1["new_alert_events"] == 1 and p1["old_alert_events"] == 0
        assert p1["latest_z"] == 4.0 and p1["profile_flags"] == ["low_lying"]
        md = risk_v2.shadow_markdown(s, 7)
        assert "วิธีใหม่" in md and "| 1 |" in md


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestShadowEndpointAndScan:
    async def test_endpoint_markdown(self, client):
        rows = [{"plot_id": 7, "created_at": "2026-10-10", "old": "high", "risk_v2": {"status": "ok"}}]
        with patch("main.get_shadow_rows", new_callable=AsyncMock, return_value=rows):
            r = await client.get("/admin/risk-shadow?format=markdown", headers=ADMIN_HEADERS)
        assert r.status_code == 200 and "สรุปผลรันคู่" in r.text
        assert (await client.get("/admin/risk-shadow")).status_code == 403

    async def test_scan_stores_shadow_result_without_touching_cache(self):
        from contextlib import ExitStack
        from scheduler import daily_scan_job
        plot = {"id": 7, "name": "A", "lat": 13.5, "lng": 99.8, "polygon": None}
        cached_dict = {"lat": 13.5, "lng": 99.8, "overall_risk_level": "ok"}
        with ExitStack() as st:
            for name, kw in [("delete_old_grid_snapshots", {}), ("delete_old_plot_images", {}),
                             ("get_all_user_ids", {"return_value": ["U1"]}),
                             ("get_notifiable_users", {"return_value": set()}),
                             ("get_digest_users", {"return_value": set()}),
                             ("get_user_plots", {"return_value": [plot]}),
                             ("get_plot_analysis_since", {"return_value": None})]:
                st.enter_context(patch(f"scheduler.{name}", new_callable=AsyncMock, **kw))
            st.enter_context(patch("scheduler.analyze_durian_plot", return_value=cached_dict))
            st.enter_context(patch("scheduler.format_message", return_value="m"))
            st.enter_context(patch("scheduler._is_digest_day", return_value=False))
            st.enter_context(patch("scheduler.risk_v2.get_wetness_anomaly",
                                   return_value={"method": "s1_anomaly_v1", "status": "medium", "z": 1.3}))
            save = st.enter_context(patch("scheduler.save_analysis", new_callable=AsyncMock))
            st.enter_context(patch("scheduler.send_line_message", new_callable=AsyncMock))
            await daily_scan_job(hour=None)
        saved = save.call_args.args[1]
        assert saved["risk_v2"]["status"] == "medium"
        assert "risk_v2" not in cached_dict          # dict ใน cache ไม่ถูกแก้
