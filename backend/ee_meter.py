"""
ee_meter.py — วัดการใช้ Earth Engine จริง (EECU-วินาที) ต่อการวิเคราะห์ (แผนทุน TED Fund — earth-engine-cost.md)

ปิดเป็นค่าเริ่มต้น เปิดด้วย env EE_USAGE_METER=1 ชั่วคราว (เก็บ ~7 วันแล้วปิด) เพราะแต่ละงานที่วัดมีคำขอ Earth Engine เพิ่ม 1 ครั้ง
(ดึงโปรไฟล์) และเขียนแถวลงตาราง ee_usage_log (migrate_v25)

วิธีวัด: ee.data.profiling ขอโปรไฟล์ทุกคำขอภายในงาน แล้ว Profile.getProfiles คืนตาราง "EECU·s PeakMem Count Description"
EECU-วินาทีรวม = ผลรวมคอลัมน์แรกทุกแถว (ตรวจกับตัวอย่างจริงแล้ว: แต่ละแถวเป็นหมวดงานที่ไม่ซ้อนกัน)
ครอบคลุมเฉพาะงานที่ห่อด้วย @metered(...) (ฟังก์ชันระดับบนของงานวิเคราะห์) — งานซ้อนในงานที่วัดอยู่แล้วไม่นับซ้ำ

บันทึกลง DB แบบ best-effort: ล้มเหลวเงียบ ๆ ไม่ให้กระทบงานวิเคราะห์ ห้ามเก็บพิกัด/รหัสผู้ใช้ (เก็บแค่ชื่องาน เวลา และ EECU)
"""

import functools
import logging
import os
import re
import statistics
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

_tl = threading.local()
_UNITS = {"": 1.0, "k": 1e3, "M": 1e6}
_ROW = re.compile(r"^\s*(\d+(?:\.\d+)?)([kM]?)\s+\S+\s+\d+\s+\S", re.M)


def enabled() -> bool:
    return os.environ.get("EE_USAGE_METER", "").strip().lower() in ("1", "true", "yes")


def parse_eecu_seconds(profile_text: str) -> float:
    """ผลรวมคอลัมน์ EECU·s ของตารางโปรไฟล์ (หัวตาราง/บรรทัดอื่นไม่ตรงรูปแบบจึงถูกข้ามเอง)"""
    return sum(float(m.group(1)) * _UNITS[m.group(2)] for m in _ROW.finditer(profile_text or ""))


def _fetch_profile_text(ids: list[str]) -> str:
    import ee
    get_profiles = ee.ApiFunction.lookup("Profile.getProfiles").call     # ซ่อนอยู่ ต้องเรียกตรง ๆ (เหมือน ee.profilePrinting)
    last = None
    for _ in range(3):
        try:
            return get_profiles(ids=ids).getInfo()
        except Exception as e:           # ชั่วคราวได้ — ลองใหม่
            last = e
            time.sleep(0.5)
    raise last


def _store(row: dict) -> None:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return
    import httpx
    httpx.post(f"{url}/rest/v1/ee_usage_log", timeout=5, json=row,
               headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                        "Prefer": "return=minimal"})


def record(op: str, profile_ids: list[str], wall_s: float, ok: bool) -> float | None:
    """ดึงโปรไฟล์ → คำนวณ EECU → เก็บ · คืนค่า EECU (ไว้ทดสอบ/สคริปต์วัด) หรือ None ถ้าวัดไม่ได้"""
    if not profile_ids:
        return None
    try:
        eecu = parse_eecu_seconds(_fetch_profile_text(profile_ids))
        _store({"op": op, "eecu_seconds": round(eecu, 4), "wall_seconds": round(wall_s, 3),
                "profile_calls": len(profile_ids), "ok": ok})
        return eecu
    except Exception as e:
        logger.warning(f"ee_meter record failed ({op}): {type(e).__name__}: {str(e)[:120]}")
        return None


def metered(op: str):
    """ห่อฟังก์ชัน (sync, รันในเธรด) ที่เรียก Earth Engine — เปิดเมื่อ EE_USAGE_METER=1 เท่านั้น งานซ้อนไม่นับซ้ำ"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled() or getattr(_tl, "depth", 0) > 0:
                return fn(*args, **kwargs)
            import ee
            ids: list[str] = []
            started, ok = time.time(), True
            _tl.depth = 1
            try:
                with ee.data.profiling(ids.append):
                    return fn(*args, **kwargs)
            except Exception:
                ok = False
                raise
            finally:
                _tl.depth = 0
                record(op, ids, time.time() - started, ok)
        return wrapper
    return deco


# ── สรุปสำหรับหน้า admin / เอกสารต้นทุน (ฟังก์ชันล้วน) ──────────────────────────
# งานที่เกิดต่อ "แปลงต่อวัน" ในสแกนรายวัน (scheduler.daily_scan_job): วิเคราะห์หลัก + วิธีวัดใหม่ + ตารางจุด
DAILY_SCAN_OPS = ("analyze", "wetness_anomaly", "moisture_grid")
WEEKLY_SCAN_OPS = ("risk_profile",)          # คำนวณเฉพาะวันสรุปรายสัปดาห์ (÷ 7 ต่อวัน)


def _p95(vals: list[float]) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def summarize(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ok", True) and r.get("eecu_seconds") is not None:
            by[r["op"]].append(r)
    ops = {}
    for op, rs in sorted(by.items()):
        e = [float(r["eecu_seconds"]) for r in rs]
        ops[op] = {"n": len(e), "mean_eecu_s": round(statistics.fmean(e), 3), "p95_eecu_s": round(_p95(e), 3),
                   "max_eecu_s": round(max(e), 3), "total_eecu_s": round(sum(e), 2),
                   "mean_wall_s": round(statistics.fmean(float(r.get("wall_seconds") or 0) for r in rs), 2)}
    daily = {}
    for r in rows:
        if r.get("ok", True) and r.get("eecu_seconds") is not None and r.get("created_at"):
            d = str(r["created_at"])[:10]
            daily[d] = round(daily.get(d, 0.0) + float(r["eecu_seconds"]), 2)
    per_plot_day = (sum(ops[o]["mean_eecu_s"] for o in DAILY_SCAN_OPS if o in ops)
                    + sum(ops[o]["mean_eecu_s"] for o in WEEKLY_SCAN_OPS if o in ops) / 7)
    return {"ops": ops, "per_plot_day_eecu_s": round(per_plot_day, 3) if per_plot_day else None,
            "per_plot_day_ops": [o for o in DAILY_SCAN_OPS + WEEKLY_SCAN_OPS if o in ops],
            "total_eecu_hours": round(sum(daily.values()) / 3600, 4), "by_day_eecu_s": dict(sorted(daily.items())),
            "note_th": "EECU-วินาที = ผลรวมโปรไฟล์ Earth Engine ของงานที่ห่อด้วยตัววัด · ต่อแปลงต่อวัน = ค่าเฉลี่ยของ "
                       "analyze + wetness_anomaly + moisture_grid + risk_profile÷7 (งานในสแกนรายวัน/รายสัปดาห์) · ไม่รวมงานที่ผู้ใช้กดเอง"}
