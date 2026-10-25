"""
test_province_2e.py — Phase 2e: แดชบอร์ดจังหวัด (ตัวเลขรวม + ซ่อนตัวเลขเล็ก) + สถิติการใช้งานรายสัปดาห์
"""

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import province

PLOTS = [
    {"id": 1, "user_id": "A", "area_code": "7004", "area_rai": 5, "crop_ids": ["pomelo"], "plot_type": "raised_bed",
     "polygon": [[0, 0], [1, 0], [1, 1], [0, 0]]},
    {"id": 2, "user_id": "B", "area_code": "7004", "area_rai": 3, "crop_ids": ["pomelo", "vegetables"], "plot_type": None},
    {"id": 3, "user_id": "C", "area_code": "7004", "area_rai": 2, "crop_ids": [], "plot_type": "flat"},
    {"id": 4, "user_id": "D", "area_code": "7001", "area_rai": 4, "crop_ids": ["mango"], "plot_type": "flat"},
    {"id": 5, "user_id": "E", "area_code": None, "area_rai": 22000, "crop_ids": [], "plot_type": None},
]


class TestDistrictSummary:
    def test_counts(self):
        s = province.district_summary(PLOTS, {1: "ชื้นกว่าปกติ", 2: "ปกติ"})
        d = {r["code"]: r for r in s["districts"]}
        assert len([r for r in s["districts"] if r["code"]]) == 10
        assert d["7004"]["plots"] == 3 and d["7004"]["farmers"] == 3 and d["7004"]["area_rai"] == 10
        assert d["7004"]["crops"]["ส้มโอ"] == 2 and d["7004"]["plot_types"]["สวนยกร่อง"] == 1
        assert d["7004"]["status"] == {"ชื้นกว่าปกติ": 1, "ปกติ": 1, "ยังไม่มีข้อมูล": 1}
        assert d["7004"]["with_boundary"] == 1 and d["7004"]["lat"] is not None
        assert d[None]["name_th"] == province.OUTSIDE

    def test_implausible_area_not_counted(self):
        s = province.district_summary(PLOTS)
        assert s["totals"]["plots_area_not_counted"] == 1
        outside = next(r for r in s["districts"] if r["code"] is None)
        assert outside["area_rai"] == 0 and outside["plots"] == 1

    def test_suppression(self):
        rows = province.suppressed(province.district_summary(PLOTS)["districts"])
        d = {r["code"]: r for r in rows}
        assert d["7004"]["plots"] == 3 and not d["7004"]["suppressed"]       # 3 คน แสดงได้
        assert d["7001"]["plots"] == "<3" and d["7001"]["crops"] == {}      # 1 คน ซ่อน
        assert d["7002"]["plots"] == 0 and not d["7002"]["suppressed"]       # 0 คน แสดง 0


class TestWeeklyUsage:
    def test_weeks(self):
        today = datetime(2026, 10, 28, 5, tzinfo=timezone.utc)            # พุธ
        events = [{"user_id": "A", "name": "app_open", "created_at": "2026-10-20T03:00:00Z"},
                  {"user_id": "A", "name": "analyze", "created_at": "2026-10-27T03:00:00Z"},
                  {"user_id": "B", "name": "photo_upload", "created_at": "2026-10-27T04:00:00Z"},
                  {"user_id": "B", "name": "photo_upload", "created_at": "2026-10-28T04:00:00Z"}]
        users = [{"user_id": "B", "created_at": "2026-10-26T01:00:00Z"}]
        w = province.weekly_usage(events, users, 2, today)
        assert [x["week_start"] for x in w] == ["2026-10-19", "2026-10-26"]
        cur = w[1]
        assert cur["active_users"] == 2 and cur["returning_users"] == 1     # A ใช้ 2 สัปดาห์
        assert cur["analyses"] == 1 and cur["photos"] == 2 and cur["new_users"] == 1


@pytest_asyncio.fixture
async def client():
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestProvinceApi:
    async def test_needs_key(self, client):
        for path in ("/admin/province", "/admin/province/export.csv", "/admin/usage"):
            assert (await client.get(path)).status_code == 403

    async def test_province_no_personal_data(self, client):
        with patch("main.get_active_plots", new_callable=AsyncMock, return_value=PLOTS), \
             patch("main.get_latest_plot_signals", new_callable=AsyncMock, return_value=None), \
             patch("main.log_audit", new_callable=AsyncMock):
            body = (await client.get("/admin/province", headers=ADMIN_HEADERS)).text
        assert '"A"' not in body and "user_id" not in body and "polygon" not in body

    async def test_export_csv_suppressed(self, client):
        with patch("main.get_active_plots", new_callable=AsyncMock, return_value=PLOTS), \
             patch("main.get_latest_plot_signals", new_callable=AsyncMock, return_value=None), \
             patch("main.log_audit", new_callable=AsyncMock) as audit:
            resp = await client.get("/admin/province/export.csv", headers=ADMIN_HEADERS)
        assert resp.status_code == 200 and resp.text.startswith("﻿")
        assert "เมืองราชบุรี,<3,<3" in resp.text and "ดำเนินสะดวก,3,3" in resp.text
        assert audit.await_args.args[2] == "export_province"

    async def test_usage(self, client):
        with patch("main.get_events_since", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_users_created", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_active_plots", new_callable=AsyncMock, return_value=PLOTS):
            body = (await client.get("/admin/usage?weeks=4", headers=ADMIN_HEADERS)).json()
        assert len(body["weeks"]) == 4 and body["totals"]["active_plots"] == 5
