"""
test_validation_2d.py — Phase 2d: ยืนยันภาคสนาม จริง / ไม่จริง / ไม่แน่ใจ
"""

import sys
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
import validation


def _rows(problem, answers, role="farmer"):
    return [{"problem": problem, "answer": a, "role": role, "plot_id": i % 3} for i, a in enumerate(answers)]


class TestSummary:
    def test_small_sample_no_rate(self):
        s = validation.summarize(_rows("wet_spot", ["true", "true", "false", "unsure"]))
        b = s["by_problem"][0]
        assert (b["true"], b["false"], b["unsure"], b["decided"]) == (2, 1, 1, 3)
        assert b["match_rate"] is None and "ตัวอย่างยังน้อย" in b["text_th"]
        assert s["total"] == 4 and s["plots"] == 3

    def test_rate_when_enough(self):
        b = validation.summarize(_rows("dry_spot", ["true"] * 8 + ["false"] * 2, role="officer"))["by_problem"][0]
        assert b["match_rate"] == 0.8 and b["officer_true"] == 8
        assert b["text_th"] == "ตรงกับในแปลง 8 ใน 10 ครั้งที่ตอบชัด"


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


PLOT = {"id": 5, "user_id": "U1", "area_code": "7004", "created_by": "U1"}


@pytest.mark.asyncio
class TestValidationApi:
    async def test_point_validation(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 1}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"), json={
                "scope": "point", "problem": "wet_spot", "severity": "medium", "answer": "true",
                "lat": 13.5, "lng": 99.9, "context": {"rel": 0.31, "big": "x" * 500, "nested": {"a": 1}}})
        assert resp.status_code == 200 and resp.json()["answer_th"] == "จริง"
        row = ins.await_args.args[0]
        assert row["role"] == "farmer" and row["scope"] == "point" and row["lat"] == 13.5
        assert len(row["context"]["big"]) == 60 and isinstance(row["context"]["nested"], str)

    async def test_point_needs_coordinates(self, client):
        resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                 json={"scope": "point", "problem": "wet_spot", "answer": "true"})
        assert resp.status_code == 422

    async def test_unknown_problem(self, client):
        resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                 json={"scope": "plot", "problem": "ufo", "answer": "true"})
        assert resp.status_code == 422

    async def test_other_users_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={**PLOT, "user_id": "U2", "created_by": "U2"}), \
             patch("main.insert_validation", new_callable=AsyncMock) as ins:
            resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                     json={"scope": "plot", "problem": "wetter_than_usual", "answer": "false"})
        assert resp.status_code == 404
        ins.assert_not_called()

    async def test_officer_records_role(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock,
                   return_value={"role": "officer", "areas": ["7004"], "share_with_officers": False}), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"U1": True}), \
             patch("main.log_audit", new_callable=AsyncMock), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 2}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/Uoff/5/validations", headers=auth_as("Uoff"),
                                     json={"scope": "plot", "problem": "wetter_than_usual", "answer": "unsure"})
        assert resp.status_code == 200 and ins.await_args.args[0]["role"] == "officer"

    async def test_list(self, client):
        rows = [{"plot_id": 5, "user_id": "U1", "role": "farmer", "scope": "plot", "problem": "dry_spot",
                 "answer": "true", "created_at": "2026-10-25T00:00:00Z"}]
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.list_validations", new_callable=AsyncMock, return_value=rows):
            body = (await client.get("/plots/U1/5/validations", headers=auth_as("U1"))).json()
        assert body["summary"]["total"] == 1 and body["items"][0]["by_me"] is True
        assert "user_id" not in body["items"][0]

    async def test_admin_summary_and_export(self, client):
        rows = [{"id": 1, "plot_id": 5, "user_id": "U1", "role": "officer", "scope": "point", "problem": "wet_spot",
                 "answer": "false", "lat": 13.5, "lng": 99.9, "created_at": "2026-10-25T00:00:00Z"}]
        with patch("main.list_validations", new_callable=AsyncMock, return_value=rows), \
             patch("main.log_audit", new_callable=AsyncMock):
            s = (await client.get("/admin/validations", headers=ADMIN_HEADERS)).json()
            csv_resp = await client.get("/admin/validations/export.csv", headers=ADMIN_HEADERS)
        assert s["by_problem"][0]["officer_false"] == 1
        assert csv_resp.status_code == 200 and "U1" not in csv_resp.text and "13.5" in csv_resp.text

    async def test_admin_needs_key(self, client):
        assert (await client.get("/admin/validations")).status_code == 403
