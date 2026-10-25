"""
test_rain_alerts_3a.py — Phase 3a/3b: เตือนฝนหนักแบบบอกจุดเสี่ยง + ตั้งความถี่
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import rain_alerts as ra
import recommendations

SQUARE = [[99.800, 13.500], [99.801, 13.500], [99.801, 13.501], [99.800, 13.501], [99.800, 13.500]]


def _snapshot():
    pts = []
    for i in range(10):
        for j in range(10):
            pts.append({"lat": 13.5 + i * 0.0001 + 0.00005, "lng": 99.8 + j * 0.0001 + 0.00005,
                        "swab_index": 0.3 if i >= 8 else 0.0, "status": "optimal"})
    return pts


class TestTrigger:
    def test_heavy_tomorrow(self):
        s = ra.heavy_rain_soon({"daily_mm": [3, 42.5, 10, 0, 0, 0, 0]})
        assert s == {"day_offset": 1, "mm": 42.5, "peak_mm": 42.5, "level": "warning"}

    def test_very_heavy_is_critical(self):
        assert ra.heavy_rain_soon({"daily_mm": [36, 95, 0]})["level"] == "critical"

    def test_only_within_3_days(self):
        assert ra.heavy_rain_soon({"daily_mm": [5, 10, 20, 80, 0, 0, 0]}) is None

    def test_old_rule_week_total_no_longer_triggers(self):
        """ฝน 7 วันรวม 70 มม. แต่ไม่มีวันไหนถึง 35 → ไม่เตือน (กฎเดิมจะเตือน)"""
        assert ra.heavy_rain_soon({"daily_mm": [10, 10, 10, 10, 10, 10, 10], "total_rain_mm": 70}) is None

    def test_source_without_daily(self):
        s = ra.heavy_rain_soon({"daily_mm": None, "max_daily_mm": 50})
        assert s["day_offset"] is None and "ภายใน 7 วัน" in ra.headline("A", s, {"raised_bed": False, "wet": 0})


class TestMessage:
    def test_risk_points_and_headline(self):
        risk = ra.risk_points(_snapshot(), SQUARE, "flat")
        assert risk["wet"] == 20 and "ฝั่งเหนือ" in risk["where"]
        h = ra.headline("แปลง A", {"day_offset": 1, "mm": 42, "level": "warning"}, risk)
        assert h == "พรุ่งนี้ฝนหนัก (~42 มม.) — แปลง A: มีจุดที่ชื้นกว่าส่วนอื่น 20 จุด เสี่ยงน้ำขังก่อน"

    def test_raised_bed_ignores_wet_points(self):
        risk = ra.risk_points(_snapshot(), SQUARE, "raised_bed")
        assert risk["wet"] == 0 and risk["raised_bed"]
        assert "จุด" not in ra.headline("ส้มโอ", {"day_offset": 0, "mm": 40, "level": "warning"}, risk)

    def test_crop_note_only_for_known_sensitive(self):
        assert "ทุเรียน" in ra.crop_note(["durian", "mango"])
        assert ra.crop_note(["mango"]) is None

    def test_flex_has_recommendation_status_and_attribution(self):
        rules = list(recommendations.seed_rules())
        rec = recommendations.recommend(rules, ["durian"], "flat", "heavy_rain_coming", "medium")
        assert rec["severity"] == "high"                      # ทุเรียนอ่อนไหวน้ำขัง → ปรับขึ้น
        risk = ra.risk_points(_snapshot(), SQUARE, "flat")
        flex = ra.build_flex({"name": "A"}, {"day_offset": 1, "mm": 40, "level": "warning"}, risk, rec,
                             ra.crop_note(["durian"]), "https://liff.line.me/x")
        dumped = json.dumps(flex, ensure_ascii=False)
        assert "ร่าง — รอเจ้าหน้าที่เกษตรตรวจ" in dumped and "Open-Meteo" in dumped
        assert "https://liff.line.me/x" in dumped and "13.5" not in dumped     # ไม่ใส่พิกัดแปลง
        assert len(dumped.encode()) < 10_000

    def test_heavy_rain_rules_seeded_without_numbers(self):
        rules = [r for r in recommendations.seed_rules() if r["problem"] == "heavy_rain_coming"]
        assert len(rules) == 3 and all(r["status"] == "draft" for r in rules)

    def test_weekly_cap(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert ra.weekly_cap_reached((now - timedelta(days=3)).isoformat(), now)
        assert not ra.weekly_cap_reached((now - timedelta(days=8)).isoformat(), now)
        assert not ra.weekly_cap_reached(None, now)


PLOT = {"id": 7, "name": "แปลง A", "lat": 13.5, "lng": 99.8, "polygon": SQUARE, "plot_type": "flat",
        "crop_ids": ["durian"]}


def _job_patches(forecast, last_user_msg=None, weekly=frozenset(), rain_new=True):
    return [
        patch("scheduler.get_all_user_ids", new_callable=AsyncMock, return_value=["U1"]),
        patch("scheduler.get_notifiable_users", new_callable=AsyncMock, return_value={"U1"}),
        patch("scheduler.get_rain_freq_users", new_callable=AsyncMock, return_value=set(weekly)),
        patch("scheduler.get_last_user_message", new_callable=AsyncMock, return_value=last_user_msg),
        patch("scheduler.get_user_plots", new_callable=AsyncMock, return_value=[PLOT]),
        patch("scheduler.get_7day_rain", new_callable=AsyncMock, return_value=forecast),
        patch("scheduler._rain_alert_is_new", new_callable=AsyncMock, return_value=rain_new),
        patch("scheduler.get_latest_grid_snapshot", new_callable=AsyncMock, return_value=_snapshot()),
        patch("scheduler.build_plot_grid_image_url", new_callable=AsyncMock, return_value={"url": None}),
        patch("database.list_recommendation_rules", new_callable=AsyncMock, return_value=None),
        patch("scheduler.send_line_message", new_callable=AsyncMock),
    ]


async def _run(patches):
    import scheduler
    recommendations.invalidate()
    for p in patches:
        p.start()
    try:
        await scheduler.rain_alert_job()
        return scheduler.send_line_message
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
class TestJob:
    async def test_sends_one_push_with_risk(self):
        import scheduler
        patches = _job_patches({"daily_mm": [0, 45, 0, 0, 0, 0, 0], "max_daily_mm": 45})
        recommendations.invalidate()
        for p in patches:
            p.start()
        try:
            await scheduler.rain_alert_job()
            send = scheduler.send_line_message
            assert send.await_count == 1
            kwargs = send.await_args.kwargs
            assert kwargs["kind"] == "rain_alert" and kwargs["log_plots"] == [(7, "warning")]
            assert "จุดที่ชื้นกว่าส่วนอื่น 20 จุด" in send.await_args.args[1]
        finally:
            for p in patches:
                p.stop()

    async def test_no_heavy_rain_no_push(self):
        import scheduler
        patches = _job_patches({"daily_mm": [10, 10, 10, 10, 10, 10, 10], "max_daily_mm": 10, "total_rain_mm": 70})
        for p in patches:
            p.start()
        try:
            await scheduler.rain_alert_job()
            assert scheduler.send_line_message.await_count == 0
        finally:
            for p in patches:
                p.stop()

    async def test_weekly_cap_skips(self):
        import scheduler
        recent = {"created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}
        patches = _job_patches({"daily_mm": [50, 0, 0], "max_daily_mm": 50}, last_user_msg=recent, weekly={"U1"})
        for p in patches:
            p.start()
        try:
            await scheduler.rain_alert_job()
            assert scheduler.send_line_message.await_count == 0
        finally:
            for p in patches:
                p.stop()

    async def test_dedupe_respected(self):
        import scheduler
        patches = _job_patches({"daily_mm": [50, 0, 0], "max_daily_mm": 50}, rain_new=False)
        for p in patches:
            p.start()
        try:
            await scheduler.rain_alert_job()
            assert scheduler.send_line_message.await_count == 0
        finally:
            for p in patches:
                p.stop()


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
class TestSettingsApi:
    async def test_get_settings(self, client):
        with patch("main.get_user", new_callable=AsyncMock,
                   return_value={"notify_weekly": True, "notify_daily_digest": False, "rain_alert_freq": "weekly_max"}):
            body = (await client.get("/user/U1/notify-settings", headers=auth_as("U1"))).json()
        assert body["rain_alerts"] and body["rain_alert_freq"] == "weekly_max" and not body["weekly_digest"]

    async def test_set_freq(self, client):
        with patch("main.set_rain_alert_freq", new_callable=AsyncMock, return_value=True) as sf:
            ok = await client.patch("/user/U1/rain-alert-freq?mode=weekly_max", headers=auth_as("U1"))
            bad = await client.patch("/user/U1/rain-alert-freq?mode=hourly", headers=auth_as("U1"))
            other = await client.patch("/user/U2/rain-alert-freq?mode=normal", headers=auth_as("U1"))
        assert ok.status_code == 200 and bad.status_code == 422 and other.status_code == 403
        sf.assert_awaited_once_with("U1", "weekly_max")
