"""
test_roles_2a.py — Phase 2a: บทบาท farmer/officer/admin, พื้นที่, รหัสเชิญ, audit log, events
"""

import sys
from datetime import datetime, timedelta, timezone
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
import areas

OFFICER = {"role": "officer", "areas": ["7004"], "share_with_officers": False}
FARMER = {"role": "farmer", "areas": [], "share_with_officers": False}
ADMIN = {"role": "admin", "areas": [], "share_with_officers": False}
PLOT = {"id": 5, "user_id": "Ufarmer", "area_code": "7004", "created_by": "Ufarmer"}


class TestAccessRules:
    def test_owner(self):
        assert access.plot_access_reason(FARMER, "Ufarmer", PLOT, False) == "owner"

    def test_other_farmer_denied(self):
        assert access.plot_access_reason(FARMER, "Uother", PLOT, True) is None

    def test_officer_needs_owner_consent(self):
        assert access.plot_access_reason(OFFICER, "Uoff", PLOT, owner_shares=False) is None
        assert access.plot_access_reason(OFFICER, "Uoff", PLOT, owner_shares=True) == "officer_area"

    def test_officer_outside_area_denied(self):
        assert access.plot_access_reason(OFFICER, "Uoff", {**PLOT, "area_code": "7001"}, True) is None
        assert access.plot_access_reason(OFFICER, "Uoff", {**PLOT, "area_code": None}, True) is None

    def test_officer_created_plot(self):
        assert access.plot_access_reason(OFFICER, "Uoff", {**PLOT, "created_by": "Uoff", "area_code": None}, False) \
            == "officer_created"

    def test_officer_cannot_do_owner_only(self):
        assert access.plot_access_reason(OFFICER, "Uoff", PLOT, True, access.OWNER_ONLY) is None

    def test_admin(self):
        assert access.plot_access_reason(ADMIN, "Uadmin", PLOT, False) == "admin"

    def test_invite_never_downgrades(self):
        assert access.can_upgrade("farmer", "officer")
        assert not access.can_upgrade("admin", "officer")
        assert not access.can_upgrade("officer", "officer")


class TestAreas:
    def test_ten_ratchaburi_districts(self):
        ds = areas.districts()
        assert len(ds) == 10 and {d["code"] for d in ds} == {f"70{i:02d}" for i in range(1, 11)}

    def test_geo_name_mapping(self):
        assert areas.from_geo_name("Damnoen Saduak") == {"area_code": "7004", "area_name": "อ.ดำเนินสะดวก จ.ราชบุรี"}
        assert areas.from_geo_name("Mueang Nakhon Pathom") == {"area_code": None, "area_name": "Mueang Nakhon Pathom"}
        assert areas.from_geo_name(None)["area_code"] is None

    def test_validate_codes(self):
        assert areas.validate_codes(["7004", "7004", "7001"]) == ["7004", "7001"]
        with pytest.raises(ValueError):
            areas.validate_codes(["9999"])


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
    access.invalidate()


def _as(role_info):
    return patch("main.get_user_access", new_callable=AsyncMock, return_value=role_info)


@pytest.mark.asyncio
class TestPlotAccessApi:
    async def test_officer_views_shared_plot_and_is_audited(self, client):
        with _as(OFFICER), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"Ufarmer": True}), \
             patch("main.get_plot_history", new_callable=AsyncMock, return_value=[]), \
             patch("main.log_audit", new_callable=AsyncMock) as audit:
            resp = await client.get("/plots/Uoff/5/history", headers=auth_as("Uoff"))
        assert resp.status_code == 200
        args = audit.await_args.args
        assert args[0] == "Uoff" and args[1] == "officer" and args[5] == "Ufarmer"

    async def test_officer_without_consent_404(self, client):
        with _as(OFFICER), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"Ufarmer": False}), \
             patch("main.get_plot_history", new_callable=AsyncMock) as hist:
            resp = await client.get("/plots/Uoff/5/history", headers=auth_as("Uoff"))
        assert resp.status_code == 404
        hist.assert_not_called()

    async def test_officer_cannot_merge_farmer_plot(self, client):
        with _as(OFFICER), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"Ufarmer": True}), \
             patch("main.merge_plot_history", new_callable=AsyncMock) as merge:
            resp = await client.post("/plots/Uoff/5/merge", headers=auth_as("Uoff"), json={"source_plot_id": 6})
        assert resp.status_code == 404
        merge.assert_not_called()

    async def test_admin_views_any_plot(self, client):
        with _as(ADMIN), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_persistent_wet_points", new_callable=AsyncMock, return_value=[]), \
             patch("main.log_audit", new_callable=AsyncMock):
            resp = await client.get("/plots/Uadmin/5/persistent-wet", headers=auth_as("Uadmin"))
        assert resp.status_code == 200

    async def test_officer_list_filters_consent(self, client):
        rows = [dict(PLOT, id=1), dict(PLOT, id=2, user_id="Unoshare"), dict(PLOT, id=3, user_id="Uoff",
                                                                              created_by="Uoff", area_code=None)]
        with _as(OFFICER), \
             patch("main.get_plots_for_officer", new_callable=AsyncMock, return_value=rows), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"Ufarmer": True, "Unoshare": False}), \
             patch("main.get_latest_risk_by_plots", new_callable=AsyncMock, return_value={}), \
             patch("main.log_audit", new_callable=AsyncMock):
            resp = await client.get("/officer/plots", headers=auth_as("Uoff"))
        assert resp.status_code == 200
        body = resp.json()
        assert [p["id"] for p in body["plots"]] == [1, 3]
        assert "user_id" not in body["plots"][0]          # ไม่ส่ง LINE userId ของเจ้าของ

    async def test_farmer_cannot_list_officer_plots(self, client):
        with _as(FARMER):
            assert (await client.get("/officer/plots", headers=auth_as("Ufarmer"))).status_code == 403

    async def test_officer_new_plot_records_owner_label(self, client):
        data = {"ndvi_now": .7, "ndvi_prev": .6, "elevation": 5.0}
        with _as(OFFICER), \
             patch("main._run_gee_analysis", new_callable=AsyncMock, return_value=data), \
             patch("main.format_message", return_value="m"), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock, return_value=9) as sp, \
             patch("main._store_boundary_check", new_callable=AsyncMock), \
             patch("main._assign_area", new_callable=AsyncMock) as area, \
             patch("main.log_event", new_callable=AsyncMock), \
             patch("main.save_analysis", new_callable=AsyncMock):
            resp = await client.post("/analyze", headers=auth_as("Uoff"),
                                     json={"lat": 13.5, "lng": 99.9, "owner_label": "ลุงสมชาย"})
        assert resp.status_code == 200
        assert sp.await_args.kwargs["extra"] == {"created_by": "Uoff", "owner_label": "ลุงสมชาย"}
        area.assert_awaited_once()

    async def test_farmer_owner_label_ignored(self, client):
        data = {"ndvi_now": .7, "ndvi_prev": .6, "elevation": 5.0}
        with _as(FARMER), \
             patch("main._run_gee_analysis", new_callable=AsyncMock, return_value=data), \
             patch("main.format_message", return_value="m"), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock, return_value=9) as sp, \
             patch("main._store_boundary_check", new_callable=AsyncMock), \
             patch("main._assign_area", new_callable=AsyncMock), \
             patch("main.log_event", new_callable=AsyncMock), \
             patch("main.save_analysis", new_callable=AsyncMock):
            await client.post("/analyze", headers=auth_as("Ufarmer"), json={"lat": 13.5, "lng": 99.9, "owner_label": "x"})
        assert sp.await_args.kwargs["extra"] == {"created_by": "Ufarmer"}


def _invite(**kw):
    base = {"code": "ABCD2345", "role": "officer", "area_codes": ["7004"], "used_count": 0, "max_uses": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}
    return {**base, **kw}


@pytest.mark.asyncio
class TestInvites:
    async def test_redeem_grants_officer(self, client):
        with _as(FARMER), \
             patch("main.get_invite", new_callable=AsyncMock, return_value=_invite()), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.record_invite_use", new_callable=AsyncMock, return_value=True), \
             patch("main.set_user_role", new_callable=AsyncMock, return_value=True) as setrole, \
             patch("main.log_audit", new_callable=AsyncMock):
            resp = await client.post("/invites/redeem", headers=auth_as("Unew"), json={"code": "abcd2345"})
        assert resp.status_code == 200
        setrole.assert_awaited_once_with("Unew", "officer", ["7004"])
        assert resp.json()["areas"][0]["name_th"] == "ดำเนินสะดวก"

    async def test_admin_keeps_admin(self, client):
        with _as(ADMIN), \
             patch("main.get_invite", new_callable=AsyncMock, return_value=_invite()), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.record_invite_use", new_callable=AsyncMock, return_value=True), \
             patch("main.set_user_role", new_callable=AsyncMock, return_value=True) as setrole, \
             patch("main.log_audit", new_callable=AsyncMock):
            await client.post("/invites/redeem", headers=auth_as("Uadmin"), json={"code": "ABCD2345"})
        assert setrole.await_args.args[1] == "admin"

    @pytest.mark.parametrize("inv", [None, _invite(used_count=1),
                                     _invite(expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())])
    async def test_bad_codes(self, client, inv):
        with _as(FARMER), patch("main.get_invite", new_callable=AsyncMock, return_value=inv), \
             patch("main.set_user_role", new_callable=AsyncMock) as setrole:
            resp = await client.post("/invites/redeem", headers=auth_as("Unew"), json={"code": "ABCD2345"})
        assert resp.status_code == 404
        setrole.assert_not_called()

    async def test_admin_creates_invite(self, client):
        with patch("main.create_invite", new_callable=AsyncMock, return_value=True) as ci, \
             patch("main.log_audit", new_callable=AsyncMock):
            resp = await client.post("/admin/invites", headers=ADMIN_HEADERS, json={"area_codes": ["7004", "7005"]})
        assert resp.status_code == 200
        code = resp.json()["code"]
        assert len(code) == 8 and not set(code) & set("01OI")
        assert resp.json()["link"].endswith(f"?invite={code}")
        assert ci.await_args.args[0]["area_codes"] == ["7004", "7005"]

    async def test_officer_invite_needs_area(self, client):
        resp = await client.post("/admin/invites", headers=ADMIN_HEADERS, json={"area_codes": []})
        assert resp.status_code == 422

    async def test_admin_set_role_validates_area(self, client):
        resp = await client.patch("/admin/users/U1", headers=ADMIN_HEADERS, json={"role": "officer", "areas": ["1234"]})
        assert resp.status_code == 422

    async def test_admin_endpoints_need_key(self, client):
        for method, path in (("get", "/admin/users"), ("post", "/admin/invites"), ("get", "/admin/audit-logs")):
            assert (await getattr(client, method)(path)).status_code == 403


@pytest.mark.asyncio
class TestEventsAndMe:
    async def test_me(self, client):
        with _as(OFFICER):
            body = (await client.get("/me", headers=auth_as("Uoff"))).json()
        assert body["role"] == "officer" and body["role_th"] == "เจ้าหน้าที่"
        assert body["areas"] == [{"code": "7004", "name_th": "ดำเนินสะดวก"}]

    async def test_event_whitelist(self, client):
        with patch("main.log_event", new_callable=AsyncMock) as le:
            ok = await client.post("/events", headers=auth_as("U1"), json={"name": "app_open", "props": {"v": "x" * 99}})
            bad = await client.post("/events", headers=auth_as("U1"), json={"name": "drop_tables"})
        assert ok.status_code == 204 and bad.status_code == 422
        assert len(le.await_args.args[3]["v"]) == 60
