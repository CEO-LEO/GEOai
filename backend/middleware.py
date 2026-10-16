"""
middleware.py — Rate limiting และ request logging

แบ่งเป็น 2 ชั้น:

1. RateLimitMiddleware — เพดานหยาบต่อ IP เฉพาะ path ที่เป็น API (ไม่นับไฟล์ static
   ของ /liff, /dashboard และ /health) ใช้กัน flood เท่านั้น จึงตั้งไว้สูงพอให้
   คน 30-50 คนที่ใช้ Wi-Fi เดียวกัน (IP เดียวกัน) ตอนอบรมใช้งานพร้อมกันได้

   เดิมจำกัด 10 คำขอ/นาที/IP รวมไฟล์ static ด้วย — ทดสอบจริง 2026-10-15 โดน 429
   หลังยิงไปราว 12 ครั้ง (ดู docs/v2/phase0-report.md หัวข้อ 4) ถ้าจัดอบรมในห้อง
   เดียวกันแอปจะโหลดไม่ขึ้นทั้งห้อง

2. user_rate_limit(...) — dependency จำกัดต่อ LINE userId ที่ยืนยันตัวตนแล้ว
   (ดู auth.current_user) ใช้คุม endpoint แพงอย่าง /analyze ที่เรียก GEE
   คนละคนไม่แย่งโควตากันแม้อยู่หลัง IP เดียวกัน

ทั้งสองชั้นเก็บสถานะในแรมของโปรเซส — ใช้ได้กับ Render instance เดียวตอนนี้ ถ้าขยาย
เป็นหลาย instance ต้องย้ายไปเก็บที่ร่วมกัน (เช่น Redis)
"""

import logging
import os
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from auth import current_user

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────
# 50 คน x ราว 12 คำขอ API/นาที ตอนใช้งานหนัก (เปิดแอป + วิเคราะห์ + ดูประวัติ)
IP_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_IP_PER_MIN", "600"))
WINDOW_S = 60

# path ที่ไม่นับเลย: ไฟล์ static, health check, และ webhook (LINE เซ็นลายเซ็นมาแล้ว)
_EXEMPT_PREFIXES = ("/liff", "/dashboard", "/health", "/webhook", "/favicon")

_TOO_MANY = '{"detail":"คำขอบ่อยเกินไป กรุณารอสักครู่"}'


class SlidingWindowLimiter:
    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._last_sweep = time.monotonic()

    def hit(self, key: str, max_calls: int, window_s: int = WINDOW_S) -> bool:
        """บันทึก 1 ครั้ง คืน True ถ้าเกินเพดาน (ครั้งที่เกินไม่ถูกนับ)"""
        now = time.monotonic()
        self._sweep(now, window_s)
        calls = [t for t in self._buckets[key] if now - t < window_s]
        if len(calls) >= max_calls:
            self._buckets[key] = calls
            return True
        calls.append(now)
        self._buckets[key] = calls
        return False

    def _sweep(self, now: float, window_s: int) -> None:
        # ลบ key ที่ไม่มีคำขอใน window ล่าสุดทิ้งเป็นระยะ กัน dict โตไม่จำกัด
        if now - self._last_sweep < window_s:
            return
        self._last_sweep = now
        for k in [k for k, v in self._buckets.items() if not v or now - v[-1] >= window_s]:
            del self._buckets[k]

    def reset(self) -> None:
        self._buckets.clear()


_ip_limiter = SlidingWindowLimiter()
_user_limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    # Render อยู่หลัง Cloudflare — CF-Connecting-IP ถูกเขียนทับโดย Cloudflare เสมอ
    # (client ปลอมไม่ได้) ส่วน X-Forwarded-For ตัวแรก client ใส่มาเองได้ ใช้เป็นทางสำรอง
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_exempt(path: str) -> bool:
    return path == "/" or path.startswith(_EXEMPT_PREFIXES)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        ip = client_ip(request)
        path = request.url.path

        if ip in ("127.0.0.1", "::1", "testclient") or _is_exempt(path):
            return await call_next(request)

        if _ip_limiter.hit(f"ip:{ip}", IP_LIMIT_PER_MIN):
            logger.warning(f"IP rate limit hit on {path} from {ip}")
            return Response(content=_TOO_MANY, status_code=429, media_type="application/json")

        start = time.monotonic()
        response = await call_next(request)
        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"{request.method} {path} → {response.status_code} ({elapsed:.0f}ms) [{ip}]")
        return response


def user_rate_limit(bucket: str, max_calls: int, window_s: int = WINDOW_S):
    """
    dependency จำกัดจำนวนคำขอต่อผู้ใช้ (ต่อ bucket) — ต้องผ่าน current_user ก่อนเสมอ
    ตัวอย่าง: dependencies=[Depends(user_rate_limit("analyze", 3))]
    """
    async def _limit(user_id: str = Depends(current_user)) -> str:
        if _user_limiter.hit(f"{bucket}:{user_id}", max_calls, window_s):
            logger.warning(f"User rate limit hit: bucket={bucket} user={user_id[:8]}…")
            raise HTTPException(status_code=429, detail="คำขอบ่อยเกินไป กรุณารอสักครู่")
        return user_id
    return _limit


def reset_limits() -> None:
    """ใช้ใน test"""
    _ip_limiter.reset()
    _user_limiter.reset()
