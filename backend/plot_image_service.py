"""
plot_image_service.py — สร้าง + แคชภาพแผนที่ความชื้นสำหรับส่งเข้า LINE

ย้ายมาจาก main.py (เดิมมีแค่ /analyze เรียกใช้) เพราะตอนนี้ scheduler.py
(daily_scan_job — สรุปแปลงประจำวัน) ต้องเรียกใช้ด้วย — main.py import จาก
scheduler.py อยู่แล้ว (create_scheduler/daily_scan_job/rain_alert_job) ถ้าฝัง
ฟังก์ชันนี้ไว้ใน main.py แล้วให้ scheduler.py import กลับจะเกิด circular import
เลยแยกมาเป็นโมดูลกลางที่ทั้งคู่ import ได้โดยไม่ชนกัน
"""

import asyncio
import logging
import os
import time
import uuid

from fastapi.concurrency import run_in_threadpool

import gee_analysis

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

# LINE ต้องการ URL https สาธารณะที่ server ของ LINE ดึงเองได้โดยไม่ auth — เก็บภาพ
# ที่ compose แล้วไว้ใน memory ชั่วคราว (ไม่ต้องถาวร เพราะ LINE จะดึงภายในไม่กี่วินาที
# ถึงนาทีหลัง push ไปแล้ว) ผ่าน token สุ่ม แล้วเสิร์ฟผ่าน endpoint ใน main.py
PUBLIC_BASE_URL      = os.environ.get("PUBLIC_BASE_URL", "https://iamroot.onrender.com")
PLOT_IMAGE_TTL_S      = 3600   # 1 ชม. — เกินพอสำหรับ LINE ดึงภาพไปแสดง
PLOT_IMAGE_CACHE_MAX  = 200    # กัน memory โตไม่จำกัดถ้ามีการวิเคราะห์รัวๆ
_plot_image_cache: dict[str, tuple[bytes, float]] = {}


def cache_plot_image(png_bytes: bytes) -> str:
    now = time.time()
    expired = [k for k, (_, exp) in _plot_image_cache.items() if exp < now]
    for k in expired:
        del _plot_image_cache[k]
    if len(_plot_image_cache) >= PLOT_IMAGE_CACHE_MAX:
        oldest = min(_plot_image_cache, key=lambda k: _plot_image_cache[k][1])
        del _plot_image_cache[oldest]
    token = uuid.uuid4().hex
    _plot_image_cache[token] = (png_bytes, now + PLOT_IMAGE_TTL_S)
    return token


def get_cached_plot_image(token: str) -> bytes | None:
    entry = _plot_image_cache.get(token)
    if not entry or entry[1] < time.time():
        return None
    return entry[0]


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
        token = cache_plot_image(composed)
        url = f"{PUBLIC_BASE_URL}/plot-image/{token}.png"
        logger.info(f"Plot grid image ready: {url}")
        return url
    except Exception as e:
        logger.warning(f"Plot grid image generation failed ({type(e).__name__}, non-fatal, skipping): {e}")
        return None
