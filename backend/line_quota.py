"""
line_quota.py — ตรวจโควตาข้อความ LINE OA ก่อนส่ง + สถานะสุขภาพการส่งข้อความ

ที่มา: 2026-10 OA อยู่แพลน Free (300 ข้อความ/เดือน) โควตาหมดตั้งแต่ 7 ต.ค. แล้ว push
ล้มเหลวเงียบๆ 8 วันโดยไม่มีใครรู้ (ดู docs/v2/phase1a-report.md)

- ก่อน push ทุกครั้ง: ถามโควตาคงเหลือ (cache 10 นาที + นับเพิ่มเองระหว่างนั้น)
  ใกล้เต็มให้ส่งเฉพาะข้อความสำคัญ:
      critical (คำเตือนความเสี่ยงเพิ่มขึ้น, ฝนวิกฤต)  → ส่งจนกว่าจะหมด
      normal   (แจ้งเตือนทั่วไป)                     → ส่งเมื่อเหลือ ≥ 10%
      low      (สรุปรายสัปดาห์)                       → ส่งเมื่อเหลือ ≥ 25%
- เก็บสถิติส่งไม่สำเร็จ/ถูกข้ามใน 24 ชม. → health() ให้ /admin/messaging/status
  และ GitHub Actions (keep-alive.yml) ตรวจทุกชั่วโมง ถ้าไม่ ok งานจะล้ม → GitHub ส่ง
  อีเมลแจ้งเจ้าของ repo (ใช้ได้แม้โควตา LINE หมด ซึ่งแจ้งผ่าน LINE ไม่ได้)
"""

import logging
import os
import time
from collections import deque

import httpx

logger = logging.getLogger(__name__)

QUOTA_URL       = "https://api.line.me/v2/bot/message/quota"
CONSUMPTION_URL = "https://api.line.me/v2/bot/message/quota/consumption"
REFRESH_S       = 600

PRIORITY_MIN_REMAINING_FRACTION = {"critical": 0.0, "normal": 0.10, "low": 0.25}

_state = {"type": None, "quota": None, "used": None, "fetched_at": 0.0, "sent_since_fetch": 0,
          "last_error": None}
_events: deque[tuple[float, str]] = deque(maxlen=1000)   # (เวลา, "failed"/"skipped")


def _token() -> str:
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


async def refresh(force: bool = False) -> None:
    if not force and time.monotonic() - _state["fetched_at"] < REFRESH_S:
        return
    token = _token()
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            q = await client.get(QUOTA_URL, headers=headers)
            c = await client.get(CONSUMPTION_URL, headers=headers)
        if q.status_code == 200 and c.status_code == 200:
            qj, cj = q.json(), c.json()
            _state.update(type=qj.get("type"), quota=qj.get("value"), used=cj.get("totalUsage"),
                          fetched_at=time.monotonic(), sent_since_fetch=0, last_error=None)
        else:
            _state["last_error"] = f"quota API {q.status_code}/{c.status_code}"
            logger.warning(f"LINE quota check failed: {_state['last_error']}")
    except httpx.HTTPError as e:
        _state["last_error"] = type(e).__name__
        logger.warning(f"LINE quota check error: {type(e).__name__}")


def remaining() -> int | None:
    """None = ไม่จำกัด (type=none) หรือยังไม่รู้"""
    if _state["type"] != "limited" or _state["quota"] is None or _state["used"] is None:
        return None
    return max(0, _state["quota"] - _state["used"] - _state["sent_since_fetch"])


def is_allowed(priority: str) -> bool:
    left = remaining()
    if left is None:
        return True          # ไม่รู้โควตา → ส่งตามปกติ (ปลอดภัยกว่าเงียบทั้งระบบ)
    if left <= 0:
        return False
    quota = _state["quota"] or 1
    return left / quota >= PRIORITY_MIN_REMAINING_FRACTION.get(priority, 0.10)


async def check(priority: str) -> bool:
    await refresh()
    return is_allowed(priority)


def note_sent() -> None:
    _state["sent_since_fetch"] += 1


def note_exhausted() -> None:
    """LINE ตอบ 429 ว่าเกินโควตาเดือนนี้ — ถือว่าเหลือ 0 ทันทีจนกว่าจะเช็กรอบใหม่"""
    if _state["quota"] is not None:
        _state["used"] = _state["quota"]
        _state["sent_since_fetch"] = 0
        _state["fetched_at"] = time.monotonic()


def note_event(kind: str) -> None:
    _events.append((time.time(), kind))


def _count_recent(kind: str, window_s: int = 86400) -> int:
    cutoff = time.time() - window_s
    return sum(1 for t, k in _events if k == kind and t >= cutoff)


def health() -> dict:
    """ok / warn / critical พร้อมเหตุผล — ใช้ใน /admin/messaging/status"""
    left = remaining()
    quota = _state["quota"]
    failed = _count_recent("failed")
    skipped = _count_recent("skipped")
    reasons = []
    level = "ok"
    if left is not None and left <= 0:
        level = "critical"; reasons.append("โควตาข้อความ LINE เดือนนี้หมดแล้ว")
    elif left is not None and quota and left / quota < 0.10:
        level = "warn"; reasons.append(f"โควตาเหลือ {left}/{quota} (< 10%)")
    if failed:
        level = "critical"; reasons.append(f"ส่งไม่สำเร็จ {failed} ครั้งใน 24 ชม.")
    if skipped:
        if level == "ok":
            level = "warn"
        reasons.append(f"ข้ามการส่งเพราะโควตา {skipped} ครั้งใน 24 ชม.")
    return {
        "health": level,
        "reasons": reasons,
        "quota_type": _state["type"],
        "quota": quota,
        "used": None if _state["used"] is None else _state["used"] + _state["sent_since_fetch"],
        "remaining": left,
        "failed_24h": failed,
        "skipped_24h": skipped,
        "quota_check_error": _state["last_error"],
    }


def reset_for_tests() -> None:
    _state.update(type=None, quota=None, used=None, fetched_at=0.0, sent_since_fetch=0, last_error=None)
    _events.clear()
