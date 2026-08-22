"""
middleware.py — Rate limiting และ request logging
ป้องกัน abuse และ spam จาก client ที่ไม่ใช่ LINE
"""

import time
import json
import logging
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────
RATE_LIMIT_CALLS = 10        # จำนวน request สูงสุด
RATE_LIMIT_WINDOW = 60       # ต่อ N วินาที
RATE_LIMIT_BURST  = 3        # /analyze เรียกได้สูงสุดกี่ครั้ง/นาที


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Token bucket rate limiter แบบ in-memory
    เหมาะสำหรับ single-instance server (Railway/Render)
    """

    def __init__(self, app):
        super().__init__(app)
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._analyze_buckets: dict[str, list[float]] = defaultdict(list)

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def _get_rate_limit_key(self, request: Request, ip: str) -> str:
        """
        คีย์สำหรับ rate limit — เดิมใช้ IP อย่างเดียวทุก endpoint แต่มือถือไทยหลาย
        เจ้าใช้ CGNAT (หลายคนแชร์ IP สาธารณะเดียวกันจริงๆ) ทำให้เกษตรกรคนหนึ่งยิง
        ถี่แล้วไปเข้าคิว/โดนบล็อกคนอื่นที่ IP เดียวกันด้วยทั้งที่ไม่เกี่ยวกันเลย —
        /analyze มี user_id (LINE userId ตัวจริงต่อคน) แนบมาใน body อยู่แล้ว ใช้เป็น
        identity แทน IP ได้แม่นกว่า จึงลองอ่านมาใช้ก่อน ถ้าอ่านไม่ได้ (body ผิดรูป/ไม่มี
        user_id) ก็ fallback กลับไปที่ IP เหมือนเดิม — endpoint อื่นที่ไม่มี user_id
        ในตัว request (เช่น /analyze/grid, /analyze/preview) ยังใช้ IP ต่อไปเพราะ
        ไม่มีข้อมูลผู้ใช้จริงให้อ้างอิง

        อ่าน body ที่นี่แล้วยัง cache ไว้ใน request._body ปลอดภัย — Starlette replay
        body ที่ cache ไว้กลับให้ downstream (Pydantic model parsing ใน /analyze)
        อ่านซ้ำได้ปกติ ไม่ใช่การ "ใช้ body ทิ้ง" แต่อย่างใด
        """
        if request.url.path == "/analyze":
            try:
                body = await request.body()
                payload = json.loads(body)
                user_id = payload.get("user_id")
                if user_id and isinstance(user_id, str):
                    return f"user:{user_id}"
            except Exception:
                pass
        return f"ip:{ip}"

    def _is_limited(self, bucket: dict, key: str,
                    max_calls: int, window: int) -> bool:
        now   = time.monotonic()
        calls = bucket[key]
        # ลบ timestamp ที่พ้น window ออก
        bucket[key] = [t for t in calls if now - t < window]
        if len(bucket[key]) >= max_calls:
            return True
        bucket[key].append(now)
        return False

    async def dispatch(self, request: Request, call_next) -> Response:
        ip = self._get_ip(request)

        # Skip rate limiting for loopback (tests / local dev)
        if ip in ("127.0.0.1", "::1", "testclient"):
            return await call_next(request)

        # /webhook ไม่ rate limit (LINE ส่งมาจาก IP จำกัด)
        if request.url.path == "/webhook":
            return await call_next(request)

        key = await self._get_rate_limit_key(request, ip)

        # /analyze — strict limit (GEE call แพง)
        if request.url.path == "/analyze":
            if self._is_limited(self._analyze_buckets, key,
                                  RATE_LIMIT_BURST, RATE_LIMIT_WINDOW):
                logger.warning(f"Rate limit hit on /analyze from {key}")
                return Response(
                    content='{"detail":"คำขอบ่อยเกินไป กรุณารอสักครู่"}',
                    status_code=429,
                    media_type="application/json",
                )

        # Global rate limit
        if self._is_limited(self._buckets, key,
                             RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW):
            logger.warning(f"Rate limit hit from {key}")
            return Response(
                content='{"detail":"คำขอบ่อยเกินไป กรุณารอสักครู่"}',
                status_code=429,
                media_type="application/json",
            )

        start = time.monotonic()
        response = await call_next(request)
        elapsed  = (time.monotonic() - start) * 1000

        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} ({elapsed:.0f}ms) [{ip}]"
        )
        return response
