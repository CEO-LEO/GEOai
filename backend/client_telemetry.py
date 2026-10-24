"""
client_telemetry.py — เวลาเปิดแอปและ error จากหน้า LIFF (Phase 1b)

- kind="timing": เวลาแต่ละขั้นตอนตอนเปิดแอป (ms นับจากเริ่มโหลดสคริปต์) เช่น
  liff_init / plots / map / ready — ใช้วัดผลการปรับความเร็วบนมือถือจริงใน LINE
- kind="error": error ที่เกิดใน LIFF (spec ข้อ 2.5 — "ข้อผิดพลาดใน LIFF ต้องส่งกลับมาดูได้")
  ถูกเขียนลง log ระดับ WARNING ด้วย จึงเห็นใน /admin/logs

ไม่เก็บข้อมูลส่วนบุคคล (ไม่มี userId/พิกัด) เก็บในแรมล่าสุด MAX_EVENTS รายการ (หายเมื่อ restart)
"""

import logging
import statistics
import time
from collections import deque
from typing import Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

MAX_EVENTS = 1000
_events: deque[dict] = deque(maxlen=MAX_EVENTS)


class ClientEvent(BaseModel):
    kind: Literal["timing", "error"]
    marks: dict[str, int] | None = None
    cached: bool | None = None
    tab: str | None = Field(None, max_length=20)
    message: str | None = Field(None, max_length=300)
    source: str | None = Field(None, max_length=200)
    line: int | None = Field(None, ge=0, le=10_000_000)
    app_version: str | None = Field(None, max_length=20)

    @field_validator("marks")
    @classmethod
    def _check_marks(cls, v):
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("too many marks")
        for k, ms in v.items():
            if len(k) > 30 or not (0 <= ms <= 300_000):
                raise ValueError("invalid mark")
        return v


def record(event: ClientEvent, user_agent: str = "") -> None:
    ua = "line" if "Line/" in user_agent else "browser"
    platform = "ios" if ("iPhone" in user_agent or "iPad" in user_agent) else (
        "android" if "Android" in user_agent else "other")
    item = {"t": time.time(), "ua": ua, "platform": platform, **event.model_dump(exclude_none=True)}
    _events.append(item)
    if event.kind == "error":
        logger.warning(f"LIFF client error ({platform}/{ua}, v{event.app_version}): "
                       f"{event.message} @ {event.source}:{event.line}")


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def summary(since_s: int = 7 * 86400) -> dict:
    cutoff = time.time() - since_s
    recent = [e for e in _events if e["t"] >= cutoff]
    timings = [e for e in recent if e["kind"] == "timing"]
    by_mark: dict[str, list[int]] = {}
    for e in timings:
        for k, ms in (e.get("marks") or {}).items():
            by_mark.setdefault(k, []).append(ms)
    groups: dict[str, dict] = {}
    for key in sorted({(e.get("app_version"), e["platform"]) for e in timings}, key=str):
        ready = [e["marks"]["ready"] for e in timings
                 if (e.get("app_version"), e["platform"]) == key and "ready" in (e.get("marks") or {})]
        groups[f"{key[0]}/{key[1]}"] = {"count": len(ready), "ready_p50_ms": _pct(ready, 0.5),
                                         "ready_p90_ms": _pct(ready, 0.9)}
    return {
        "timing_count": len(timings),
        "marks_ms": {k: {"p50": _pct(v, 0.5), "p90": _pct(v, 0.9),
                         "mean": round(statistics.fmean(v)) if v else None}
                     for k, v in by_mark.items()},
        "by_version_platform": groups,
        "errors": [{k: e.get(k) for k in ("t", "platform", "app_version", "message", "source", "line")}
                   for e in recent if e["kind"] == "error"][-30:],
    }


def reset_for_tests() -> None:
    _events.clear()
