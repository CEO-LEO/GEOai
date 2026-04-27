"""
cache.py — In-memory LRU cache สำหรับผล GEE analysis
ป้องกันการเรียก GEE ซ้ำสำหรับพิกัดเดิมในช่วงเวลาสั้นๆ
ลดเวลา response จาก ~30s เหลือ <1ms ถ้า hit cache

Cache key: (lat ปัดเป็น 3 ทศนิยม, lng ปัดเป็น 3 ทศนิยม)
≈ 111 เมตร ต่อ 0.001 องศา → แปลงขนาด < 100m จะ cache ร่วมกัน

TTL: 6 ชั่วโมง (GEE data อัปเดตรายวัน จึงไม่จำเป็นต้อง invalidate บ่อย)
Max size: 500 entries
"""

import time
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 6 * 3600   # 6 hours
_CACHE_MAX_SIZE    = 500
_COORD_PRECISION   = 3           # ปัดพิกัดทศนิยม 3 ตำแหน่ง


class _LRUCache:
    def __init__(self, max_size: int, ttl: int):
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl      = ttl

    def _key(self, lat: float, lng: float) -> str:
        return f"{round(lat, _COORD_PRECISION)},{round(lng, _COORD_PRECISION)}"

    def get(self, lat: float, lng: float) -> dict | None:
        key = self._key(lat, lng)
        if key not in self._store:
            return None
        value, ts = self._store[key]
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            logger.debug(f"Cache expired: {key}")
            return None
        # move to end (most recently used)
        self._store.move_to_end(key)
        logger.debug(f"Cache hit: {key}")
        return value

    def set(self, lat: float, lng: float, data: dict) -> None:
        key = self._key(lat, lng)
        self._store[key] = (data, time.monotonic())
        self._store.move_to_end(key)
        if len(self._store) > self._max_size:
            evicted = self._store.popitem(last=False)
            logger.debug(f"Cache evicted: {evicted[0]}")

    def stats(self) -> dict:
        now    = time.monotonic()
        valid  = sum(1 for _, (_, ts) in self._store.items()
                     if now - ts <= self._ttl)
        return {"size": len(self._store), "valid": valid,
                "max_size": self._max_size, "ttl_hours": self._ttl / 3600}


# Singleton instance
_gee_cache = _LRUCache(max_size=_CACHE_MAX_SIZE, ttl=_CACHE_TTL_SECONDS)


def get_cached(lat: float, lng: float) -> dict | None:
    return _gee_cache.get(lat, lng)


def set_cached(lat: float, lng: float, data: dict) -> None:
    _gee_cache.set(lat, lng, data)


def cache_stats() -> dict:
    return _gee_cache.stats()
