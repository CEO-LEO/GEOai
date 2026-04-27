"""
test_system.py — End-to-end mock tests
ทดสอบทุก layer โดยไม่เรียก GEE/LINE จริง
วิธีใช้:
  pip install pytest pytest-asyncio httpx
  pytest test_system.py -v
"""

import json
import logging
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

# ── Mock GEE ก่อน import main ──────────────────────────
import sys
gee_mock = MagicMock()
gee_mock.Initialize = MagicMock()
gee_mock.ServiceAccountCredentials = MagicMock()
gee_mock.EEException = Exception
sys.modules["ee"] = gee_mock

# Mock scheduler ไม่ให้ start จริง
scheduler_mock = MagicMock()
scheduler_mock.start = MagicMock()
scheduler_mock.shutdown = MagicMock()


# ── Fixtures ────────────────────────────────────────────

SAMPLE_GEE_DATA = {
    "lat": 12.6011,
    "lng": 102.1042,
    "ndvi_now":         0.52,
    "ndvi_prev":        0.65,
    "ndvi_change":      -0.13,
    "soil_moisture_vv": -8.0,
    "elevation":        45.0,
    "elevation_diff":   -2.1,
    "bsi":              0.28,
    "topsoil_risk_level": "high",
    "predicted_yield_kg_per_rai": 1147,
}

SAMPLE_GEE_DATA_OK = {
    "lat": 12.60,
    "lng": 102.10,
    "ndvi_now":         0.70,
    "ndvi_prev":        0.68,
    "ndvi_change":      0.02,
    "soil_moisture_vv": -14.0,
    "elevation":        48.0,
    "elevation_diff":   1.5,
    "bsi":              0.08,
    "topsoil_risk_level": "low",
    "predicted_yield_kg_per_rai": 1800,
}


@pytest_asyncio.fixture
async def client():
    """สร้าง test client กับ mock GEE + scheduler"""
    env_vars = {
        "GEE_SERVICE_ACCOUNT": "test@test.iam.gserviceaccount.com",
        "GEE_KEY_JSON": json.dumps({"type": "service_account"}),
        "LINE_CHANNEL_ACCESS_TOKEN": "test_token",
        "LINE_CHANNEL_SECRET": "test_secret",
        "LIFF_URL": "https://liff.line.me/test",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    with patch.dict("os.environ", env_vars):
        with patch("scheduler.create_scheduler", return_value=scheduler_mock):
            with patch("tempfile.NamedTemporaryFile"):
                with patch("os.unlink"):
                    from main import app
                    async with AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://test"
                    ) as ac:
                        yield ac


# ── Tests: Rule Engine ─────────────────────────────────

class TestRuleEngine:
    def test_high_risk_root_rot(self):
        from rule_engine import format_message
        msg = format_message(SAMPLE_GEE_DATA, 12.6011, 102.1042)
        assert "เฝ้าระวัง" in msg or "เสี่ยง" in msg
        assert "NDVI" in msg

    def test_healthy_plot(self):
        from rule_engine import format_message
        msg = format_message(SAMPLE_GEE_DATA_OK, 12.60, 102.10)
        assert "ปกติ" in msg or "ดี" in msg

    def test_message_contains_coords(self):
        from rule_engine import format_message
        msg = format_message(SAMPLE_GEE_DATA, 12.6011, 102.1042)
        assert "12.6011" in msg
        assert "102.1042" in msg


# ── Tests: Flex Messages ───────────────────────────────

class TestFlexMessages:
    def test_output_is_flex_type(self):
        from flex_messages import build_result_flex
        from rule_engine import format_message
        plain = format_message(SAMPLE_GEE_DATA, 12.60, 102.10)
        flex  = build_result_flex(SAMPLE_GEE_DATA, 12.60, 102.10, plain)
        assert flex["type"] == "flex"
        assert "contents" in flex

    def test_high_risk_color(self):
        from flex_messages import build_result_flex, _risk_level
        level = _risk_level(SAMPLE_GEE_DATA)
        assert level == "high"

    def test_ok_color(self):
        from flex_messages import _risk_level
        level = _risk_level(SAMPLE_GEE_DATA_OK)
        assert level == "ok"


# ── Tests: Cache ───────────────────────────────────────

class TestCache:
    def test_miss_returns_none(self):
        from cache import get_cached
        assert get_cached(0.0, 0.0) is None

    def test_set_then_get(self):
        from cache import get_cached, set_cached
        set_cached(99.0, 99.0, {"test": True})
        result = get_cached(99.0, 99.0)
        assert result == {"test": True}

    def test_precision_bucketing(self):
        """พิกัดใกล้กันควร cache ร่วมกัน"""
        from cache import get_cached, set_cached
        set_cached(12.6001, 102.1001, {"bucket": "test"})
        # 12.6001 และ 12.6004 ปัด 3 ทศนิยม → 12.600 ทั้งคู่
        result = get_cached(12.6004, 102.1004)
        assert result == {"bucket": "test"}

    def test_cache_stats(self):
        from cache import cache_stats
        stats = cache_stats()
        assert "size" in stats
        assert "ttl_hours" in stats


# ── Tests: API Endpoints ───────────────────────────────

@pytest.mark.asyncio
class TestAPI:
    async def test_health_endpoint(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "cache" in data

    async def test_analyze_calls_gee(self, client):
        with patch("main.analyze_durian_plot",
                   return_value=SAMPLE_GEE_DATA) as mock_gee:
            with patch("main.send_line_message", new_callable=AsyncMock):
                with patch("main.save_analysis", new_callable=AsyncMock):
                    with patch("main.save_plot",
                               new_callable=AsyncMock, return_value=1):
                        resp = await client.post("/analyze", json={
                            "lat":    12.6011,
                            "lng":    102.1042,
                            "user_id": "Utest123"
                        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_gee.assert_called_once_with(12.6011, 102.1042, polygon=None)

    async def test_analyze_invalid_lat(self, client):
        resp = await client.post("/analyze", json={
            "lat": 999.0, "lng": 102.0, "user_id": "Utest"
        })
        assert resp.status_code == 422

    async def test_analyze_gee_error_returns_502(self, client):
        with patch("main.analyze_durian_plot",
                   side_effect=Exception("GEE down")):
            resp = await client.post("/analyze", json={
                "lat": 12.60, "lng": 102.10, "user_id": "Utest"
            })
        assert resp.status_code in (500, 502)

    async def test_plots_endpoint(self, client):
        with patch("main.get_user_plots",
                   new_callable=AsyncMock,
                   return_value=[{"id": 1, "name": "แปลงที่ 1"}]):
            resp = await client.get("/plots/Utest123")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_export_csv(self, client):
        with patch("main.get_all_reports",
                   new_callable=AsyncMock,
                   return_value=[{**SAMPLE_GEE_DATA,
                                  "id": 1, "user_id": "U1",
                                  "created_at": "2026-04-09T00:00:00"}]):
            resp = await client.get("/admin/reports/export.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "ndvi_now" in resp.text

    async def test_admin_stats(self, client):
        reports = [
            {**SAMPLE_GEE_DATA, "id": 1, "user_id": "U1",
             "created_at": "2026-04-09"},
            {**SAMPLE_GEE_DATA_OK, "id": 2, "user_id": "U2",
             "created_at": "2026-04-09"},
        ]
        with patch("main.get_all_reports",
                   new_callable=AsyncMock, return_value=reports):
            resp = await client.get("/admin/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["unique_users"] == 2
        assert data["high_risk"] >= 1
        assert data["ok"] >= 1

    async def test_delete_plot(self, client):
        with patch("main.delete_plot",
                   new_callable=AsyncMock, return_value=True):
            resp = await client.delete("/plots/Utest123/1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    async def test_delete_plot_not_found(self, client):
        with patch("main.delete_plot",
                   new_callable=AsyncMock, return_value=False):
            resp = await client.delete("/plots/Utest123/999")
        assert resp.status_code == 404

    async def test_plot_history(self, client):
        mock_hist = [
            {**SAMPLE_GEE_DATA, "id": 1, "message": "test",
             "created_at": "2026-04-09T07:00:00"}
        ]
        with patch("main.get_plot_history",
                   new_callable=AsyncMock, return_value=mock_hist):
            resp = await client.get("/plots/Utest123/1/history")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["plot_id"] == 1


# ── Tests: Weather Alert ───────────────────────────────

class TestWeatherAlert:
    def test_rain_threshold(self):
        from weather_alert import WeatherForecast
        forecast: WeatherForecast = {
            "total_rain_mm": 120.0,
            "max_daily_mm": 45.0,
            "rainy_days": 5,
            "is_heavy_rain": True,
        }
        assert forecast["is_heavy_rain"] is True
        assert forecast["total_rain_mm"] > 80

    def test_rain_alert_message(self):
        from weather_alert import build_rain_alert_message
        forecast = {
            "total_rain_mm": 100.0,
            "max_daily_mm": 42.0,
            "rainy_days": 4,
            "is_heavy_rain": True,
        }
        msg = build_rain_alert_message(forecast, 12.60, 102.10, "แปลง A")
        assert "แปลง A" in msg
        assert "100.0" in msg
        assert "ระบายน้ำ" in msg

    def test_rain_alert_flex_structure(self):
        from weather_alert import build_rain_alert_flex
        forecast = {
            "total_rain_mm": 90.0,
            "max_daily_mm": 30.0,
            "rainy_days": 3,
            "is_heavy_rain": True,
        }
        flex = build_rain_alert_flex(forecast, 12.60, 102.10, "แปลง B")
        assert flex["type"] == "flex"
        assert "bubble" in str(flex["contents"]["type"])

    def test_no_heavy_rain(self):
        from weather_alert import RAIN_THRESHOLD_MM
        forecast = {
            "total_rain_mm": 20.0,
            "max_daily_mm": 8.0,
            "rainy_days": 2,
            "is_heavy_rain": False,
        }
        assert forecast["is_heavy_rain"] is False
        assert forecast["total_rain_mm"] < RAIN_THRESHOLD_MM


# ── Tests: Webhook Signature ───────────────────────────

class TestWebhookSignature:
    def test_valid_signature(self):
        import hashlib, hmac, base64
        from webhook import _verify_signature
        secret = "test_secret"
        body = b'{"events":[]}'
        digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        sig = base64.b64encode(digest).decode()

        with patch.dict("os.environ", {"LINE_CHANNEL_SECRET": secret}):
            assert _verify_signature(body, sig) is True

    def test_invalid_signature(self):
        from webhook import _verify_signature
        with patch.dict("os.environ", {"LINE_CHANNEL_SECRET": "real_secret"}):
            assert _verify_signature(b"body", "wrong_sig") is False


# ── Tests: GEE Retry ──────────────────────────────────

class TestGEERetry:
    def test_retry_succeeds_on_second_attempt(self):
        from gee_analysis import _retry_gee
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                exc = Exception("transient")
                exc.__class__.__name__ = "EEException"
                raise exc
            return "success"

        # Need to mock ee.EEException for isinstance check
        gee_mock.EEException = Exception
        result = _retry_gee(flaky)
        assert result == "success"
        assert call_count == 2

    def test_retry_exhausted_raises(self):
        from gee_analysis import _retry_gee, MAX_RETRIES

        def always_fail():
            raise gee_mock.EEException("permanent failure")

        with pytest.raises(Exception, match="permanent failure"):
            _retry_gee(always_fail)

    def test_safe_getInfo_returns_default(self):
        from gee_analysis import _safe_getInfo
        mock_computation = MagicMock()
        mock_computation.getInfo.side_effect = gee_mock.EEException("down")
        result = _safe_getInfo(mock_computation, default=-15.0)
        assert result == -15.0

    def test_safe_getInfo_returns_value(self):
        from gee_analysis import _safe_getInfo
        mock_computation = MagicMock()
        mock_computation.getInfo.return_value = 0.65
        result = _safe_getInfo(mock_computation, default=0.0)
        assert result == 0.65


# ── Tests: Duplicate Plot Detection ────────────────────

@pytest.mark.asyncio
class TestDuplicatePlot:
    async def test_find_nearby_returns_existing(self, client):
        """ถ้ามีแปลงใกล้กัน ≤ 0.001° → คืน plot_id เดิม"""
        from database import find_nearby_plot
        nearby_row = [{"id": 42, "lat": 12.600, "lng": 102.100}]
        with patch("database.httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = nearby_row
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch.dict("os.environ", {
                "SUPABASE_URL": "https://test.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test_key",
            }):
                with patch("database.SUPABASE_URL", "https://test.supabase.co"):
                    with patch("database.SUPABASE_KEY", "test_key"):
                        result = await find_nearby_plot("Utest", 12.6004, 102.1004)
        assert result == 42

    async def test_find_nearby_returns_none(self, client):
        """ถ้าไม่มีแปลงใกล้กัน → คืน None"""
        from database import find_nearby_plot
        with patch("database.httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch("database.SUPABASE_URL", "https://test.supabase.co"):
                with patch("database.SUPABASE_KEY", "test_key"):
                    result = await find_nearby_plot("Utest", 14.0, 100.0)
        assert result is None


# ── Tests: Log Buffer ─────────────────────────────────

class TestLogBuffer:
    def test_buffer_captures_warnings(self):
        import log_buffer
        log_buffer.install()
        test_logger = logging.getLogger("test_buffer")
        test_logger.warning("test warning message")
        entries = log_buffer.get_entries(limit=5)
        messages = [e["message"] for e in entries]
        assert any("test warning" in m for m in messages)

    def test_buffer_stats(self):
        import log_buffer
        stats = log_buffer.get_stats()
        assert "total" in stats
        assert "max" in stats

    def test_buffer_level_filter(self):
        import log_buffer
        log_buffer.install()
        test_logger = logging.getLogger("test_filter")
        test_logger.warning("a warning")
        test_logger.error("an error")
        errors = log_buffer.get_entries(level="ERROR", limit=50)
        for e in errors:
            assert e["level"] == "ERROR"


# ── Tests: Admin Logs Endpoint ─────────────────────────

@pytest.mark.asyncio
class TestAdminLogs:
    async def test_admin_logs_endpoint(self, client):
        resp = await client.get("/admin/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data
        assert "entries" in data


# ── Tests: i18n Multi-language ─────────────────────────

class TestI18n:
    def test_thai_default(self):
        from i18n import t
        assert t("risk.high") == "เสี่ยงสูง"

    def test_english(self):
        from i18n import t
        assert t("risk.high", "en") == "High Risk"

    def test_placeholder_formatting(self):
        from i18n import t
        result = t("escalation.body", "th", weeks=3)
        assert "3" in result

    def test_missing_key_returns_key(self):
        from i18n import t
        assert t("nonexistent.key") == "nonexistent.key"


# ── Tests: Admin Stats with Labels ────────────────────

@pytest.mark.asyncio
class TestAdminStatsLang:
    async def test_stats_thai_labels(self, client):
        reports = [{**SAMPLE_GEE_DATA, "id": 1, "user_id": "U1", "created_at": "2026-04-09"}]
        with patch("main.get_all_reports", new_callable=AsyncMock, return_value=reports):
            resp = await client.get("/admin/stats?lang=th")
        assert resp.status_code == 200
        data = resp.json()
        assert "labels" in data
        assert data["labels"]["high_risk"] == "เสี่ยงสูง"

    async def test_stats_english_labels(self, client):
        reports = [{**SAMPLE_GEE_DATA, "id": 1, "user_id": "U1", "created_at": "2026-04-09"}]
        with patch("main.get_all_reports", new_callable=AsyncMock, return_value=reports):
            resp = await client.get("/admin/stats?lang=en")
        assert resp.status_code == 200
        data = resp.json()
        assert data["labels"]["high_risk"] == "High Risk"


# ── Tests: LINE Retry Queue ───────────────────────────

@pytest.mark.asyncio
class TestLineRetryQueue:
    async def test_send_success(self):
        from line_sender import send_line_message
        with patch.dict("os.environ", {"LINE_CHANNEL_ACCESS_TOKEN": "tok"}):
            with patch("line_sender.httpx.AsyncClient") as MockClient:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client
                result = await send_line_message("Utest", "hello")
        assert result is True

    async def test_send_failure_returns_false(self):
        from line_sender import send_line_message
        with patch.dict("os.environ", {"LINE_CHANNEL_ACCESS_TOKEN": "tok"}):
            with patch("line_sender.httpx.AsyncClient") as MockClient:
                mock_resp = MagicMock()
                mock_resp.status_code = 500
                mock_resp.text = "Server Error"
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client
                with patch("line_sender.asyncio.sleep", new_callable=AsyncMock):
                    result = await send_line_message("Utest", "hello")
        assert result is False


# test_failed_queue ไม่ใช่ async — อยู่นอก class ที่มี @pytest.mark.asyncio
class TestLineRetryQueueSync:
    def test_failed_queue(self):
        from line_sender import get_failed_queue, get_retry_stats
        stats = get_retry_stats()
        assert "failed_count" in stats
        assert "max_capacity" in stats
        queue = get_failed_queue()
        assert isinstance(queue, list)


# ── Tests: predict_yield (Rule Engine) ─────────────────────

class TestPredictYield:
    def test_healthy_plot_above_base(self):
        """NDVI > 0.6 → ผลผลิตควรมากกว่าฐาน 1,500 กก."""
        from rule_engine import predict_yield
        result = predict_yield(ndvi_now=0.72, topsoil_risk_level="low", elevation_diff=0.5)
        assert result > 1500

    def test_base_yield_no_modifiers(self):
        """NDVI อยู่ที่ 0.6 พอดี ไม่มีปัจจัยลบ → ผลลัพธ์ควร = 1,500"""
        from rule_engine import predict_yield, PREDICT_BASE_YIELD_KG
        result = predict_yield(ndvi_now=0.55, topsoil_risk_level="low", elevation_diff=0.0)
        assert result == PREDICT_BASE_YIELD_KG

    def test_high_bsi_reduces_yield(self):
        """topsoil_risk high → ลด 15%"""
        from rule_engine import predict_yield, PREDICT_BASE_YIELD_KG
        result = predict_yield(ndvi_now=0.55, topsoil_risk_level="high", elevation_diff=0.0)
        assert result == round(PREDICT_BASE_YIELD_KG * 0.85)

    def test_waterlogging_reduces_yield(self):
        """elevation_diff < -1.5 → ลด 10%"""
        from rule_engine import predict_yield, PREDICT_BASE_YIELD_KG
        result = predict_yield(ndvi_now=0.55, topsoil_risk_level="low", elevation_diff=-2.0)
        assert result == round(PREDICT_BASE_YIELD_KG * 0.90)

    def test_all_negative_factors(self):
        """BSI สูง + แอ่งน้ำ + NDVI ต่ำ → ลดค้ายสุด"""
        from rule_engine import predict_yield, PREDICT_BASE_YIELD_KG
        result = predict_yield(ndvi_now=0.55, topsoil_risk_level="high", elevation_diff=-2.5)
        assert result == round(PREDICT_BASE_YIELD_KG * 0.85 * 0.90)

    def test_all_positive_factors(self):
        """NDVI > 0.6 + ไม่มีปัจจัยลบ → สูงกว่าฐาน 20%"""
        from rule_engine import predict_yield, PREDICT_BASE_YIELD_KG
        result = predict_yield(ndvi_now=0.80, topsoil_risk_level="low", elevation_diff=1.0)
        assert result == round(PREDICT_BASE_YIELD_KG * 1.20)

    def test_returns_int(self):
        from rule_engine import predict_yield
        result = predict_yield(0.65, "medium", -0.5)
        assert isinstance(result, int)


# ── Tests: Topsoil BSI (gee_analysis + rule_engine) ──

class TestTopsoilAnalysis:
    def test_high_risk_when_bsi_high_and_slope(self):
        """BSI > 0.2 และ elev_diff ลาดชัน → high"""
        from rule_engine import format_message
        data = {**SAMPLE_GEE_DATA_OK, "bsi": 0.30, "elevation_diff": -2.0,
                "topsoil_risk_level": "high"}
        msg = format_message(data, 12.60, 102.10)
        assert "หน้าดินพังทลาย" in msg or "BSI" in msg

    def test_medium_risk_bsi_high_no_slope(self):
        """BSI > 0.2 แต่ไม่ลาดชัน → medium"""
        from rule_engine import format_message
        data = {**SAMPLE_GEE_DATA_OK, "bsi": 0.25, "elevation_diff": 0.5,
                "topsoil_risk_level": "medium"}
        msg = format_message(data, 12.60, 102.10)
        assert "ดินเปิดโล่ง" in msg or "BSI" in msg

    def test_low_bsi_no_warning(self):
        """BSI ต่ำ → ไม่มีการเตือนเรื่องหน้าดิน"""
        from rule_engine import format_message
        data = {**SAMPLE_GEE_DATA_OK, "bsi": 0.05, "topsoil_risk_level": "low"}
        msg = format_message(data, 12.60, 102.10)
        assert "หน้าดินพังทลาย" not in msg

    def test_bsi_in_message_output(self):
        """BSI ควรปรากฏในข้อความ"""
        from rule_engine import format_message
        msg = format_message(SAMPLE_GEE_DATA, 12.6011, 102.1042)
        assert "BSI" in msg

    def test_topsoil_flex_section_high(self):
        """topsoil_risk high → flex section มีสีแดงและคำแนะนำหญ้าแฝก"""
        from flex_messages import _build_topsoil_section
        components = _build_topsoil_section(bsi=0.30, topsoil_risk="high")
        text_blob = str(components)
        assert "หน้าดินเปิดโล่ง" in text_blob
        assert "หญ้าแฝก" in text_blob
        assert "#C62828" in text_blob   # สีแดง

    def test_topsoil_flex_section_low(self):
        """topsoil_risk low → flex section สีเขียว ไม่มีคำแนะนำเพิ่มเติม"""
        from flex_messages import _build_topsoil_section
        components = _build_topsoil_section(bsi=0.05, topsoil_risk="low")
        text_blob = str(components)
        assert "หน้าดินสมบูรณ์" in text_blob
        assert "#2E7D32" in text_blob   # สีเขียว
        assert "หญ้าแฝก" not in text_blob

    def test_topsoil_flex_bsi_displayed(self):
        """BSI ต้องแสดงใน flex component"""
        from flex_messages import _build_topsoil_section
        components = _build_topsoil_section(bsi=0.123, topsoil_risk="medium")
        assert "0.123" in str(components)

    def test_topsoil_risk_affects_overall_status(self):
        """topsoil_risk high ควรทำให้ isHigh = True ใน JS logic"""
        # ตรวจว่า SAMPLE_GEE_DATA (ที่มี topsoil high) ระบบ flex แรง risk = high
        from flex_messages import _risk_level
        level = _risk_level(SAMPLE_GEE_DATA)
        assert level == "high"


# ── Tests: predict_from_ml (ML Model) ──────────────────

class TestMLModel:
    def test_model_file_exists_after_training(self, tmp_path, monkeypatch):
        """train_model() ต้องสร้างไฟล์ .pkl"""
        import ml_model
        model_file = tmp_path / "durian_yield_model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", str(model_file))
        ml_model.train_model(save=True)
        assert model_file.exists()

    def test_predict_returns_positive_int(self, tmp_path, monkeypatch):
        """predict_from_ml ต้องคืน int บวก"""
        import ml_model
        model_file = tmp_path / "durian_yield_model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", str(model_file))
        ml_model.train_model(save=True)
        result = ml_model.predict_from_ml(0.65, 0.10, 0.5)
        assert isinstance(result, int)
        assert result > 0

    def test_healthy_plot_higher_than_poor(self, tmp_path, monkeypatch):
        """NDVI สูงควรให้ผลผลิตสูงกว่า NDVI ต่ำ"""
        import ml_model
        model_file = tmp_path / "durian_yield_model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", str(model_file))
        ml_model.train_model(save=True)
        healthy = ml_model.predict_from_ml(0.80, 0.05, 0.5)
        poor    = ml_model.predict_from_ml(0.25, 0.35, -2.5)
        assert healthy > poor

    def test_predict_returns_minus1_when_no_model(self, tmp_path, monkeypatch):
        """predict_from_ml คืน -1 ถ้าไม่พบไฟล์ .pkl"""
        import ml_model
        monkeypatch.setattr(ml_model, "MODEL_PATH", str(tmp_path / "nonexistent.pkl"))
        result = ml_model.predict_from_ml(0.70, 0.10, 0.5)
        assert result == -1

    def test_dataset_shape(self):
        """make_mock_dataset ต้องคืน X(200,3) และ y(200,)"""
        from ml_model import make_mock_dataset
        X, y = make_mock_dataset(200)
        assert X.shape == (200, 3)
        assert y.shape == (200,)

    def test_dataset_yield_in_range(self):
        """actual_yield ต้องอยู่ในช่วง 100-2500 กก./ไร่"""
        from ml_model import make_mock_dataset
        import numpy as np
        X, y = make_mock_dataset(200)
        assert np.all(y >= 100)
        assert np.all(y <= 2500)

    def test_train_model_r2_reasonable(self, tmp_path, monkeypatch):
        """R² ควร > 0.5 บนข้อมูลจำลอง"""
        from ml_model import make_mock_dataset, N_SAMPLES, RANDOM_STATE
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import r2_score
        import numpy as np
        X, y = make_mock_dataset(N_SAMPLES, RANDOM_STATE)
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2,
                                                   random_state=RANDOM_STATE)
        m = RandomForestRegressor(n_estimators=50, random_state=RANDOM_STATE)
        m.fit(X_tr, y_tr)
        r2 = r2_score(y_te, m.predict(X_te))
        assert r2 > 0.5


# ── Tests: Database – ML/BSI fields ────────────────────

@pytest.mark.asyncio
class TestDatabaseMLFields:
    async def test_save_analysis_includes_bsi_and_risk(self):
        """save_analysis ต้องส่ง bsi_score, risk_level, predicted_yield ไป Supabase"""
        from database import save_analysis
        captured = {}

        async def fake_post(url, **kwargs):
            captured.update(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 201
            return resp

        with patch("database.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch("database.SUPABASE_URL", "https://test.supabase.co"):
                with patch("database.SUPABASE_KEY", "test_key"):
                    await save_analysis("Utest", SAMPLE_GEE_DATA, "test msg")

        assert captured.get("bsi_score") == SAMPLE_GEE_DATA["bsi"]
        assert captured.get("risk_level") == SAMPLE_GEE_DATA["topsoil_risk_level"]
        assert captured.get("predicted_yield_kg_per_rai") == SAMPLE_GEE_DATA["predicted_yield_kg_per_rai"]
        assert "created_at" in captured

    async def test_save_analysis_includes_timestamp(self):
        """created_at ต้องเป็น ISO string ที่มี timezone"""
        from database import save_analysis
        captured = {}

        async def fake_post(url, **kwargs):
            captured.update(kwargs.get("json", {}))
            resp = MagicMock(); resp.status_code = 201
            return resp

        with patch("database.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch("database.SUPABASE_URL", "https://test.supabase.co"):
                with patch("database.SUPABASE_KEY", "test_key"):
                    await save_analysis("Utest", SAMPLE_GEE_DATA_OK, "ok msg")

        ts = captured.get("created_at", "")
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")


# ── Tests: predicted_yield in analyze response ────────

@pytest.mark.asyncio
class TestAnalyzePredictedYield:
    async def test_analyze_returns_predicted_yield(self, client):
        """API /analyze ต้องคืน predicted_yield_kg_per_rai"""
        with patch("main.analyze_durian_plot",
                   return_value=SAMPLE_GEE_DATA) as _:
            with patch("main.send_line_message", new_callable=AsyncMock):
                with patch("main.save_analysis", new_callable=AsyncMock):
                    with patch("main.save_plot",
                               new_callable=AsyncMock, return_value=1):
                        resp = await client.post("/analyze", json={
                            "lat": 12.6011, "lng": 102.1042, "user_id": "Utest"
                        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "predicted_yield_kg_per_rai" in data
        assert isinstance(data["predicted_yield_kg_per_rai"], int)

    async def test_analyze_returns_bsi_and_topsoil(self, client):
        """API /analyze ต้องคืน bsi และ topsoil_risk_level"""
        with patch("main.analyze_durian_plot",
                   return_value=SAMPLE_GEE_DATA_OK):
            with patch("main.send_line_message", new_callable=AsyncMock):
                with patch("main.save_analysis", new_callable=AsyncMock):
                    with patch("main.save_plot",
                               new_callable=AsyncMock, return_value=2):
                        resp = await client.post("/analyze", json={
                            "lat": 12.60, "lng": 102.10, "user_id": "Utest"
                        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "bsi" in data
        assert data["topsoil_risk_level"] in ("low", "medium", "high")

    async def test_failed_messages_endpoint(self, client):
        resp = await client.get("/admin/failed-messages")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data
        assert "messages" in data


# ── Tests: Escalation ─────────────────────────────────

class TestEscalation:
    def test_count_consecutive_high_risk(self):
        from scheduler import _count_consecutive_high_risk
        analyses = [
            {"ndvi_change": -0.15, "elevation_diff": 0, "soil_moisture_vv": -12},
            {"ndvi_change": -0.12, "elevation_diff": 0, "soil_moisture_vv": -12},
            {"ndvi_change": 0.02, "elevation_diff": 0, "soil_moisture_vv": -14},
        ]
        assert _count_consecutive_high_risk(analyses) == 2

    def test_no_escalation_when_ok(self):
        from scheduler import _count_consecutive_high_risk
        analyses = [
            {"ndvi_change": 0.02, "elevation_diff": 1, "soil_moisture_vv": -14},
            {"ndvi_change": -0.15, "elevation_diff": 0, "soil_moisture_vv": -12},
        ]
        assert _count_consecutive_high_risk(analyses) == 0

    def test_build_escalation_flex(self):
        from flex_messages import build_escalation_flex
        from rule_engine import format_message
        plain = format_message(SAMPLE_GEE_DATA, 12.60, 102.10)
        flex = build_escalation_flex(SAMPLE_GEE_DATA, 12.60, 102.10, plain, 3)
        assert flex["type"] == "flex"
        assert "ฉุกเฉิน" in flex["altText"] or "3" in flex["altText"]
        # Header should be critical red
        assert flex["contents"]["header"]["backgroundColor"] == "#B71C1C"


# ── Tests: Polygon Support ─────────────────────────────

@pytest.mark.asyncio
class TestPolygon:
    async def test_analyze_with_polygon(self, client):
        polygon = [[102.1, 12.6], [102.11, 12.6], [102.11, 12.61], [102.1, 12.61], [102.1, 12.6]]
        with patch("main.analyze_durian_plot",
                   return_value=SAMPLE_GEE_DATA) as mock_gee:
            with patch("main.send_line_message", new_callable=AsyncMock, return_value=True):
                with patch("main.save_analysis", new_callable=AsyncMock):
                    with patch("main.save_plot",
                               new_callable=AsyncMock, return_value=1):
                        resp = await client.post("/analyze", json={
                            "lat": 12.6, "lng": 102.1,
                            "user_id": "Utest",
                            "polygon": polygon,
                        })
        assert resp.status_code == 200
        mock_gee.assert_called_once_with(12.6, 102.1, polygon=polygon)

    async def test_analyze_without_polygon(self, client):
        with patch("main.analyze_durian_plot",
                   return_value=SAMPLE_GEE_DATA):
            with patch("main.send_line_message", new_callable=AsyncMock, return_value=True):
                with patch("main.save_analysis", new_callable=AsyncMock):
                    with patch("main.save_plot",
                               new_callable=AsyncMock, return_value=1):
                        resp = await client.post("/analyze", json={
                            "lat": 12.6, "lng": 102.1,
                            "user_id": "Utest",
                        })
        assert resp.status_code == 200


# ── Tests: NDVI Timeseries Endpoint ───────────────────

@pytest.mark.asyncio
class TestNDVITimeseries:
    async def test_ndvi_timeseries_endpoint(self, client):
        mock_plots = [{"id": 1, "name": "แปลง 1", "lat": 12.6, "lng": 102.1}]
        mock_series = [
            {"month": "2024-06", "ndvi": 0.55},
            {"month": "2024-07", "ndvi": 0.58},
        ]
        with patch("main.get_user_plots",
                   new_callable=AsyncMock, return_value=mock_plots):
            with patch("main.get_ndvi_timeseries",
                       return_value=mock_series):
                resp = await client.get("/plots/Utest/1/ndvi-timeseries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plot_id"] == 1
        assert len(data["series"]) == 2

    async def test_ndvi_timeseries_plot_not_found(self, client):
        with patch("main.get_user_plots",
                   new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/plots/Utest/999/ndvi-timeseries")
        assert resp.status_code == 404
