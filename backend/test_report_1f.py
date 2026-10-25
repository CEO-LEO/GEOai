"""
test_report_1f.py — Phase 1f: รายงานแปลง (PDF) + การ์ดแชร์ใน LINE
"""

import io
import json
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

import report_service as rs

REC = {"problem": "dry_spot", "severity": "medium", "title": "จุดนี้แห้งกว่าส่วนอื่นของแปลง",
       "reason": "", "actions": ["เพิ่มการให้น้ำตรงจุดนี้"], "status": "draft", "status_th": "ร่าง — รอเจ้าหน้าที่เกษตรตรวจ"}


class TestWrap:
    def test_wraps_thai_without_splitting_marks(self):
        text = "ตรวจร่องระบายน้ำรอบแปลงให้ไม่อุดตันและลดการให้น้ำลงช่วงนี้" * 3
        lines = rs.wrap_text(text, len, 20)
        assert "".join(lines) == text
        assert all(len(l) <= 22 for l in lines)
        assert not any(l and l[0] in rs._COMBINING for l in lines)   # ห้ามขึ้นบรรทัดด้วยสระบน/วรรณยุกต์

    def test_prefers_spaces(self):
        assert rs.wrap_text("aaaa bbbb cccc", len, 10) == ["aaaa bbbb", "cccc"]

    def test_keeps_newlines(self):
        assert rs.wrap_text("a\nb", len, 10) == ["a", "b"]


class TestPdf:
    def _map(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (480, 600), (40, 90, 50)).save(buf, format="PNG")
        return buf.getvalue()

    def test_renders_pdf(self):
        pdf = rs.render_report_pdf("แปลงที่ 1", "25 ต.ค. 2569", "มะม่วง · แปลงเรียบ", self._map(),
                                   ["บางส่วนชื้นหรือแห้ง", "🔴 จุดที่แห้งกว่าส่วนอื่น 3 จุด"], [REC], "แห้งกว่าปกติ")
        assert pdf[:5] == b"%PDF-"

    def test_long_report_multiple_pages_and_point_plot(self):
        recs = [dict(REC, actions=["ข้อความยาว " * 30] * 5)] * 6
        pdf = rs.render_report_pdf("จุดเดียว", "25 ต.ค. 2569", "", None, [], recs, None)
        assert pdf[:5] == b"%PDF-" and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") + pdf.count(b"/Type/Page") >= 1

    def test_strip_emoji(self):
        assert rs.strip_emoji("🔴 จุดแห้ง  🟣 น้ำรวม") == "จุดแห้ง น้ำรวม"


class TestShareFlex:
    def test_no_coordinates_and_has_open_button(self):
        flex = rs.build_share_flex("สวนลุง", "https://x/img.png", ["สรุป", "🔵 จุดชื้น 3 จุด"], "https://liff.line.me/abc")
        dumped = json.dumps(flex, ensure_ascii=False)
        assert flex["type"] == "flex" and flex["contents"]["hero"]["url"] == "https://x/img.png"
        assert "https://liff.line.me/abc" in dumped and "13." not in dumped and "lat" not in dumped
        assert "เทียบกันเองภายในแปลงนี้" in dumped

    def test_without_image(self):
        assert "hero" not in rs.build_share_flex("สวน", None, [], "https://liff.line.me/abc")["contents"]


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
class TestReportApi:
    async def test_requires_token(self, client):
        assert (await client.post("/report/plot/U1/5")).status_code == 401

    async def test_other_users_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U2"}):
            resp = await client.post("/report/plot/U1/5", headers=auth_as("U1"))
        assert resp.status_code == 404

    async def test_success(self, client):
        out = {"pdf_url": "https://x/r.pdf", "image_url": None, "summary_lines": [], "share": {"type": "flex"}}
        with patch("main.get_plot_by_id", new_callable=AsyncMock,
                   return_value={"id": 5, "user_id": "U1", "name": "A", "plot_type": "flat"}), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=["mango"]), \
             patch("main.get_latest_plot_signals", new_callable=AsyncMock, return_value=None), \
             patch("database.list_recommendation_rules", new_callable=AsyncMock, return_value=None), \
             patch("report_service.build_plot_report", new_callable=AsyncMock, return_value=out) as build:
            resp = await client.post("/report/plot/U1/5", headers=auth_as("U1"))
        assert resp.status_code == 200 and resp.json()["pdf_url"] == "https://x/r.pdf"
        assert build.await_args.args[1] == "มะม่วง · แปลงเรียบ"

    async def test_all_parts_failed_is_502(self, client):
        out = {"pdf_url": None, "image_url": None, "summary_lines": [], "share": {}}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U1"}), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_latest_plot_signals", new_callable=AsyncMock, return_value=None), \
             patch("database.list_recommendation_rules", new_callable=AsyncMock, return_value=None), \
             patch("report_service.build_plot_report", new_callable=AsyncMock, return_value=out):
            resp = await client.post("/report/plot/U1/5", headers=auth_as("U1"))
        assert resp.status_code == 502
