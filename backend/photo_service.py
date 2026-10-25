"""
photo_service.py — รูปถ่ายภาคสนาม (Phase 2c)

- แอปบีบอัดรูปในเครื่องก่อนส่ง (canvas → JPEG ซึ่งไม่มี EXIF) และอ่านพิกัด/เวลาจาก EXIF แยกส่งมาเป็นข้อมูล
- server เข้ารหัสรูปใหม่อีกรอบเสมอ (หมุนตาม EXIF แล้วบันทึกแบบไม่มี EXIF, ด้านยาวไม่เกิน MAX_SIDE)
  กันกรณีแอปรุ่นเก่า/เครื่องที่ส่งรูปดิบมา — ไฟล์ที่เก็บจึงไม่มีพิกัดฝังอยู่ (พิกัดอยู่ในตาราง photos เท่านั้น)
- เก็บใน Storage bucket แบบ private "plot-photos" (สร้างอัตโนมัติครั้งแรก) เปิดดูด้วย signed URL อายุสั้น
"""

import io
import logging

import httpx

from database import SUPABASE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

BUCKET = "plot-photos"
MAX_SIDE = 1600
JPEG_QUALITY = 82
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MIN_UPLOAD_BYTES = 1024
SIGNED_URL_TTL_S = 3600
MAX_TAGS = 8

TAGS_TH = {
    "waterlogged":  "น้ำขัง",
    "soggy_soil":   "ดินแฉะ",
    "dry_soil":     "ดินแห้ง/ขาดน้ำ",
    "yellow_leaves": "ใบเหลือง",
    "leaf_drop":    "ใบร่วง",
    "root_rot":     "โคนเน่า/รากเน่า",
    "wilting":      "ต้นเหี่ยว",
    "pest":         "แมลง/โรคพืช",
    "drainage":     "ร่องน้ำ/ทางระบายน้ำ",
    "normal":       "ปกติ",
    "other":        "อื่น ๆ",
}


def validate_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        if t not in TAGS_TH:
            raise ValueError(f"ไม่รู้จักแท็ก: {t}")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ValueError(f"แท็กได้ไม่เกิน {MAX_TAGS} อย่าง")
    return out


def clean_image(raw: bytes) -> tuple[bytes, int, int]:
    """ตรวจว่าเป็นรูปจริง → หมุนตาม EXIF → ย่อ → JPEG ไม่มี EXIF · ValueError ถ้าไม่ใช่รูป/ใหญ่เกิน"""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(raw) < MIN_UPLOAD_BYTES:
        raise ValueError("ไฟล์รูปเล็กผิดปกติ")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("ไฟล์รูปใหญ่เกิน 8 MB")
    try:
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("JPEG", "PNG", "WEBP", "MPO", "HEIF"):
            raise ValueError("รองรับเฉพาะรูป JPEG / PNG / WEBP")
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ValueError("ไฟล์นี้ไม่ใช่รูปภาพ")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)   # ไม่ส่ง exif= → ไม่มี EXIF ในไฟล์
    return buf.getvalue(), img.width, img.height


def _headers(content_type: str = "application/json") -> dict:
    return {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY, "Content-Type": content_type}


async def _ensure_bucket(client: httpx.AsyncClient) -> bool:
    resp = await client.post(f"{SUPABASE_URL}/storage/v1/bucket", headers=_headers(),
                             json={"id": BUCKET, "name": BUCKET, "public": False,
                                   "allowed_mime_types": ["image/jpeg"], "file_size_limit": 5 * 1024 * 1024})
    return resp.status_code in (200, 201) or "already exists" in resp.text.lower()


async def upload(path: str, data: bytes) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(2):
            resp = await client.post(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
                                     headers={**_headers("image/jpeg"), "x-upsert": "true"}, content=data)
            if resp.status_code in (200, 201):
                return True
            if attempt == 0 and await _ensure_bucket(client):
                continue
            logger.warning(f"photo upload failed: {resp.status_code} — {resp.text[:200]}")
            return False
    return False


async def signed_urls(paths: list[str]) -> dict[str, str]:
    """path → signed URL (อายุ SIGNED_URL_TTL_S) — เรียกครั้งเดียวทั้งชุด"""
    if not paths or not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET}", headers=_headers(),
                                     json={"expiresIn": SIGNED_URL_TTL_S, "paths": paths})
        if resp.status_code != 200:
            logger.warning(f"photo sign failed: {resp.status_code}")
            return {}
        return {r["path"]: f"{SUPABASE_URL}/storage/v1{r['signedURL']}" for r in resp.json() if r.get("signedURL")}
    except Exception as e:
        logger.warning(f"photo sign failed ({type(e).__name__})")
        return {}


async def delete_objects(paths: list[str]) -> bool:
    if not paths or not SUPABASE_URL or not SUPABASE_KEY:
        return True
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request("DELETE", f"{SUPABASE_URL}/storage/v1/object/{BUCKET}",
                                        headers=_headers(), json={"prefixes": paths})
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"photo delete failed ({type(e).__name__})")
        return False
