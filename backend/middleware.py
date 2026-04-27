"""
middleware.py — Rate limiting และ request logging
ป้องกัน abuse และ spam จาก client ที่ไม่ใช่ LINE
"""

import time
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

        # /analyze — strict limit (GEE call แพง)
        if request.url.path == "/analyze":
            if self._is_limited(self._analyze_buckets, ip,
                                  RATE_LIMIT_BURST, RATE_LIMIT_WINDOW):
                logger.warning(f"Rate limit hit on /analyze from {ip}")
                return Response(
                    content='{"detail":"คำขอบ่อยเกินไป กรุณารอสักครู่"}',
                    status_code=429,
                    media_type="application/json",
                )

        # Global rate limit
        if self._is_limited(self._buckets, ip,
                             RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW):
            logger.warning(f"Rate limit hit from {ip}")
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
