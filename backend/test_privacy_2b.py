"""
test_privacy_2b.py — Phase 2b (PDPA): ความยินยอม, ดาวน์โหลดข้อมูล, ใครเปิดดูแปลงของฉัน, ขอลบ/ลบถาวร
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import access
import privacy

CONSENTED = {"user_id": "U1", "consent_version": privacy.PRIVACY_VERSION, "share_with_officers": True,
             "consent_photos_research": False, "deletion_requested_at": None}


class TestPayload:
    def test_needs_consent(self):
        assert privacy.consent_payload(None)["needs_consent"]
        assert privacy.consent_payload({"consent_version": "2020-01-01"})["needs_consent"]
        p = privacy.consent_payload(CONSENTED)
        assert not p["needs_consent"] and p["choices"] == {"share_with_officers": True, "photos_research": False}

    def test_policy_page_matches_version(self):
        html = (Path(__file__).parent.parent / "liff" / "privacy.html").read_text(encoding="utf-8")
        y, m, d = privacy.PRIVACY_VERSION.split("-")
        assert f"{int(d)} ตุลาคม {int(y) + 543}" in html            # ฉบับในหน้าเว็บตรงกับ PRIVACY_VERSION
        for must in ("ติดต่อเจ้าหน้าที่", "สคส.", "ขอลบ", "Supabase", "Render", "Google Earth Engine"):
            assert must in html

    def test_optional_items_default_off(self):
        assert {o["key"] for o in privacy.OPTIONAL} == {"share_with_officers", "photos_research"}
        assert privacy.consent_payload({})["choices"] == {"share_with_officers": False, "photos_research": False}


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    access.invalidate()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


def _priv(value):
    return patch("main.get_user_privacy", new_callable=AsyncMock, return_value=value)


@pytest.mark.asyncio
class TestConsentApi:
    async def test_wrong_version_409(self, client):
        resp = await client.post("/me/consent", headers=auth_as("U1"), json={"version": "1999-01-01"})
        assert resp.status_code == 409

    async def test_accept_new_user(self, client):
        with patch("main.get_user_privacy", new_callable=AsyncMock, side_effect=[None, CONSENTED]), \
             patch("main.upsert_user", new_callable=AsyncMock) as up, \
             patch("main.save_consent", new_callable=AsyncMock, return_value=True) as sc, \
             patch("main.log_audit", new_callable=AsyncMock) as audit:
            resp = await client.post("/me/consent", headers=auth_as("U1"),
                                     json={"version": privacy.PRIVACY_VERSION, "share_with_officers": True})
        assert resp.status_code == 200 and not resp.json()["needs_consent"]
        up.assert_awaited_once()
        assert sc.await_args.args == ("U1", privacy.PRIVACY_VERSION, True, False, "accept")
        assert audit.await_args.args[2] == "consent_accept"

    async def test_update_existing(self, client):
        with patch("main.get_user_privacy", new_callable=AsyncMock, side_effect=[CONSENTED, CONSENTED]), \
             patch("main.save_consent", new_callable=AsyncMock, return_value=True) as sc, \
             patch("main.log_audit", new_callable=AsyncMock):
            await client.post("/me/consent", headers=auth_as("U1"), json={"version": privacy.PRIVACY_VERSION})
        assert sc.await_args.args[4] == "update"

    async def test_analyze_blocked_without_consent(self, client):
        with patch("main.SUPABASE_CONFIGURED", True), _priv({"consent_version": None}), \
             patch("main._run_gee_analysis", new_callable=AsyncMock) as gee:
            resp = await client.post("/analyze", headers=auth_as("U1"), json={"lat": 13.5, "lng": 99.9})
        assert resp.status_code == 428
        gee.assert_not_called()

    async def test_analyze_blocked_when_deletion_requested(self, client):
        with patch("main.SUPABASE_CONFIGURED", True), _priv({**CONSENTED, "deletion_requested_at": "2026-10-25T00:00:00Z"}):
            resp = await client.post("/analyze", headers=auth_as("U1"), json={"lat": 13.5, "lng": 99.9})
        assert resp.status_code == 409

    async def test_analyze_allowed_with_consent(self, client):
        data = {"ndvi_now": .7, "ndvi_prev": .6, "elevation": 5.0}
        with patch("main.SUPABASE_CONFIGURED", True), _priv(CONSENTED), \
             patch("main._run_gee_analysis", new_callable=AsyncMock, return_value=data), \
             patch("main.format_message", return_value="m"), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock, return_value=3), \
             patch("main._store_boundary_check", new_callable=AsyncMock), \
             patch("main._assign_area", new_callable=AsyncMock), \
             patch("main.log_event", new_callable=AsyncMock), \
             patch("main.save_analysis", new_callable=AsyncMock):
            resp = await client.post("/analyze", headers=auth_as("U1"), json={"lat": 13.5, "lng": 99.9})
        assert resp.status_code == 200

    async def test_me_has_privacy(self, client):
        body = (await client.get("/me", headers=auth_as("U1"))).json()
        assert body["privacy"]["version"] == privacy.PRIVACY_VERSION
        assert body["privacy"]["needs_consent"] is False       # dev/test ไม่มี Supabase = ไม่ถาม


@pytest.mark.asyncio
class TestMyData:
    async def test_download(self, client):
        with patch("main.export_user_data", new_callable=AsyncMock, return_value={"plots": [{"id": 1}]}), \
             patch("main.log_audit", new_callable=AsyncMock) as audit:
            resp = await client.get("/me/data", headers=auth_as("U1"))
        assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
        assert json.loads(resp.content)["plots"] == [{"id": 1}]
        assert audit.await_args.args[2] == "export_data"

    async def test_access_log_hides_self_and_actor_ids(self, client):
        rows = [{"actor_user_id": "U1", "actor_role": "farmer", "action": "export_data", "target_type": "user",
                 "target_id": "U1", "created_at": "2026-10-25T01:00:00Z"},
                {"actor_user_id": "Uoff", "actor_role": "officer", "action": "view_plot", "target_type": "plot",
                 "target_id": "5", "created_at": "2026-10-25T02:00:00Z"}]
        with patch("main.list_audit_logs", new_callable=AsyncMock, return_value=rows):
            body = (await client.get("/me/access-log", headers=auth_as("U1"))).json()
        assert body["entries"] == [{"role_th": "เจ้าหน้าที่", "action": "view_plot", "plot_id": "5",
                                    "at": "2026-10-25T02:00:00Z"}]

    async def test_deletion_request_and_cancel(self, client):
        with patch("main.set_deletion_request", new_callable=AsyncMock, return_value=True) as sd, \
             patch("main.log_audit", new_callable=AsyncMock):
            r1 = await client.post("/me/deletion-request", headers=auth_as("U1"))
            with _priv({**CONSENTED, "deletion_requested_at": "2026-10-25T00:00:00Z"}):
                r2 = await client.post("/me/deletion-request/cancel", headers=auth_as("U1"))
        assert r1.status_code == 200 and r1.json()["grace_days"] == 30
        assert r2.status_code == 200
        assert [c.args[1] for c in sd.await_args_list] == [True, False]

    async def test_cancel_without_request_404(self, client):
        with _priv(CONSENTED):
            assert (await client.post("/me/deletion-request/cancel", headers=auth_as("U1"))).status_code == 404


@pytest.mark.asyncio
class TestPurge:
    async def test_needs_key(self, client):
        assert (await client.post("/admin/users/U1/purge", json={"confirm_user_id": "U1"})).status_code == 403

    async def test_confirm_must_match(self, client):
        with patch("main.purge_user", new_callable=AsyncMock) as pu:
            resp = await client.post("/admin/users/U1/purge", headers=ADMIN_HEADERS, json={"confirm_user_id": "U2"})
        assert resp.status_code == 422
        pu.assert_not_called()

    async def test_only_after_user_request(self, client):
        with _priv(CONSENTED), patch("main.purge_user", new_callable=AsyncMock) as pu:
            resp = await client.post("/admin/users/U1/purge", headers=ADMIN_HEADERS, json={"confirm_user_id": "U1"})
        assert resp.status_code == 409
        pu.assert_not_called()

    async def test_purge(self, client):
        with _priv({**CONSENTED, "deletion_requested_at": "2026-09-01T00:00:00Z"}), \
             patch("main.purge_user", new_callable=AsyncMock, return_value={"plots": 2}) as pu, \
             patch("main.log_audit", new_callable=AsyncMock) as audit:
            resp = await client.post("/admin/users/U1/purge", headers=ADMIN_HEADERS, json={"confirm_user_id": "U1"})
        assert resp.status_code == 200 and resp.json()["deleted_rows"] == {"plots": 2}
        pu.assert_awaited_once_with("U1")
        assert audit.await_args.args[5] is None        # audit ไม่เก็บ userId ที่ถูกลบ
