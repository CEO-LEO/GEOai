"""
trees.py — ปักหมุดต้นไม้รายต้น (spec 6.3 ตัวเลือก) — ฟังก์ชันล้วน ทดสอบได้

หมุดปักจาก GPS ตอนยืนที่ต้น (มือถือคลาดได้หลายเมตร ต้นไม้ห่างกัน 6–10 ม.) → ชื่อ/เลขต้นคือตัวระบุหลัก ตำแหน่งช่วยหา
บันทึกต่อต้น = สิ่งที่ผู้ใช้ทำ/เห็นเอง (อาการ ใส่ปุ๋ย พ่นยา บันทึก) ไม่ใช่คำแนะนำของระบบ · แท็กอาการใช้ชุดเดียวกับรูป
"""

import math
import re
from datetime import date, timedelta

import photo_service

LOG_KINDS_TH = {"symptom": "อาการ", "fertilize": "ใส่ปุ๋ย", "spray": "พ่นยา", "note": "บันทึก"}
MAX_PINS_PER_PLOT = 500
EDGE_TOLERANCE_M = 15        # GPS คลาด: ยอมหมุดที่อยู่นอกขอบเขตไม่เกินนี้
POINT_PLOT_RADIUS_M = 150    # แปลงที่ปักจุดเดียว (ไม่มีขอบเขต)
MAX_PAST_DAYS = 5 * 365
_M_PER_DEG = 111_320


def _xy(lat0: float, lat: float, lng: float) -> tuple[float, float]:
    """พิกัด → เมตรแบบแบนรอบ lat0 (พอสำหรับระยะไม่กี่ร้อยเมตร)"""
    return lng * _M_PER_DEG * math.cos(math.radians(lat0)), lat * _M_PER_DEG


def point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """polygon = [[lng, lat], ...] (รูปแบบเดียวกับ plots.polygon)"""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def distance_to_polygon_m(lat: float, lng: float, polygon: list) -> float:
    px, py = _xy(lat, lat, lng)
    best = math.inf
    n = len(polygon)
    for i in range(n):
        ax, ay = _xy(lat, polygon[i][1], polygon[i][0])
        bx, by = _xy(lat, polygon[(i + 1) % n][1], polygon[(i + 1) % n][0])
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    x1, y1 = _xy(lat1, lat1, lng1)
    x2, y2 = _xy(lat1, lat2, lng2)
    return math.hypot(x2 - x1, y2 - y1)


def within_plot(plot: dict, lat: float, lng: float) -> bool:
    poly = plot.get("polygon")
    if poly and len(poly) >= 3:
        return point_in_polygon(lat, lng, poly) or distance_to_polygon_m(lat, lng, poly) <= EDGE_TOLERANCE_M
    return distance_m(plot["lat"], plot["lng"], lat, lng) <= POINT_PLOT_RADIUS_M


def next_label(labels: list[str]) -> str:
    """ชื่ออัตโนมัติ "ต้นที่ N" ต่อจากเลขที่มากที่สุดในแปลง"""
    nums = [int(m.group(1)) for x in labels if (m := re.fullmatch(r"ต้นที่ (\d+)", (x or "").strip()))]
    return f"ต้นที่ {max(nums, default=0) + 1}"


def validate_log(kind: str, tags: list[str] | None, happened_on: date, today: date) -> list[str]:
    """ตรวจบันทึก — คืนแท็กที่ใช้ได้ · ผิด = ValueError ข้อความไทย"""
    if kind not in LOG_KINDS_TH:
        raise ValueError("ชนิดบันทึกไม่ถูกต้อง")
    if happened_on > today + timedelta(days=1):
        raise ValueError("วันที่อยู่ในอนาคต")
    if happened_on < today - timedelta(days=MAX_PAST_DAYS):
        raise ValueError("วันที่เก่าเกิน 5 ปี")
    clean = photo_service.validate_tags(tags)
    if kind == "symptom" and not clean:
        raise ValueError("เลือกอาการที่เห็นอย่างน้อย 1 อย่าง")
    if kind != "symptom" and clean:
        raise ValueError("แท็กอาการใช้ได้กับบันทึกอาการเท่านั้น")
    return clean


def log_out(row: dict, me: str, plot_owner: str | None) -> dict:
    return {k: row.get(k) for k in ("id", "kind", "happened_on", "tags", "product", "note", "created_at")} | {
        "kind_th": LOG_KINDS_TH.get(row["kind"], row["kind"]),
        "tags_th": [photo_service.TAGS_TH.get(t, t) for t in row.get("tags") or []],
        "by_me": row.get("by_user") == me,
        "can_delete": row.get("by_user") == me or plot_owner == me,
    }


def tree_list(pins: list[dict], logs: list[dict], photo_tree_ids: list[int | None]) -> list[dict]:
    """หมุดของแปลง + บันทึกล่าสุด/จำนวนบันทึก/จำนวนรูป ต่อต้น (เรียงตามชื่อแบบเลขธรรมชาติ)"""
    latest: dict[int, dict] = {}
    count: dict[int, int] = {}
    for lg in logs:
        t = lg["tree_id"]
        count[t] = count.get(t, 0) + 1
        if t not in latest or (lg["happened_on"], lg["created_at"]) > (latest[t]["happened_on"], latest[t]["created_at"]):
            latest[t] = lg
    photos: dict[int, int] = {}
    for t in photo_tree_ids:
        if t is not None:
            photos[t] = photos.get(t, 0) + 1

    def natural(p):
        return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", p.get("label") or "")]
    out = []
    for p in sorted(pins, key=natural):
        last = latest.get(p["id"])
        out.append({k: p.get(k) for k in ("id", "label", "lat", "lng", "accuracy_m", "crop_id", "created_at")} | {
            "logs": count.get(p["id"], 0), "photos": photos.get(p["id"], 0),
            "last": None if not last else {"kind_th": LOG_KINDS_TH.get(last["kind"]), "happened_on": last["happened_on"],
                                           "tags_th": [photo_service.TAGS_TH.get(t, t) for t in last.get("tags") or []]}})
    return out
