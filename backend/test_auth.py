"""
test_auth.py — การยืนยันตัวตน LINE, สิทธิ์ความเป็นเจ้าของแปลง, admin, rate limit (Phase 1a)
"""

import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, TEST_CHANNEL_ID, auth_as, fake_verify_line_token

# GEE ปลอมก่อน import main (แบบเดียวกับ test_system.py)
if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


def _from_ip(ip: str, headers: dict | None = None) -> dict:
    """จำลองคำขอจาก IP ภายนอก (ASGITransport ปกติเป็น 127.0.0.1 ซึ่งไม่ถูกจำกัด)"""
    return {**(headers or {}), "CF-Connecting-IP": ip}


# ── ต้องมี token ──────────────────────────────────────

@pytest.mark.asyncio
class TestRequiresToken:
    @pytest.mark.parametrize("method,path", [
        ("get",    "/plots/U1"),
        ("get",    "/plots/U1/1/history"),
        ("get",    "/plots/U1/1/persistent-wet"),
        ("get",    "/plots/U1/1/ndvi-timeseries"),
        ("delete", "/plots/U1/1"),
        ("patch",  "/user/U1/notify?enabled=true"),
        ("patch",  "/user/U1/notify-digest?enabled=true"),
    ])
    async def test_user_endpoints_reject_missing_token(self, client, method, path):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 401

    async def test_analyze_rejects_missing_token(self, client):
        resp = await client.post("/analyze", json={"lat": 13.5, "lng": 99.8, "user_id": "U1"})
        assert resp.status_code == 401

    async def test_grid_rejects_missing_token(self, client):
        poly = [[99.8, 13.5], [99.81, 13.5], [99.81, 13.51], [99.8, 13.5]]
        resp = await client.post("/analyze/grid", json={"polygon": poly})
        assert resp.status_code == 401

    async def test_invalid_token_rejected(self, client):
        resp = await client.get("/plots/U1", headers={"Authorization": "Bearer forged",
                                                      "X-Line-Token-Type": "id_token"})
        assert resp.status_code == 401

    async def test_unknown_token_type_rejected(self, client):
        resp = await client.get("/plots/U1", headers={"Authorization": "Bearer tok-U1",
                                                      "X-Line-Token-Type": "password"})
        assert resp.status_code == 401

    async def test_static_and_health_need_no_token(self, client):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/liff/config.js")).status_code == 200


# ── เจ้าของข้อมูลเท่านั้น ─────────────────────────────

@pytest.mark.asyncio
class TestOwnership:
    async def test_cannot_list_other_users_plots(self, client):
        with patch("main.get_user_plots", new_callable=AsyncMock, return_value=[]) as m:
            resp = await client.get("/plots/U2", headers=auth_as("U1"))
        assert resp.status_code == 403
        m.assert_not_called()

    async def test_can_list_own_plots(self, client):
        with patch("main.get_user_plots", new_callable=AsyncMock, return_value=[{"id": 7}]), \
             patch("main.get_latest_risk_by_plots", new_callable=AsyncMock, return_value={}):
            resp = await client.get("/plots/U1", headers=auth_as("U1"))
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_history_of_someone_elses_plot_is_404(self, client):
        """เดิม history ไม่เช็กเจ้าของเลย — ใส่ user_id ตัวเองใน URL แล้วไล่ plot_id ได้"""
        with patch("main.get_plot_by_id", new_callable=AsyncMock,
                   return_value={"id": 5, "user_id": "U2"}), \
             patch("main.get_plot_history", new_callable=AsyncMock, return_value=[]) as hist:
            resp = await client.get("/plots/U1/5/history", headers=auth_as("U1"))
        assert resp.status_code == 404
        hist.assert_not_called()

    async def test_persistent_wet_of_someone_elses_plot_is_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock,
                   return_value={"id": 5, "user_id": "U2"}), \
             patch("main.get_persistent_wet_points", new_callable=AsyncMock, return_value=[]) as pw:
            resp = await client.get("/plots/U1/5/persistent-wet", headers=auth_as("U1"))
        assert resp.status_code == 404
        pw.assert_not_called()

    async def test_missing_plot_is_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=None):
            resp = await client.get("/plots/U1/999/history", headers=auth_as("U1"))
        assert resp.status_code == 404

    async def test_cannot_delete_with_someone_elses_user_id(self, client):
        with patch("main.delete_plot", new_callable=AsyncMock, return_value=True) as d:
            resp = await client.delete("/plots/U2/5", headers=auth_as("U1"))
        assert resp.status_code == 403
        d.assert_not_called()

    async def test_cannot_change_someone_elses_notification(self, client):
        with patch("main.set_notify_digest", new_callable=AsyncMock, return_value=True) as s:
            resp = await client.patch("/user/U2/notify-digest?enabled=true", headers=auth_as("U1"))
        assert resp.status_code == 403
        s.assert_not_called()

    async def test_analyze_pushes_only_to_token_owner(self, client):
        """user_id ใน body ถูกเมิน — push ไปหาเจ้าของ token เท่านั้น"""
        data = {"ndvi_now": 0.6, "ndvi_prev": 0.6, "ndvi_change": 0.0, "soil_moisture_vv": -14.0,
                "elevation": 10.0, "elevation_diff": 0.0, "bsi": 0.1}
        with patch("main.analyze_durian_plot", return_value=data), \
             patch("main.send_line_message", new_callable=AsyncMock) as send, \
             patch("main.save_analysis", new_callable=AsyncMock), \
             patch("main.upsert_user", new_callable=AsyncMock) as upsert, \
             patch("main.save_plot", new_callable=AsyncMock, return_value=1) as save_plot:
            resp = await client.post("/analyze", headers=auth_as("U1"),
                                     json={"lat": 13.5, "lng": 99.8, "user_id": "UVICTIM"})
        assert resp.status_code == 200
        assert send.call_args.args[0] == "U1"
        assert upsert.call_args.args[0] == "U1"
        assert save_plot.call_args.args[0] == "U1"


# ── admin ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestAdmin:
    @pytest.mark.parametrize("method,path", [
        ("get",  "/admin/stats"),
        ("get",  "/admin/reports"),
        ("post", "/admin/trigger/daily-scan"),
        ("post", "/admin/trigger/rain-alert"),
        ("post", "/admin/richmenu/setup"),
        ("get",  "/analyze/preview?lat=13.5&lng=99.8"),
        ("get",  "/weather-alert/preview?lat=13.5&lng=99.8"),
    ])
    async def test_admin_endpoints_reject_without_key(self, client, method, path):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 403

    async def test_user_token_is_not_admin(self, client):
        resp = await client.get("/admin/stats", headers=auth_as("U1"))
        assert resp.status_code == 403

    async def test_wrong_admin_key(self, client):
        resp = await client.get("/admin/stats", headers={"X-Admin-Key": "wrong"})
        assert resp.status_code == 403

    async def test_iot_reading_requires_admin(self, client):
        """IoT สั่ง push ข้อความ LINE ได้ ต้องเป็น admin"""
        body = {"plot_id": 1, "sensor_id": "s1", "depth_cm": 30, "moisture_pct": 95, "temp_c": 28}
        with patch("main.send_line_message", new_callable=AsyncMock) as send:
            resp = await client.post("/iot/reading", json=body, headers=auth_as("U1"))
        assert resp.status_code == 403
        send.assert_not_called()

    async def test_admin_fails_closed_when_key_unset(self, client, monkeypatch):
        """เดิมถ้าไม่ได้ตั้ง ADMIN_API_KEY ระบบปล่อยผ่านทุกคำขอ"""
        monkeypatch.delenv("ADMIN_API_KEY")
        resp = await client.get("/admin/stats", headers={"X-Admin-Key": ""})
        assert resp.status_code == 503

    async def test_admin_with_key(self, client):
        with patch("main.get_all_reports", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/admin/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200


# ── webhook ───────────────────────────────────────────

class TestWebhookFailClosed:
    def test_rejects_when_secret_unset(self, monkeypatch):
        from webhook import _verify_signature
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "")
        assert _verify_signature(b'{"events":[]}', "") is False


# ── การตรวจ token กับ LINE (ไม่ยิง LINE จริง) ─────────

def _mock_httpx(post=None, get=None):
    client = AsyncMock()
    client.post = AsyncMock(side_effect=post)
    client.get = AsyncMock(side_effect=get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _resp(status: int, body: dict):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


@pytest.mark.asyncio
class TestVerifyLineToken:
    async def test_id_token_ok_and_cached(self):
        import auth
        auth.clear_cache()
        posts = []

        async def post(url, data=None, **kw):
            posts.append(data)
            return _resp(200, {"sub": "U1", "exp": time.time() + 3600})

        with patch("auth.httpx.AsyncClient", return_value=_mock_httpx(post=post)):
            assert await auth.verify_line_token("idtok", "id_token") == "U1"
            assert await auth.verify_line_token("idtok", "id_token") == "U1"
        assert len(posts) == 1                        # ครั้งที่สองมาจาก cache
        assert posts[0]["client_id"] == TEST_CHANNEL_ID

    async def test_id_token_rejected_by_line(self):
        import auth
        auth.clear_cache()

        async def post(url, data=None, **kw):
            return _resp(400, {"error": "invalid_request", "error_description": "IdToken expired."})

        with patch("auth.httpx.AsyncClient", return_value=_mock_httpx(post=post)):
            assert await auth.verify_line_token("expired", "id_token") is None

    async def test_access_token_from_other_channel_rejected(self):
        import auth
        auth.clear_cache()

        async def get(url, params=None, headers=None, **kw):
            if url == auth.LINE_VERIFY_URL:
                return _resp(200, {"client_id": "9999999999", "expires_in": 3000, "scope": "profile"})
            return _resp(200, {"userId": "U1"})

        with patch("auth.httpx.AsyncClient", return_value=_mock_httpx(get=get)):
            assert await auth.verify_line_token("acc", "access_token") is None

    async def test_access_token_ok(self):
        import auth
        auth.clear_cache()

        async def get(url, params=None, headers=None, **kw):
            if url == auth.LINE_VERIFY_URL:
                return _resp(200, {"client_id": TEST_CHANNEL_ID, "expires_in": 3000})
            return _resp(200, {"userId": "U1"})

        with patch("auth.httpx.AsyncClient", return_value=_mock_httpx(get=get)):
            assert await auth.verify_line_token("acc-ok", "access_token") == "U1"


class TestAuthConfig:
    def test_channel_id_from_liff_url(self, monkeypatch):
        import auth
        monkeypatch.delenv("LINE_LOGIN_CHANNEL_ID")
        monkeypatch.setenv("LIFF_URL", "https://liff.line.me/2010580115-di08GvXS")
        assert auth.login_channel_id() == "2010580115"

    def test_explicit_channel_id_wins(self, monkeypatch):
        import auth
        monkeypatch.setenv("LINE_LOGIN_CHANNEL_ID", "555")
        assert auth.login_channel_id() == "555"

    def test_dev_auth_never_on_render(self, monkeypatch):
        import auth
        monkeypatch.setenv("IAMROOT_DEV_AUTH_USER", "dev-user")
        assert auth.dev_auth_user() == "dev-user"
        monkeypatch.setenv("RENDER", "true")
        assert auth.dev_auth_user() is None


# ── รูปย่อแปลงใน bucket public ต้องเดา path ไม่ได้ ─────

class TestThumbnailPath:
    def test_path_is_stable_but_not_guessable(self, monkeypatch):
        import plot_image_service as pis
        monkeypatch.setattr(pis, "SUPABASE_KEY", "server-secret")
        p1 = pis._thumbnail_path(42)
        assert p1 == pis._thumbnail_path(42)               # เขียนทับที่เดิมได้
        assert p1 != "thumbnails/42.png"                    # ไม่ใช่ path เลขเรียงแบบเดิม
        assert p1.startswith("thumbnails/42-") and len(p1) > len("thumbnails/42-.png") + 16
        monkeypatch.setattr(pis, "SUPABASE_KEY", "other-secret")
        assert pis._thumbnail_path(42) != p1               # ไม่รู้ key ก็คำนวณไม่ได้


# ── rate limit ───────────────────────────────────────

@pytest.mark.asyncio
class TestRateLimit:
    async def test_static_files_not_counted(self, client, monkeypatch):
        import middleware
        monkeypatch.setattr(middleware, "IP_LIMIT_PER_MIN", 2)
        for _ in range(10):
            resp = await client.get("/liff/config.js", headers=_from_ip("203.0.113.9"))
            assert resp.status_code == 200

    async def test_ip_ceiling_still_applies_to_api(self, client, monkeypatch):
        import middleware
        monkeypatch.setattr(middleware, "IP_LIMIT_PER_MIN", 2)
        codes = [(await client.get("/plots/U1", headers=_from_ip("203.0.113.9"))).status_code
                 for _ in range(3)]
        assert codes == [401, 401, 429]

    async def test_training_room_50_users_one_wifi(self, client):
        """อบรม 50 คนหลัง IP เดียวกัน คนละ 10 คำขอ/นาที ต้องไม่มีใครโดน 429"""
        with patch("main.get_user_plots", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_latest_risk_by_plots", new_callable=AsyncMock, return_value={}):
            for round_ in range(10):
                for i in range(50):
                    uid = f"U{i:03d}"
                    resp = await client.get(f"/plots/{uid}", headers=_from_ip("198.51.100.7", auth_as(uid)))
                    assert resp.status_code == 200, (round_, uid, resp.status_code)

    async def test_analyze_limited_per_user_not_per_ip(self, client):
        data = {"ndvi_now": 0.6, "ndvi_prev": 0.6, "ndvi_change": 0.0, "soil_moisture_vv": -14.0,
                "elevation": 10.0, "elevation_diff": 0.0, "bsi": 0.1}
        body = {"lat": 13.5, "lng": 99.8}
        with patch("main.analyze_durian_plot", return_value=data), \
             patch("main.send_line_message", new_callable=AsyncMock), \
             patch("main.save_analysis", new_callable=AsyncMock), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock, return_value=1):
            codes_a = [(await client.post("/analyze", json=body,
                                          headers=_from_ip("198.51.100.7", auth_as("UA")))).status_code
                       for _ in range(4)]
            code_b = (await client.post("/analyze", json=body,
                                        headers=_from_ip("198.51.100.7", auth_as("UB")))).status_code
        assert codes_a == [200, 200, 200, 429]
        assert code_b == 200     # คนข้างๆ ใน Wi-Fi เดียวกันไม่โดนลูกหลง
