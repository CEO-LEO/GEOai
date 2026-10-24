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

import line_quota

logger = logging.getLogger(__name__)

LINE_API_URL = "https://api.line.me/v2/bot/message/push"

# ── Retry config ──────────────────────────────────────
MAX_RETRIES       = 3
RETRY_DELAYS_S    = [2, 5, 15]  # delay ก่อน retry แต่ละรอบ
FAILED_QUEUE_MAX  = 200         # เก็บ failed messages ไว้ตรวจสอบ

# In-memory failed queue สำหรับ monitoring
_failed_queue: deque[dict] = deque(maxlen=FAILED_QUEUE_MAX)


MAX_MESSAGES_PER_PUSH = 5   # ข้อจำกัดของ LINE: 1 push ส่งได้ไม่เกิน 5 message object


async def send_line_message(
    user_id: str, message: str, flex: dict | None = None, image_url: str | None = None,
    *, messages: list[dict] | None = None, kind: str = "general", priority: str = "normal",
    plot_id: int | None = None, level: str | None = None,
    log_plots: list[tuple[int | None, str | None]] | None = None,
) -> bool:
    """
    ส่งข้อความ push ไปหา user_id ทาง LINE Messaging API
    คืน True ถ้าสำเร็จ, False ถ้าล้มเหลวหรือถูกข้ามเพราะโควตา

    - 1 push ไปหา 1 คน นับเป็น 1 ข้อความในโควตา ไม่ว่าจะมีกี่ message object (สูงสุด 5)
      จึงควรรวมการ์ด + รูปไว้ใน push เดียวเสมอ (ส่ง messages= มาเองได้)
    - image_url (ถ้ามี) ต่อท้ายการ์ด/ข้อความหลักใน push เดียวกัน ต้องเป็น https ที่ LINE
      ดึงได้เองโดยไม่ต้อง auth (ดู plot_image_service.upload_plot_image)
    - priority: critical / normal / low — ตรวจโควตาก่อนส่ง ใกล้เต็มจะส่งเฉพาะที่สำคัญ
      (ดู line_quota.py) ทุกการส่ง/ล้มเหลว/ข้าม ถูก log และบันทึกลง line_message_log
    - log_plots: push เดียวที่รวมหลายแปลง (เช่นเตือนฝน) — บันทึก 1 แถวต่อแปลง [(plot_id, level)]
      เพื่อให้การกันส่งซ้ำรายแปลงยังทำงาน (โควตานับครั้งเดียวต่อ push เหมือนเดิม)
    """
    targets = log_plots or [(plot_id, level)]
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set — skipping push")
        return False

    if messages is None:
        messages = [flex if flex else {"type": "text", "text": message}]
        if image_url:
            messages.append({
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            })
    if len(messages) > MAX_MESSAGES_PER_PUSH:
        logger.warning(f"push has {len(messages)} messages — truncated to {MAX_MESSAGES_PER_PUSH}")
        messages = messages[:MAX_MESSAGES_PER_PUSH]

    if not await line_quota.check(priority):
        line_quota.note_event("skipped")
        logger.error(f"LINE push SKIPPED (quota) kind={kind} priority={priority} "
                     f"remaining={line_quota.remaining()} user={user_id[:8]}…")
        for pid, lvl in targets:
            await _log(user_id, kind, priority, "skipped_quota", None, pid, lvl, "quota low/exhausted")
        return False

    payload = {"to": user_id, "messages": messages}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    last_status = 0
    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(LINE_API_URL, headers=headers, json=payload)

            last_status = resp.status_code
            last_error = resp.text[:300]

            if resp.status_code == 200:
                line_quota.note_sent()
                logger.info(f"LINE push sent kind={kind} to {user_id[:8]}…"
                            + (f" (retry {attempt})" if attempt > 0 else ""))
                for pid, lvl in targets:
                    await _log(user_id, kind, priority, "sent", 200, pid, lvl, None)
                return True

            # เกินโควตาเดือนนี้ — retry ไปก็ไม่ผ่าน
            if resp.status_code == 429 and "monthly limit" in resp.text.lower():
                line_quota.note_exhausted()
                logger.error(f"LINE push failed: monthly quota exhausted — {resp.text[:200]}")
                break

            # 4xx client errors (except 429) → ไม่ retry
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                logger.error(f"LINE push failed (client error): {resp.status_code} — {resp.text}")
                break

            # 429 (rate) / 5xx → retry
            logger.warning(
                f"LINE push attempt {attempt + 1}/{MAX_RETRIES} failed: "
                f"{resp.status_code} — retrying in {RETRY_DELAYS_S[attempt]}s"
            )

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_error = type(e).__name__
            logger.warning(
                f"LINE push attempt {attempt + 1}/{MAX_RETRIES} network error: {e} "
                f"— retrying in {RETRY_DELAYS_S[attempt]}s"
            )

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS_S[attempt])

    _failed_queue.append({
        "user_id":   user_id,
        "kind":      kind,
        "status":    last_status,
        "message":   message[:100],
        "has_flex":  flex is not None,
        "has_image": image_url is not None,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "attempts":  MAX_RETRIES,
    })
    line_quota.note_event("failed")
    logger.error(f"LINE push FAILED kind={kind} status={last_status} for {user_id[:8]}…")
    for pid, lvl in targets:
        await _log(user_id, kind, priority, "failed", last_status or None, pid, lvl, last_error)
    return False


async def _log(user_id, kind, priority, status, http_status, plot_id, level, error) -> None:
    # import ช้า — database import ของหนักและ test หลายตัว import line_sender ตรงๆ
    try:
        from database import log_line_message
        await log_line_message(user_id, kind, priority, status, http_status=http_status,
                               plot_id=plot_id, level=level, error=error)
    except Exception as e:
        logger.warning(f"message log failed: {type(e).__name__}")


def get_failed_queue(limit: int = 50) -> list[dict]:
    """คืน failed messages ล่าสุดสำหรับ admin monitoring"""
    return list(reversed(list(_failed_queue)))[:limit]


def get_retry_stats() -> dict:
    """สรุปสถิติ retry queue"""
    return {
        "failed_count": len(_failed_queue),
        "max_capacity":  FAILED_QUEUE_MAX,
    }

