"""
test_summary_1e.py — Phase 1e: ประโยคสรุปภาษาคน + คำอธิบายสี
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import plot_summary

SQUARE = [[99.800, 13.500], [99.801, 13.500], [99.801, 13.501], [99.800, 13.501], [99.800, 13.500]]
STEP = 0.0001   # ~11 ม.


def _grid(value_fn, flow=None):
    pts = []
    for i in range(10):
        for j in range(10):
            p = {"lat": 13.5 + i * STEP + STEP / 2, "lng": 99.8 + j * STEP + STEP / 2,
                 "swab_index": value_fn(i, j)}
            if flow:
                p["flow_to"] = flow(i, j, p)
            pts.append(p)
    return pts


class TestSummary:
    def test_uniform_plot(self):
        s = plot_summary.summarize(_grid(lambda i, j: 0.05), SQUARE)
        assert "ใกล้เคียงกัน" in s["summary_th"]
        assert s["color_note_th"] == plot_summary.COLOR_NOTE_TH

    def test_wet_north_dry_south_with_direction(self):
        # i = แถวละติจูด (มาก = เหนือ) — เหนือชื้น ใต้แห้ง
        s = plot_summary.summarize(_grid(lambda i, j: 0.3 if i >= 7 else (-0.3 if i <= 2 else 0.0)), SQUARE)
        text = "\n".join(s["lines"])
        assert "ชื้นกว่าส่วนอื่น" in text and "ฝั่งเหนือของแปลง" in text
        assert "แห้งกว่าส่วนอื่น" in text and "ฝั่งใต้ของแปลง" in text
        assert s["counts"]["wet"] == 30 and s["counts"]["dry"] == 30

    def test_raised_bed_hides_wet_and_sinks(self):
        pts = _grid(lambda i, j: 0.3 if i >= 7 else 0.0)
        s = plot_summary.summarize(pts, SQUARE, plot_type="raised_bed")
        text = s["summary_th"] + "\n".join(s["lines"])
        assert "ชื้นกว่าส่วนอื่น" not in text
        assert plot_summary.RAISED_BED_SUMMARY_TH in s["lines"]

    def test_sink_detection_matches_map(self):
        """ทุกจุดไหลเข้าหาจุดกลาง → มีจุดน้ำรวม และนับตรงกับ map_image._compute_flow_graph"""
        import map_image
        center = (13.5 + 5 * STEP + STEP / 2, 99.8 + 5 * STEP + STEP / 2)

        def flow(i, j, p):
            if (i, j) == (5, 5):
                return None
            return {"lat": p["lat"] + (center[0] - p["lat"]) * 0.5, "lng": p["lng"] + (center[1] - p["lng"]) * 0.5}
        pts = _grid(lambda i, j: 0.0, flow)
        mine = {id(p) for p in plot_summary.sink_points(pts, 10)}
        ref = {k for k, v in map_image._compute_flow_graph(pts, 10).items() if v >= 3}
        assert mine == ref and mine
        assert "น้ำจากรอบข้างไหลมารวม" in "\n".join(plot_summary.summarize(pts, SQUARE)["lines"])

    def test_no_points(self):
        assert plot_summary.summarize([], SQUARE)["lines"] == []

    def test_tiny_plot(self):
        s = plot_summary.summarize(_grid(lambda i, j: 0.0)[:1], SQUARE)
        assert "เล็กเกิน" in s["summary_th"]

    def test_few_dry_outliers_do_not_make_plot_wet(self):
        """แปลงจริง (25): จุดแห้งไม่กี่จุด ห้ามทำให้ส่วนใหญ่ของแปลงกลายเป็น "ชื้นกว่าส่วนอื่น" """
        s = plot_summary.summarize(_grid(lambda i, j: -0.3 if (i, j) in ((0, 0), (0, 1), (1, 0)) else 0.2), SQUARE)
        assert s["counts"]["wet"] == 0 and s["counts"]["dry"] == 3

    def test_no_numbers_claiming_accuracy(self):
        s = plot_summary.summarize(_grid(lambda i, j: 0.3 if i >= 7 else 0.0), SQUARE)
        assert "%" not in plot_summary.short_text(s)


class TestImageHeadline:
    def test_headline_from_counts(self):
        s = plot_summary.summarize(_grid(lambda i, j: -0.3 if i <= 1 else 0.0), SQUARE)
        assert plot_summary.image_headline(s) == "แห้งกว่าส่วนอื่น 20 จุด"
        assert plot_summary.image_headline(plot_summary.summarize(_grid(lambda i, j: 0.0), SQUARE)) \
            == "ความชื้นใกล้เคียงกันทั้งแปลง"

    def test_render_accepts_headline(self):
        import io
        from PIL import Image
        import map_image
        buf = io.BytesIO()
        Image.new("RGB", (480, 360)).save(buf, format="PNG")
        out = map_image.render_plot_grid_image(buf.getvalue(), (99.8, 13.5, 99.801, 13.501), _grid(lambda i, j: 0.0),
                                               SQUARE, 10, "A", None, True, "แห้งกว่าส่วนอื่น 3 จุด")
        assert out[:4] == b"\x89PNG"


class TestFlexSummary:
    def test_card_shows_summary_and_color_note(self):
        from flex_messages import build_result_flex
        data = {"ndvi_now": .7, "ndvi_change": .01, "soil_moisture_vv": -12, "elevation_diff": 0.2}
        out = json.dumps(build_result_flex(data, 13.5, 99.8, "x", summary_th="สรุป\n🔵 จุดชื้น 3 จุด"),
                         ensure_ascii=False)
        assert "📝 สรุปแปลงนี้" in out and "🔵 จุดชื้น 3 จุด" in out
        assert "เทียบกันเองภายในแปลงนี้" in out
        assert "📝 สรุปแปลงนี้" not in json.dumps(build_result_flex(data, 13.5, 99.8, "x"), ensure_ascii=False)


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
async def test_grid_endpoint_returns_summary(client):
    pts = _grid(lambda i, j: 0.3 if i >= 7 else 0.0)
    with patch("gee_analysis.get_moisture_grid", return_value=(pts, 10)):
        resp = await client.post("/analyze/grid", headers=auth_as("U1"),
                                 json={"polygon": SQUARE, "plot_type": "flat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary_th"] and body["summary_lines"]
    assert body["color_note_th"] == plot_summary.COLOR_NOTE_TH
