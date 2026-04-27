"""
In-memory ring buffer log handler
เก็บ log ล่าสุด 200 รายการสำหรับ /admin/logs endpoint
"""

import logging
from collections import deque
from datetime import datetime, timezone

MAX_ENTRIES = 200


class BufferHandler(logging.Handler):
    """Logging handler ที่เก็บ record ใน deque (ring buffer)."""

    def __init__(self, maxlen: int = MAX_ENTRIES):
        super().__init__()
        self._buffer: deque[dict] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append({
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "message": self.format(record),
        })

    def get_entries(self, level: str | None = None,
                    limit: int = 50) -> list[dict]:
        """คืน log entries ล่าสุด (reverse-chronological)."""
        entries = list(self._buffer)
        if level:
            level_upper = level.upper()
            entries = [e for e in entries if e["level"] == level_upper]
        return list(reversed(entries))[:limit]

    def stats(self) -> dict:
        """สรุปจำนวน log แต่ละ level."""
        counts: dict[str, int] = {}
        for e in self._buffer:
            counts[e["level"]] = counts.get(e["level"], 0) + 1
        return {"total": len(self._buffer), "max": self._buffer.maxlen, **counts}


# ── Singleton instance ─────────────────────────────────
_handler = BufferHandler()
_handler.setLevel(logging.WARNING)  # เก็บเฉพาะ WARNING ขึ้นไป
_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))


def install() -> None:
    """Attach ring buffer handler to root logger."""
    logging.getLogger().addHandler(_handler)


def get_entries(level: str | None = None, limit: int = 50) -> list[dict]:
    return _handler.get_entries(level, limit)


def get_stats() -> dict:
    return _handler.stats()
