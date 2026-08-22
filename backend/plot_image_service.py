"""
plot_image_service.py — สร้าง + อัปโหลดภาพแผนที่ความชื้นสำหรับส่งเข้า LINE

ย้ายมาจาก main.py (เดิมมีแค่ /analyze เรียกใช้) เพราะตอนนี้ scheduler.py
(daily_scan_job — สรุปแปลงประจำวัน) ต้องเรียกใช้ด้วย — main.py import จาก
scheduler.py อยู่แล้ว (create_scheduler/daily_scan_job/rain_alert_job) ถ้าฝัง
ฟังก์ชันนี้ไว้ใน main.py แล้วให้ scheduler.py import กลับจะเกิด circular import
เลยแยกมาเป็นโมดูลกลางที่ทั้งคู่ import ได้โดยไม่ชนกัน
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.concurrency import run_in_threadpool

import gee_analysis
from database import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# Pillow เป็น dependency ใหม่ (สำหรับฟีเจอร์ส่งรูปแผนที่เข้า LINE) — กัน import พัง
# แล้วดึงทั้งแอปตายไปด้วย ถ้าพัง ฟีเจอร์นี้แค่ปิดตัวเอง (คืน None) ส่วนอื่นทำงานต่อได้ปกติ
try:
    from map_image import render_plot_grid_image
    _MAP_IMAGE_AVAILABLE = True
except Exception as _map_image_err:
    logger.warning(f"map_image unavailable — image feature disabled: {_map_image_err}")
    render_plot_grid_image = None
    _MAP_IMAGE_AVAILABLE = False

# ── ที่เก็บภาพ ──────────────────────────────────────────────────────────
# เดิมแคชรูปที่ compose แล้วไว้ใน dict ใน RAM ของโปรเซสเอง (token→bytes, TTL 1 ชม.)
# แล้วเสิร์ฟผ่าน /plot-image/{token}.png ของแอปเอง — ใช้ไม่ได้จริง: LINE ไม่ได้ดึง
# รูปทันทีตอน push (fetch แบบ lazy ตอนผู้ใช้เปิดอ่านข้อความจริงๆ ซึ่งอาจช้ากว่านั้น
# มาก) พอโปรเซสรีสตาร์ทระหว่างนั้น (sleep/wake ของ Render free tier, หรือ deploy
# ใหม่ที่เกิดบ่อยเพราะแอปนี้ยังพัฒนาอยู่) แคชในแรมหายไปทั้งหมด รูป 404 ทันที — ตรง
# กับอาการที่ผู้ใช้บางคนเห็นรูปไม่ขึ้น (ทั้งกดปุ่ม "แปลงของฉัน" และสรุปประจำวัน)
# เปลี่ยนมาอัปโหลดขึ้น Supabase Storage แทน (bucket "plot-images", public) — URL
# คงอยู่ถาวรไม่ผูกกับ process/instance ที่สร้างมันขึ้นมาอีกต่อไป
_STORAGE_BUCKET = "plot-images"


async def upload_plot_image(png_bytes: bytes) -> str | None:
    """อัปโหลดภาพขึ้น Supabase Storage คืน public URL หรือ None ถ้าล้มเหลว"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase not configured — cannot upload plot image")
        return None
    # แยกโฟลเดอร์ตามวัน (YYYY-MM-DD) ตั้งแต่ตอนอัปโหลด เพื่อให้ลบของเก่าเป็นชุดตาม
    # วันได้ง่ายทีหลัง (ดู delete_old_plot_images) ไม่ต้อง list ทีละไฟล์เป็นพัน
    path = f"{datetime.now(timezone.utc):%Y-%m-%d}/{uuid.uuid4().hex}.png"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}/{path}",
                headers={
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "apikey": SUPABASE_KEY,
                    "Content-Type": "image/png",
                },
                content=png_bytes,
            )
        if resp.status_code not in (200, 201):
            logger.warning(f"Plot image upload failed: {resp.status_code} — {resp.text}")
            return None
        return f"{SUPABASE_URL}/storage/v1/object/public/{_STORAGE_BUCKET}/{path}"
    except Exception as e:
        logger.warning(f"Plot image upload error: {e}")
        return None


async def delete_old_plot_images(older_than_days: int = 7) -> bool:
    """
    ลบรูปแผนที่เก่าใน Storage — LINE ดึงรูปไปโฮสต์เองแค่ครั้งแรกไม่นานหลัง push
    เก็บต้นฉบับไว้นานเกินไม่มีประโยชน์ มีแต่กิน storage quota เปล่าๆ (ดูเหตุผล
    เดียวกับ delete_old_grid_snapshots ใน database.py) เรียกจาก daily_scan_job
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/list/{_STORAGE_BUCKET}",
                headers=headers, json={"prefix": "", "limit": 1000},
            )
            if resp.status_code != 200:
                logger.warning(f"Plot image list failed: {resp.status_code} — {resp.text}")
                return False
            # โฟลเดอร์ (prefix เสมือน) มี id เป็น null เสมอตาม Supabase Storage list API
            old_folders = [item["name"] for item in resp.json()
                          if item.get("id") is None and item["name"] < cutoff]
            for folder in old_folders:
                list_resp = await client.post(
                    f"{SUPABASE_URL}/storage/v1/object/list/{_STORAGE_BUCKET}",
                    headers=headers, json={"prefix": f"{folder}/", "limit": 1000},
                )
                if list_resp.status_code != 200:
                    continue
                paths = [f"{folder}/{item['name']}" for item in list_resp.json()]
                if paths:
                    await client.request(
                        "DELETE", f"{SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}",
                        headers=headers, json={"prefixes": paths},
                    )
        return True
    except Exception as e:
        logger.warning(f"delete_old_plot_images failed (non-fatal): {e}")
        return False


async def build_plot_grid_image_url(polygon: list[list[float]], plot_name: str = "") -> str | None:
    """
    สร้างภาพแผนที่ความชื้นสำหรับส่งเข้า LINE คู่กับผลวิเคราะห์ — คืน URL รูปภาพ
    หรือ None ถ้าล้มเหลว (แค่ "ของแถม" ไม่ควรทำให้คำขอหลักพังตาม — ใช้ที่เดียวกัน
    ทั้ง /analyze และ daily_scan_job)
    ยิง GEE 2 ครั้งพร้อมกัน (grid + ภาพถ่ายดาวเทียม) ผ่าน threadpool ลด wall-time
    """
    if not _MAP_IMAGE_AVAILABLE:
        return None
    try:
        grid_points, thumbnail = await asyncio.gather(
            run_in_threadpool(gee_analysis.get_moisture_grid, polygon, 10),
            run_in_threadpool(gee_analysis.get_plot_satellite_thumbnail, polygon),
        )
        png_bytes, bounds = thumbnail
        composed = await run_in_threadpool(
            render_plot_grid_image, png_bytes, bounds, grid_points, polygon, 10, plot_name
        )
        url = await upload_plot_image(composed)
        if url:
            logger.info(f"Plot grid image ready: {url}")
        return url
    except Exception as e:
        logger.warning(f"Plot grid image generation failed ({type(e).__name__}, non-fatal, skipping): {e}")
        return None
