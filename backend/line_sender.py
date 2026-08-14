"""
LINE Messaging API — push message to user
พร้อมระบบ retry queue กรณี push ล้มเหลว
"""

import asyncio
import os
import logging
from collections import deque
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

LINE_API_URL = "https://api.line.me/v2/bot/message/push"

# ── Retry config ──────────────────────────────────────
MAX_RETRIES       = 3
RETRY_DELAYS_S    = [2, 5, 15]  # delay ก่อน retry แต่ละรอบ
FAILED_QUEUE_MAX  = 200         # เก็บ failed messages ไว้ตรวจสอบ

# In-memory failed queue สำหรับ monitoring
_failed_queue: deque[dict] = deque(maxlen=FAILED_QUEUE_MAX)


async def send_line_message(
    user_id: str, message: str, flex: dict | None = None, image_url: str | None = None
) -> bool:
    """
    ส่งข้อความ push ไปหา user_id ทาง LINE Messaging API
    ถ้าส่งไม่สำเร็จ → retry สูงสุด 3 ครั้ง (exponential backoff)
    คืน True ถ้าสำเร็จ, False ถ้าล้มเหลวหลัง retry หมด

    image_url (ถ้ามี) จะถูกส่งเป็นข้อความรูปภาพ "เพิ่มเติม" ต่อจากการ์ด/ข้อความหลัก
    ในการ push ครั้งเดียวกัน (LINE รับ messages หลายชิ้นต่อ 1 การเรียก API ได้ ขึ้น
    เป็นบับเบิลแยกกันในแชทตามลำดับ) ต้องเป็น URL https ที่ LINE server เข้าถึงได้เอง
    โดยไม่ต้องใช้ auth (ดู /plot-image/{token}.png ใน main.py)
    """
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set — skipping push")
        return False

    messages = [flex if flex else {"type": "text", "text": message}]
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        })

    payload = {
        "to": user_id,
        "messages": messages,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    last_status = 0
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(LINE_API_URL, headers=headers, json=payload)

            last_status = resp.status_code

            if resp.status_code == 200:
                logger.info(f"LINE push sent to {user_id}"
                            + (f" (retry {attempt})" if attempt > 0 else ""))
                return True

            # 4xx client errors (except 429) → ไม่ retry
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                logger.error(f"LINE push failed (client error): {resp.status_code} — {resp.text}")
                break

            # 429 / 5xx → retry
            logger.warning(
                f"LINE push attempt {attempt + 1}/{MAX_RETRIES} failed: "
                f"{resp.status_code} — retrying in {RETRY_DELAYS_S[attempt]}s"
            )

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            logger.warning(
                f"LINE push attempt {attempt + 1}/{MAX_RETRIES} network error: {e} "
                f"— retrying in {RETRY_DELAYS_S[attempt]}s"
            )

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS_S[attempt])

    # ทุก retry ล้มเหลว → เก็บลง failed queue
    _failed_queue.append({
        "user_id":   user_id,
        "status":    last_status,
        "message":   message[:100],
        "has_flex":  flex is not None,
        "has_image": image_url is not None,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "attempts":  MAX_RETRIES,
    })
    logger.error(f"LINE push FAILED after {MAX_RETRIES} retries for {user_id}")
    return False


def get_failed_queue(limit: int = 50) -> list[dict]:
    """คืน failed messages ล่าสุดสำหรับ admin monitoring"""
    return list(reversed(list(_failed_queue)))[:limit]


def get_retry_stats() -> dict:
    """สรุปสถิติ retry queue"""
    return {
        "failed_count": len(_failed_queue),
        "max_capacity":  FAILED_QUEUE_MAX,
    }

