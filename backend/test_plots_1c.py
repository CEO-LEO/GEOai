"""
test_plots_1c.py — Phase 1c: ชนิดพืชหลายชนิด, ประเภทสวน, คำเตือนขอบเขต, แก้ไข/รวมประวัติแปลง, สวนยกร่อง
"""

import io
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import boundary
import crops

SQUARE = [[99.80, 13.50], [99.801, 13.50], [99.801, 13.501], [99.80, 13.501], [99.80, 13.50]]


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


# ── รายการพืช ────────────────────────────────────────

class TestCrops:
    def test_config_has_default_priority_crops_first(self):
        ids = [c["id"] for c in crops.all_crops()]
        assert ids[:6] == ["coconut_aromatic", "guava", "rose_apple", "grape", "pomelo", "durian"]
        assert "vegetables" in ids

    def test_agronomic_values_left_blank_for_experts(self):
        """ห้ามเดาตัวเลขทางการเกษตร — มีแค่ทุเรียน waterlogging_sensitivity=high จากตัวอย่างใน spec"""
        for c in crops.all_crops():
            assert all(v is None for v in c["thresholds"].values())
            if c["id"] != "durian":
                assert c["waterlogging_sensitivity"] is None

    def test_validate_dedupes_and_keeps_order(self):
        assert crops.validate_ids(["durian", "mango", "durian"]) == ["durian", "mango"]

    def test_validate_rejects_unknown(self):
        with pytest.raises(ValueError):
            crops.validate_ids(["durian", "cannabis"])

    def test_vegetable_context(self):
        assert crops.is_vegetable_context(["durian", "vegetables"], "flat")
        assert crops.is_vegetable_context(["durian"], "vegetable_bed")
        assert not crops.is_vegetable_context(["durian"], "raised_bed")


# ── คำเตือนขอบเขต ────────────────────────────────────

def _codes(ws):
    return [w["code"] for w in ws]


class TestBoundaryWarnings:
    def test_buildings_warned_for_any_crop(self):
        ws = boundary.evaluate({"trees": .7, "built": .23}, is_point=False, area_rai=3,
                               crop_ids=["vegetables"], plot_type="vegetable_bed")
        assert _codes(ws) == ["has_buildings"]
        assert "23%" in ws[0]["title"]

    def test_vegetable_plot_never_gets_low_tree_or_field_crop_warning(self):
        for plot_type in ("vegetable_bed", "flat", None):
            ws = boundary.evaluate({"crops": .9, "trees": .05}, is_point=False, area_rai=2,
                                   crop_ids=["vegetables"], plot_type=plot_type)
            assert ws == []

    def test_mixed_orchard_with_vegetables_no_tree_warning(self):
        ws = boundary.evaluate({"trees": .2, "grass": .8}, is_point=False, area_rai=2,
                               crop_ids=["durian", "vegetables"], plot_type="flat")
        assert ws == []

    def test_fruit_tree_low_cover_warned(self):
        ws = boundary.evaluate({"trees": .45, "grass": .55}, is_point=False, area_rai=2,
                               crop_ids=["durian"], plot_type="flat")
        assert _codes(ws) == ["low_tree_cover"]

    def test_raised_bed_uses_lower_tree_threshold(self):
        lc = {"trees": .45, "water": .3, "grass": .25}
        assert boundary.evaluate(lc, is_point=False, area_rai=2, crop_ids=["pomelo"],
                                 plot_type="raised_bed") == []
        assert _codes(boundary.evaluate({"trees": .3, "water": .7}, is_point=False, area_rai=2,
                                        crop_ids=["pomelo"], plot_type="raised_bed")) == ["low_tree_cover"]

    def test_fruit_tree_field_crops_suggests_newly_planted_is_fine(self):
        ws = boundary.evaluate({"crops": .6, "trees": .3}, is_point=False, area_rai=2,
                               crop_ids=["guava"], plot_type=None)
        assert _codes(ws) == ["looks_like_field_crops"]
        assert "เพิ่งปลูก" in ws[0]["body"]

    def test_no_crop_selected_no_tree_warning(self):
        assert boundary.evaluate({"trees": .1}, is_point=False, area_rai=2,
                                 crop_ids=[], plot_type=None) == []

    def test_point_and_small(self):
        assert _codes(boundary.evaluate(None, is_point=True, area_rai=None,
                                        crop_ids=["durian"], plot_type=None)) == ["point_only"]
        assert "too_small" in _codes(boundary.evaluate({"trees": 1}, is_point=False, area_rai=0.01,
                                                       crop_ids=None, plot_type=None))

    def test_thresholds_come_from_config(self):
        with patch.object(boundary, "rules", return_value={**boundary.rules(), "built_fraction_warn": 0.5}):
            assert boundary.evaluate({"built": .3, "trees": .7}, is_point=False, area_rai=2,
                                     crop_ids=None, plot_type=None) == []

    def test_wording_does_not_blame_farmer(self):
        ws = boundary.evaluate({"trees": .1, "built": .5, "crops": .6}, is_point=True, area_rai=0.01,
                               crop_ids=["durian"], plot_type=None)
        text = " ".join(w["title"] + w["body"] for w in ws) + (boundary.banner(ws) or "")
        for word in ("ผิด", "ไม่ถูกต้อง", "คลาดเคลื่อน"):
            assert word not in text
        assert all(w["secondary"] for w in ws)   # มีปุ่ม "บันทึกตามนี้/ใช้ต่อ" เสมอ ไม่บล็อก

    def test_polygon_area(self):
        assert boundary.polygon_area_rai(SQUARE) == pytest.approx(7.5, rel=0.05)
        assert boundary.polygon_area_rai(None) is None


# ── API ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestPlotApi:
    async def test_crops_endpoint_public(self, client):
        resp = await client.get("/crops")
        assert resp.status_code == 200
        body = resp.json()
        assert any(c["id"] == "durian" for c in body["crops"])
        assert {p["id"] for p in body["plot_types"]} == {"flat", "raised_bed", "vegetable_bed"}

    async def test_boundary_check_returns_warnings(self, client):
        with patch("risk_v2.get_boundary_land_cover",
                   return_value={"status": "ok", "land_cover": {"trees": .7, "built": .2}, "area_rai": 3}):
            resp = await client.post("/plots/boundary-check", headers=auth_as("U1"),
                                     json={"lat": 13.5, "lng": 99.8, "polygon": SQUARE, "crop_ids": ["durian"]})
        assert resp.status_code == 200
        assert [w["code"] for w in resp.json()["warnings"]] == ["has_buildings"]
        assert resp.json()["checked"] is True

    async def test_boundary_check_gee_failure_does_not_block(self, client):
        with patch("risk_v2.get_boundary_land_cover", side_effect=RuntimeError("gee down")):
            resp = await client.post("/plots/boundary-check", headers=auth_as("U1"),
                                     json={"lat": 13.5, "lng": 99.8, "polygon": SQUARE, "crop_ids": ["durian"]})
        assert resp.status_code == 200
        assert resp.json() == {"checked": False, "warnings": [], "area_rai": pytest.approx(7.5, rel=0.05)}

    async def test_boundary_check_rejects_unknown_crop(self, client):
        resp = await client.post("/plots/boundary-check", headers=auth_as("U1"),
                                 json={"lat": 13.5, "lng": 99.8, "crop_ids": ["xyz"]})
        assert resp.status_code == 422

    async def test_patch_other_users_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U2"}), \
             patch("main.update_plot", new_callable=AsyncMock) as up:
            resp = await client.patch("/plots/U1/5", headers=auth_as("U1"), json={"name": "x"})
        assert resp.status_code == 404
        up.assert_not_called()

    async def test_patch_boundary_keeps_history_and_archives_old(self, client):
        old = {"id": 5, "user_id": "U1", "lat": 13.0, "lng": 99.0, "polygon": [[1, 1]], "name": "A",
               "plot_type": None}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=old), \
             patch("main.archive_plot_boundary", new_callable=AsyncMock, return_value=True) as arch, \
             patch("main.update_plot", new_callable=AsyncMock, return_value=True) as up, \
             patch("main.set_plot_crops", new_callable=AsyncMock, return_value=True) as sc, \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=[]), \
             patch("main._store_boundary_check", new_callable=AsyncMock) as store, \
             patch("main.build_plot_grid_image_url", new_callable=AsyncMock) as img:
            resp = await client.patch("/plots/U1/5", headers=auth_as("U1"),
                                      json={"polygon": SQUARE[:-1], "plot_type": "raised_bed",
                                            "crop_ids": ["pomelo", "vegetables"]})
        assert resp.status_code == 200
        arch.assert_awaited_once_with(old)
        fields = up.await_args.args[1]
        assert fields["polygon"][0] == fields["polygon"][-1]          # ปิด ring ให้
        assert fields["plot_type"] == "raised_bed"
        assert fields["lat"] == pytest.approx(13.5005)
        sc.assert_awaited_once_with(5, ["pomelo", "vegetables"])
        store.assert_awaited_once()
        assert img.await_args.kwargs["plot_type"] == "raised_bed"

    async def test_patch_rejects_huge_polygon(self, client):
        huge = [[99.0, 13.0], [99.5, 13.0], [99.5, 13.5], [99.0, 13.0]]
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U1"}), \
             patch("main.archive_plot_boundary", new_callable=AsyncMock) as arch:
            resp = await client.patch("/plots/U1/5", headers=auth_as("U1"), json={"polygon": huge})
        assert resp.status_code == 422
        arch.assert_not_called()

    async def test_merge_requires_both_plots_owned(self, client):
        async def _plot(pid):
            return {"id": pid, "user_id": "U1" if pid == 5 else "U2"}
        with patch("main.get_plot_by_id", side_effect=_plot), \
             patch("main.merge_plot_history", new_callable=AsyncMock) as merge:
            resp = await client.post("/plots/U1/5/merge", headers=auth_as("U1"), json={"source_plot_id": 9})
        assert resp.status_code == 404
        merge.assert_not_called()

    async def test_merge_same_plot_rejected(self, client):
        resp = await client.post("/plots/U1/5/merge", headers=auth_as("U1"), json={"source_plot_id": 5})
        assert resp.status_code == 422

    async def test_merge_ok(self, client):
        async def _plot(pid):
            return {"id": pid, "user_id": "U1"}
        with patch("main.get_plot_by_id", side_effect=_plot), \
             patch("main.merge_plot_history", new_callable=AsyncMock, return_value=True) as merge:
            resp = await client.post("/plots/U1/5/merge", headers=auth_as("U1"), json={"source_plot_id": 9})
        assert resp.status_code == 200
        merge.assert_awaited_once_with("U1", 9, 5)

    async def test_analyze_existing_plot_uses_its_plot_type(self, client):
        data = {"ndvi_now": .7, "ndvi_prev": .6, "elevation": 5.0}
        with patch("main.get_plot_by_id", new_callable=AsyncMock,
                   return_value={"id": 5, "user_id": "U1", "plot_type": "raised_bed"}), \
             patch("main._run_gee_analysis", new_callable=AsyncMock, return_value=data), \
             patch("main.format_message", return_value="m"), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock) as sp, \
             patch("main.save_analysis", new_callable=AsyncMock), \
             patch("main.build_plot_grid_image_url", new_callable=AsyncMock,
                   return_value={"url": None, "problem_points": []}) as img:
            resp = await client.post("/analyze", headers=auth_as("U1"),
                                     json={"lat": 13.5, "lng": 99.8, "plot_id": 5, "polygon": SQUARE})
        assert resp.status_code == 200
        sp.assert_not_called()
        assert img.await_args.kwargs["plot_type"] == "raised_bed"
        assert resp.json()["plot_type"] == "raised_bed"

    async def test_analyze_new_plot_saves_crops_and_type(self, client):
        data = {"ndvi_now": .7, "ndvi_prev": .6, "elevation": 5.0}
        with patch("main._run_gee_analysis", new_callable=AsyncMock, return_value=data), \
             patch("main.format_message", return_value="m"), \
             patch("main.upsert_user", new_callable=AsyncMock), \
             patch("main.save_plot", new_callable=AsyncMock, return_value=11) as sp, \
             patch("main.set_plot_crops", new_callable=AsyncMock, return_value=True) as sc, \
             patch("main._store_boundary_check", new_callable=AsyncMock) as store, \
             patch("main.save_analysis", new_callable=AsyncMock):
            resp = await client.post("/analyze", headers=auth_as("U1"),
                                     json={"lat": 13.5, "lng": 99.8, "plot_type": "vegetable_bed",
                                           "crop_ids": ["vegetables"]})
        assert resp.status_code == 200
        assert sp.await_args.kwargs["plot_type"] == "vegetable_bed"
        sc.assert_awaited_once_with(11, ["vegetables"])
        store.assert_awaited_once()

    async def test_config_js_hides_agronomy_numbers_by_default(self, client):
        js = (await client.get("/liff/config.js")).text
        assert 'AGRONOMY_NUMBERS: "off"' in js


# ── สวนยกร่อง + รูปแผนที่ ──────────────────────────────

def _png(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (60, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _grid():
    pts = []
    for i in range(4):
        for j in range(4):
            pts.append({"lat": 13.5 + i * 0.0001, "lng": 99.8 + j * 0.0001,
                        "swab_index": 0.3 if i == 0 else 0.0,
                        "status": "waterlogged" if i == 0 else "optimal",
                        "flow_to": {"lat": 13.5, "lng": 99.8} if i else None})
    return pts


class TestRaisedBed:
    def test_tiny_satellite_image_renders(self):
        """แปลง 67/68: ภาพกว้างไม่กี่พิกเซล เคยพัง 'x1 must be greater than or equal to x0'"""
        import map_image
        out = map_image.render_plot_grid_image(_png(12, 9), (99.8, 13.5, 99.8004, 13.5003), _grid(),
                                               SQUARE, 10, "แปลง")
        assert out[:4] == b"\x89PNG"

    def test_raised_bed_render_and_headline(self):
        import map_image
        out = map_image.render_plot_grid_image(_png(400, 300), (99.8, 13.5, 99.8004, 13.5003), _grid(),
                                               SQUARE, 10, "แปลง", None, False)
        assert out[:4] == b"\x89PNG"
        headline, _ = map_image.summarize_grid_status(_grid(), ignore_wet=True)
        assert "น้ำขัง" not in headline.replace("ไม่ประเมินน้ำขัง", "")
        assert "น้ำขัง" in map_image.summarize_grid_status(_grid())[0]

    def test_problem_points_exclude_wet_for_raised_bed(self):
        import gee_analysis
        pts = _grid()
        for k, p in enumerate(pts):
            p["grid_label"] = f"A{k}"
            p["position_desc"] = "กลางแปลง"
        pts[-1].update(status="dry", swab_index=-0.2)
        chosen = gee_analysis.select_problem_points(pts, exclude_statuses=("wet", "waterlogged"))
        assert chosen and all(p["status"] not in ("wet", "waterlogged") for p in chosen)

    @pytest.mark.asyncio
    async def test_rain_job_skips_soil_waterlog_for_raised_bed(self):
        import scheduler
        plot = {"id": 3, "lat": 13.5, "lng": 99.8, "name": "ยกร่อง", "polygon": None, "plot_type": "raised_bed"}
        forecast = {"is_heavy_rain": False, "total_rain_mm": 5}
        with patch("scheduler.get_notifiable_users", new_callable=AsyncMock, return_value={"U1"}), \
             patch("scheduler.get_all_user_ids", new_callable=AsyncMock, return_value=["U1"]), \
             patch("scheduler.get_user_plots", new_callable=AsyncMock, return_value=[plot]), \
             patch("scheduler.get_7day_rain", new_callable=AsyncMock, return_value=forecast), \
             patch("scheduler.get_latest_plot_analysis", new_callable=AsyncMock, return_value={"x": 1}), \
             patch("scheduler.assess_soil_waterlog_risk") as soil:
            await scheduler.rain_alert_job()
        soil.assert_not_called()


class TestAgronomyHidden:
    def test_flex_hides_yield_by_default(self, monkeypatch):
        import json
        from flex_messages import build_result_flex
        monkeypatch.delenv("SHOW_UNVERIFIED_AGRONOMY", raising=False)
        data = {"ndvi_now": .7, "ndvi_change": .01, "soil_moisture_vv": -12, "elevation_diff": 0.2,
                "yield_estimate": {"estimated_kg_per_rai": 1500, "quality": "high"}}
        assert "ผลผลิตประเมิน" not in json.dumps(build_result_flex(data, 13.5, 99.8, "x"), ensure_ascii=False)
        monkeypatch.setenv("SHOW_UNVERIFIED_AGRONOMY", "true")
        assert "ผลผลิตประเมิน" in json.dumps(build_result_flex(data, 13.5, 99.8, "x"), ensure_ascii=False)
