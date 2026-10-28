"""
test_trees_6_3.py — spec 6.3 (ตัวเลือก): ปักหมุดต้นไม้รายต้น + บันทึกต่อต้น
"""

import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import trees

# สี่เหลี่ยม ~110 × 110 ม. (polygon = [[lng, lat], ...])
SQUARE = [[99.800, 13.500], [99.801, 13.500], [99.801, 13.501], [99.800, 13.501]]
TODAY = date(2026, 10, 28)


class TestGeometry:
    def test_inside_outside_and_tolerance(self):
        plot = {"lat": 13.5005, "lng": 99.8005, "polygon": SQUARE}
        assert trees.within_plot(plot, 13.5005, 99.8005)
        assert trees.within_plot(plot, 13.5005, 99.8011)          # ~11 ม. นอกขอบ = GPS คลาด ยอม
        assert not trees.within_plot(plot, 13.5005, 99.8030)      # ~200 ม. นอกแปลง

    def test_point_plot_radius(self):
        plot = {"lat": 13.5, "lng": 99.8, "polygon": None}
        assert trees.within_plot(plot, 13.5009, 99.8)             # ~100 ม.
        assert not trees.within_plot(plot, 13.502, 99.8)          # ~220 ม.

    def test_distance_to_polygon(self):
        assert trees.distance_to_polygon_m(13.5005, 99.8020, SQUARE) == pytest.approx(108, abs=3)


class TestLogic:
    def test_next_label(self):
        assert trees.next_label([]) == "ต้นที่ 1"
        assert trees.next_label(["ต้นที่ 2", "ต้นมะม่วงหน้าบ้าน", "ต้นที่ 10"]) == "ต้นที่ 11"

    def test_validate_log(self):
        assert trees.validate_log("symptom", ["yellow_leaves"], TODAY, TODAY) == ["yellow_leaves"]
        with pytest.raises(ValueError, match="อย่างน้อย 1"):
            trees.validate_log("symptom", [], TODAY, TODAY)
        with pytest.raises(ValueError, match="แท็กอาการ"):
            trees.validate_log("fertilize", ["pest"], TODAY, TODAY)
        with pytest.raises(ValueError, match="อนาคต"):
            trees.validate_log("note", [], date(2026, 11, 5), TODAY)
        with pytest.raises(ValueError, match="5 ปี"):
            trees.validate_log("spray", [], date(2020, 1, 1), TODAY)
        with pytest.raises(ValueError):
            trees.validate_log("symptom", ["xyz"], TODAY, TODAY)

    def test_tree_list_latest_and_natural_order(self):
        pins = [{"id": 1, "label": "ต้นที่ 10", "lat": 0, "lng": 0}, {"id": 2, "label": "ต้นที่ 2", "lat": 0, "lng": 0}]
        logs = [{"tree_id": 1, "kind": "spray", "happened_on": "2026-10-01", "created_at": "a", "tags": []},
                {"tree_id": 1, "kind": "symptom", "happened_on": "2026-10-20", "created_at": "a", "tags": ["pest"]}]
        out = trees.tree_list(pins, logs, [1, 1, None, 2])
        assert [t["label"] for t in out] == ["ต้นที่ 2", "ต้นที่ 10"]
        assert out[1]["logs"] == 2 and out[1]["photos"] == 2 and out[1]["last"]["kind_th"] == "อาการ"
        assert out[1]["last"]["tags_th"] == ["แมลง/โรคพืช"] and "created_by" not in out[0]


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


PLOT = {"id": 7, "user_id": "UT1", "lat": 13.5005, "lng": 99.8005, "polygon": SQUARE}
TREE = {"id": 3, "client_id": "tree-abc-1", "plot_id": 7, "label": "ต้นที่ 1", "lat": 13.5005, "lng": 99.8005,
        "accuracy_m": 6, "crop_id": "mango", "created_by": "UT1", "created_role": "farmer",
        "created_at": "2026-10-28T03:00:00Z", "removed_at": None}
PIN = {"client_id": "tree-new-01", "lat": 13.5005, "lng": 99.8005, "accuracy_m": 5, "crop_id": "mango"}


@pytest.mark.asyncio
class TestTreeApi:
    async def test_pin_inside_plot_auto_label(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree_by_client_id", new_callable=AsyncMock, return_value=None), \
             patch("main.list_trees", new_callable=AsyncMock, return_value=[{"label": "ต้นที่ 4"}]), \
             patch("main.insert_tree", new_callable=AsyncMock, side_effect=lambda r: {**r, "id": 9}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            r = await client.post("/plots/UT1/7/trees", headers=auth_as("UT1"), json=PIN)
        assert r.status_code == 200 and r.json()["label"] == "ต้นที่ 5" and "created_by" not in r.json()
        assert ins.await_args.args[0]["created_by"] == "UT1" and ins.await_args.args[0]["crop_id"] == "mango"

    async def test_pin_outside_plot_rejected(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree_by_client_id", new_callable=AsyncMock, return_value=None):
            r = await client.post("/plots/UT1/7/trees", headers=auth_as("UT1"), json={**PIN, "lng": 99.803})
        assert r.status_code == 422 and "นอกแปลง" in r.json()["detail"]

    async def test_pin_resend_is_idempotent(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree_by_client_id", new_callable=AsyncMock, return_value=TREE), \
             patch("main.insert_tree", new_callable=AsyncMock) as ins:
            r = await client.post("/plots/UT1/7/trees", headers=auth_as("UT1"), json={**PIN, "client_id": TREE["client_id"]})
        assert r.status_code == 200 and r.json()["duplicate"] is True and ins.await_count == 0

    async def test_log_validation_and_save(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree", new_callable=AsyncMock, return_value=TREE), \
             patch("main.get_tree_log_by_client_id", new_callable=AsyncMock, return_value=None), \
             patch("main.insert_tree_log", new_callable=AsyncMock,
                   side_effect=lambda r: {**r, "id": 5, "created_at": "2026-10-28T04:00:00Z"}), \
             patch("main.log_event", new_callable=AsyncMock):
            bad = await client.post("/plots/UT1/7/trees/3/logs", headers=auth_as("UT1"),
                                    json={"client_id": "log-0000001", "kind": "symptom", "happened_on": "2026-10-27"})
            ok = await client.post("/plots/UT1/7/trees/3/logs", headers=auth_as("UT1"),
                                   json={"client_id": "log-0000002", "kind": "fertilize", "happened_on": "2026-10-27",
                                         "product": "15-15-15 ครึ่งกิโล"})
        assert bad.status_code == 422
        assert ok.status_code == 200 and ok.json()["kind_th"] == "ใส่ปุ๋ย" and ok.json()["can_delete"] is True

    async def test_tree_from_other_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree", new_callable=AsyncMock, return_value={**TREE, "plot_id": 99}):
            r = await client.get("/plots/UT1/7/trees/3", headers=auth_as("UT1"))
        assert r.status_code == 404

    async def test_detail_hides_creator_id(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_tree", new_callable=AsyncMock, return_value={**TREE, "created_by": "UOFFICER"}), \
             patch("main.list_tree_logs", new_callable=AsyncMock, return_value=[]), \
             patch("main.list_photos", new_callable=AsyncMock, return_value=[]), \
             patch("main.photo_service.signed_urls", new_callable=AsyncMock, return_value={}):
            j = (await client.get("/plots/UT1/7/trees/3", headers=auth_as("UT1"))).json()
        assert "created_by" not in j["tree"] and j["tree"]["by_me"] is False
        assert "UOFFICER" not in str(j)

    async def test_remove_only_owner_or_creator(self, client):
        officer = {"role": "officer", "areas": ["7004"]}
        plot = {**PLOT, "user_id": "UFARM", "area_code": "7004"}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=plot), \
             patch("main.get_user_access", new_callable=AsyncMock, return_value=officer), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"UFARM": True}), \
             patch("main.log_audit", new_callable=AsyncMock), \
             patch("main.get_tree", new_callable=AsyncMock, return_value={**TREE, "created_by": "UFARM"}), \
             patch("main.update_tree", new_callable=AsyncMock, return_value=True) as up:
            r = await client.delete("/plots/UOFF9/7/trees/3", headers=auth_as("UOFF9"))
        assert r.status_code == 403 and up.await_count == 0

    async def test_photo_with_tree_of_other_plot_rejected(self, client):
        import base64, io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (30, 120, 40)).save(buf, "JPEG")
        img = base64.b64encode(buf.getvalue()).decode()
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=None), \
             patch("main.get_tree", new_callable=AsyncMock, return_value={**TREE, "plot_id": 99}), \
             patch("main.photo_service.upload", new_callable=AsyncMock, return_value=True) as up:
            r = await client.post("/plots/UT1/7/photos", headers=auth_as("UT1"),
                                  json={"client_id": "photo-tree-1", "image_base64": img, "tree_id": 3})
        assert r.status_code == 422 and up.await_count == 0
