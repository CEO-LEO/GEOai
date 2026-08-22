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
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.concurrency import run_in_threadpool

import gee_analysis
from database import SUPABASE_URL, SUPABASE_KEY, set_plot_thumbnail

logger = logging.getLogger(__name__)

# Pillow เป็น dependency ใหม่ (สำหรับฟีเจอร์ส่งรูปแผนที่เข้า LINE) — กัน import พัง
# แล้วดึงทั้งแอปตายไปด้วย ถ้าพัง ฟีเจอร์นี้แค่ปิดตัวเอง (คืน None) ส่วนอื่นทำงานต่อได้ปกติ
try:
    from map_image import render_plot_grid_image, make_list_thumbnail
    _MAP_IMAGE_AVAILABLE = True
except Exception as _map_image_err:
    logger.warning(f"map_image unavailable — image feature disabled: {_map_image_err}")
    render_plot_grid_image = None
    make_list_thumbnail = None
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
# โฟลเดอร์รูปย่อ (thumbnail) ต่อแปลง — คนละชุดกับรูปแผนที่ที่ push เข้า LINE
# (โฟลเดอร์ YYYY-MM-DD, สุ่มชื่อไฟล์ทุกครั้ง) รูปย่อนี้เขียนทับที่ path เดิมเสมอ
# (thumbnails/{plot_id}.png, upsert) เพราะเป็นรูปตัวแทนถาวรของแปลง ไม่ใช่ของที่
# ส่งครั้งเดียวแล้วทิ้ง — ต้อง "ไม่" โดน delete_old_plot_images กวาดทิ้งเหมือนกัน
_THUMB_PREFIX = "thumbnails"
_DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _upload_bytes(path: str, png_bytes: bytes, upsert: bool = False) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase not configured — cannot upload plot image")
        return False
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "image/png",
    }
    if upsert:
        headers["x-upsert"] = "true"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}/{path}",
                headers=headers, content=png_bytes,
            )
        if resp.status_code not in (200, 201):
            logger.warning(f"Plot image upload failed ({path}): {resp.status_code} — {resp.text}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Plot image upload error ({path}): {e}")
        return False


async def upload_plot_image(png_bytes: bytes) -> str | None:
    """อัปโหลดภาพขึ้น Supabase Storage คืน public URL หรือ None ถ้าล้มเหลว"""
    # แยกโฟลเดอร์ตามวัน (YYYY-MM-DD) ตั้งแต่ตอนอัปโหลด เพื่อให้ลบของเก่าเป็นชุดตาม
    # วันได้ง่ายทีหลัง (ดู delete_old_plot_images) ไม่ต้อง list ทีละไฟล์เป็นพัน
    path = f"{datetime.now(timezone.utc):%Y-%m-%d}/{uuid.uuid4().hex}.png"
    ok = await _upload_bytes(path, png_bytes)
    return f"{SUPABASE_URL}/storage/v1/object/public/{_STORAGE_BUCKET}/{path}" if ok else None


async def _upload_plot_thumbnail(plot_id: int, thumb_bytes: bytes) -> str | None:
    """อัปโหลดรูปย่อของแปลงที่ path คงที่ (เขียนทับรูปเก่าของแปลงเดิมเสมอ)"""
    path = f"{_THUMB_PREFIX}/{plot_id}.png"
    ok = await _upload_bytes(path, thumb_bytes, upsert=True)
    return f"{SUPABASE_URL}/storage/v1/object/public/{_STORAGE_BUCKET}/{path}" if ok else None


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
            # เช็ครูปแบบ YYYY-MM-DD ด้วย ไม่ใช่แค่เทียบ string เฉยๆ — กันโฟลเดอร์อื่น
            # (เช่น "thumbnails/" ของรูปย่อถาวรต่อแปลง) หลุดเข้ามาโดนลบผิดพลาด
            old_folders = [item["name"] for item in resp.json()
                          if item.get("id") is None
                          and _DATE_FOLDER_RE.match(item["name"])
                          and item["name"] < cutoff]
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


async def build_plot_grid_image_url(polygon: list[list[float]], plot_name: str = "",
                                    plot_id: int | None = None) -> str | None:
    """
    สร้างภาพแผนที่ความชื้นสำหรับส่งเข้า LINE คู่กับผลวิเคราะห์ — คืน dict
    {"url": str|None, "problem_points": list[dict]} (url=None ถ้าล้มเหลว — แค่
    "ของแถม" ไม่ควรทำให้คำขอหลักพังตาม — ใช้ที่เดียวกันทั้ง /analyze และ
    daily_scan_job) ยิง GEE 2 ครั้งพร้อมกัน (grid + ภาพถ่ายดาวเทียม) ผ่าน
    threadpool ลด wall-time

    problem_points: จุดที่พบปัญหาเด่นสุดในแปลง (ป้ายกริดอ้างอิง A1/B2/... เดียวกับ
    ที่พิมพ์ลงในรูป) — ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้" ในการ์ดไลน์ — คำนวณ
    จาก grid_points ชุดเดียวกับที่ใช้ทำรูป ไม่เรียก GEE เพิ่ม (ดู
    gee_analysis.select_problem_points) — ผู้เรียกเอาไปใส่ใน build_result_flex()

    plot_id (ถ้ามี): ถือโอกาสครอปรูปถ่ายดาวเทียมที่ดึงมาแล้ว (ไม่เรียก GEE เพิ่ม)
    เป็นรูปย่อสี่เหลี่ยมเล็กๆ อัปโหลดเก็บถาวรแยกต่างหาก แล้วบันทึกลง plots.thumbnail_url
    — ใช้แสดงในหน้า "แปลงของฉัน" (LIFF) ให้แยกแต่ละแปลงออกจากกันง่ายขึ้น (ผู้ใช้ขอ)
    ล้มเหลวได้โดยไม่กระทบรูปแผนที่หลักที่ส่งเข้า LINE (แยก try/except ต่างหาก)
    """
    empty = {"url": None, "problem_points": []}
    if not _MAP_IMAGE_AVAILABLE:
        return empty
    try:
        grid_points, thumbnail = await asyncio.gather(
            run_in_threadpool(gee_analysis.get_moisture_grid, polygon, 10),
            run_in_threadpool(gee_analysis.get_plot_satellite_thumbnail, polygon),
        )
        png_bytes, bounds = thumbnail

        # ต้องหา problem_points ก่อนวาดรูป (ไม่ใช่หลัง) เพราะผู้ใช้ขอให้วงกลมจุด
        # ปัญหาปรากฏ "บนรูป" ด้วยเลย ไม่ใช่แค่ในข้อความ — ล้มเหลวได้โดยไม่กระทบรูป
        # หลัก (แค่จะไม่มีวงกลม/ป้ายจุดปัญหาบนรูป ยังมีป้ายกริดอ้างอิงข้างขอบอยู่)
        problem_points = []
        try:
            gee_analysis.assign_grid_reference(grid_points, polygon)
            problem_points = gee_analysis.select_problem_points(grid_points)
        except Exception as e:
            logger.warning(f"Problem-point selection failed (non-fatal): {e}")

        composed = await run_in_threadpool(
            render_plot_grid_image, png_bytes, bounds, grid_points, polygon, 10, plot_name,
            problem_points,
        )
        url = await upload_plot_image(composed)
        if url:
            logger.info(f"Plot grid image ready: {url}")
    except Exception as e:
        logger.warning(f"Plot grid image generation failed ({type(e).__name__}, non-fatal, skipping): {e}")
        return empty

    if plot_id is not None:
        try:
            thumb_bytes = await run_in_threadpool(make_list_thumbnail, png_bytes)
            thumb_url = await _upload_plot_thumbnail(plot_id, thumb_bytes)
            if thumb_url:
                await set_plot_thumbnail(plot_id, thumb_url)
        except Exception as e:
            logger.warning(f"Plot list thumbnail failed (non-fatal, plot {plot_id}): {e}")

    return {"url": url, "problem_points": problem_points}
