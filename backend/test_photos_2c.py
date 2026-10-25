"""
test_photos_2c.py — Phase 2c: รูปถ่ายภาคสนาม (ลบ EXIF ฝั่ง server, สิทธิ์, ส่งซ้ำไม่สร้างซ้ำ)
"""

import base64
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

import access
import photo_service


def _jpeg_with_gps(w=2400, h=1200) -> bytes:
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational as R
    img = Image.new("RGB", (w, h), (90, 140, 60))
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (R(13), R(31), R(48)), 3: "E", 4: (R(99), R(57), R(18))}
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=ex.tobytes())
    return buf.getvalue()


class TestCleanImage:
    def test_strips_exif_and_resizes(self):
        from PIL import Image
        raw = _jpeg_with_gps()
        assert Image.open(io.BytesIO(raw)).getexif().get(0x8825) is not None
        data, w, h = photo_service.clean_image(raw)
        out = Image.open(io.BytesIO(data))
        assert out.format == "JPEG" and max(w, h) == photo_service.MAX_SIDE
        assert len(out.getexif()) == 0                      # ไม่มีพิกัดฝังในไฟล์ที่เก็บ

    def test_rejects_non_image(self):
        with pytest.raises(ValueError):
            photo_service.clean_image(b"%PDF-1.4" + b"0" * 4000)

    def test_rejects_tiny_and_huge(self):
        with pytest.raises(ValueError):
            photo_service.clean_image(b"12")
        with pytest.raises(ValueError):
            photo_service.clean_image(b"0" * (photo_service.MAX_UPLOAD_BYTES + 1))

    def test_tags(self):
        assert photo_service.validate_tags(["waterlogged", "waterlogged", "normal"]) == ["waterlogged", "normal"]
        with pytest.raises(ValueError):
            photo_service.validate_tags(["selfie"])


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
B64 = base64.b64encode(_jpeg_with_gps(800, 600)).decode()


def _body(**kw):
    return {"client_id": "abcdef123456", "image_base64": B64, "lat": 13.53, "lng": 99.955,
            "gps_source": "exif", "tags": ["waterlogged"], **kw}


@pytest.mark.asyncio
class TestPhotoApi:
    async def test_upload(self, client):
        saved = {"id": 1, "client_id": "abcdef123456", "plot_id": 5, "uploaded_by": "U1", "uploader_role": "farmer",
                 "storage_path": "5/abcdef123456.jpg", "tags": ["waterlogged"], "lat": 13.53, "lng": 99.955}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=None), \
             patch("photo_service.upload", new_callable=AsyncMock, return_value=True) as up, \
             patch("photo_service.signed_urls", new_callable=AsyncMock, return_value={"5/abcdef123456.jpg": "https://s/x"}), \
             patch("main.insert_photo", new_callable=AsyncMock, return_value=saved) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"] == "https://s/x" and body["tags_th"] == ["น้ำขัง"] and body["can_delete"]
        stored = up.await_args.args[1]
        from PIL import Image
        assert len(Image.open(io.BytesIO(stored)).getexif()) == 0
        row = ins.await_args.args[0]
        assert row["gps_source"] == "exif" and row["uploaded_by"] == "U1" and row["storage_path"] == "5/abcdef123456.jpg"

    async def test_resend_same_client_id_is_idempotent(self, client):
        existing = {"id": 1, "client_id": "abcdef123456", "plot_id": 5, "uploaded_by": "U1", "storage_path": "5/a.jpg",
                    "tags": []}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=existing), \
             patch("photo_service.upload", new_callable=AsyncMock) as up, \
             patch("photo_service.signed_urls", new_callable=AsyncMock, return_value={}):
            resp = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body())
        assert resp.status_code == 200 and resp.json()["duplicate"] is True
        up.assert_not_called()

    async def test_other_users_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={**PLOT, "user_id": "U2", "created_by": "U2"}), \
             patch("photo_service.upload", new_callable=AsyncMock) as up:
            resp = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body())
        assert resp.status_code == 404
        up.assert_not_called()

    async def test_bad_image_422(self, client):
        bad = base64.b64encode(b"not an image" * 200).decode()
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=None):
            resp = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body(image_base64=bad))
        assert resp.status_code == 422

    async def test_bad_tag_and_client_id(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=None):
            r1 = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body(tags=["selfie"]))
        r2 = await client.post("/plots/U1/5/photos", headers=auth_as("U1"), json=_body(client_id="../../etc"))
        assert r1.status_code == 422 and r2.status_code == 422

    async def test_officer_can_upload_to_shared_plot(self, client):
        saved = {"id": 2, "client_id": "abcdef123456", "plot_id": 5, "uploaded_by": "Uoff", "uploader_role": "officer",
                 "storage_path": "5/abcdef123456.jpg", "tags": []}
        with patch("main.get_user_access", new_callable=AsyncMock,
                   return_value={"role": "officer", "areas": ["7004"], "share_with_officers": False}), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"U1": True}), \
             patch("main.log_audit", new_callable=AsyncMock), \
             patch("main.get_photo_by_client_id", new_callable=AsyncMock, return_value=None), \
             patch("photo_service.upload", new_callable=AsyncMock, return_value=True), \
             patch("photo_service.signed_urls", new_callable=AsyncMock, return_value={}), \
             patch("main.insert_photo", new_callable=AsyncMock, return_value=saved) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/Uoff/5/photos", headers=auth_as("Uoff"), json=_body())
        assert resp.status_code == 200
        assert ins.await_args.args[0]["uploader_role"] == "officer"
        assert resp.json()["can_delete"] is True        # คนอัปโหลดลบรูปตัวเองได้

    async def test_delete_only_uploader_or_owner(self, client):
        photo = {"id": 9, "plot_id": 5, "uploaded_by": "Uoff", "storage_path": "5/x.jpg"}
        with patch("main.get_user_access", new_callable=AsyncMock,
                   return_value={"role": "admin", "areas": [], "share_with_officers": False}), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.log_audit", new_callable=AsyncMock), \
             patch("main.get_photo", new_callable=AsyncMock, return_value=photo), \
             patch("photo_service.delete_objects", new_callable=AsyncMock) as dl:
            resp = await client.delete("/plots/Uadmin/5/photos/9", headers=auth_as("Uadmin"))
        assert resp.status_code == 403            # ผู้ดูแลไม่ใช่คนอัปโหลด/เจ้าของ — ไม่ลบรูปแทน
        dl.assert_not_called()
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_photo", new_callable=AsyncMock, return_value=photo), \
             patch("photo_service.delete_objects", new_callable=AsyncMock, return_value=True), \
             patch("main.delete_photo_row", new_callable=AsyncMock, return_value=True), \
             patch("main.log_audit", new_callable=AsyncMock):
            resp = await client.delete("/plots/U1/5/photos/9", headers=auth_as("U1"))
        assert resp.status_code == 200            # เจ้าของแปลงลบได้

    async def test_list_photos(self, client):
        rows = [{"id": 1, "plot_id": 5, "uploaded_by": "U1", "uploader_role": "farmer", "storage_path": "5/a.jpg",
                 "tags": ["normal"], "created_at": "2026-10-25T00:00:00Z"}]
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.list_photos", new_callable=AsyncMock, return_value=rows), \
             patch("photo_service.signed_urls", new_callable=AsyncMock, return_value={"5/a.jpg": "https://s/a"}):
            body = (await client.get("/plots/U1/5/photos", headers=auth_as("U1"))).json()
        assert body["count"] == 1 and body["photos"][0]["url"] == "https://s/a"
        assert "storage_path" not in body["photos"][0] and "uploaded_by" not in body["photos"][0]

    async def test_tags_endpoint(self, client):
        tags = (await client.get("/photo-tags")).json()["tags"]
        assert {"id": "waterlogged", "name_th": "น้ำขัง"} in tags
