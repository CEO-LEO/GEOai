"""
boundary.py — คำเตือนคุณภาพขอบเขตแปลง (docs/v2/boundary-quality-warnings.md, อนุมัติ 2026-10-24)

หลัก: แยกตามสาเหตุ + ดูชนิดพืช/ประเภทสวนประกอบ ไม่ใช่ "ต้นไม้น้อย = วาดผิด"
ถ้อยคำต้องไม่ทำให้เกษตรกรรู้สึกว่าทำผิด (เจ้าของสั่ง) — บอกว่า "ถ้าปรับแบบนี้ ผลจะตรงขึ้น"
ไม่บล็อกการบันทึก: ผู้ใช้กด "บันทึกตามนี้" ได้เสมอ · เกณฑ์อยู่ใน config/boundary_rules.json
แปลงผัก / ยกแปลงผัก / สวนผสมที่มีผัก: ไม่เตือนเรื่องต้นไม้น้อยหรือภาพเห็นเป็นพืชไร่
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import crops

RULES_PATH = Path(__file__).resolve().parent / "config" / "boundary_rules.json"


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _pct(x: float) -> int:
    return int(round(x * 100))


def polygon_area_rai(polygon: list | None) -> float | None:
    """พื้นที่โดยประมาณ (ไร่) แบบระนาบ — สูตรเดียวกับ polygonAreaSqm ใน LIFF ใช้เมื่อ GEE ไม่ตอบ"""
    if not polygon or len(polygon) < 3:
        return None
    lng0, lat0 = polygon[0][0], polygon[0][1]
    m_lat = 111320.0
    m_lng = 111320.0 * math.cos(math.radians(lat0))
    pts = [((p[0] - lng0) * m_lng, (p[1] - lat0) * m_lat) for p in polygon]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    twice = sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1] for i in range(len(pts) - 1))
    return round(abs(twice) / 2 / 1600, 2)


def evaluate(land_cover: dict | None, *, is_point: bool, area_rai: float | None,
             crop_ids: list[str] | None, plot_type: str | None) -> list[dict]:
    """
    land_cover = สัดส่วนประเภทพื้นที่ในขอบเขต (Dynamic World ล่าสุด) เช่น {"trees": .6, "built": .2}
    คืน list คำเตือน [{"code","title","body","primary","secondary"}] เรียงตามความสำคัญ
    """
    r = rules()
    lc = land_cover or {}
    zero = 0.0 if lc else None
    trees, built, field = lc.get("trees", zero), lc.get("built", zero), lc.get("crops", zero)
    veg = crops.is_vegetable_context(crop_ids, plot_type)
    fruit = crops.is_fruit_tree_context(crop_ids)
    out: list[dict] = []

    if is_point:
        out.append({
            "code": "point_only",
            "title": "📍 แปลงนี้ปักไว้จุดเดียว",
            "body": "ระบบจะดูพื้นที่รอบจุดประมาณ 200 เมตร ซึ่งอาจรวมบ้านหรือสวนข้างเคียงด้วย "
                    "ถ้าวาดขอบเขตตามมุมสวน ผลจะตรงกับสวนของคุณมากขึ้น",
            "primary": "✏️ วาดขอบเขต", "secondary": "ใช้จุดเดียวไปก่อน"})
    if area_rai is not None and area_rai < r["min_area_rai"]:
        out.append({
            "code": "too_small",
            "title": "📐 ขอบเขตนี้เล็กมาก (ไม่ถึง 0.1 ไร่)",
            "body": "อาจเกิดจากจิ้มจุดใกล้กันมากเกินไป ลองวาดใหม่ให้ครอบทั้งสวน",
            "primary": "✏️ วาดใหม่", "secondary": "บันทึกตามนี้"})
    if built is not None and built >= r["built_fraction_warn"]:
        out.append({
            "code": "has_buildings",
            "title": f"🏠 ขอบเขตนี้มีบ้านหรือถนนรวมอยู่ประมาณ {_pct(built)}%",
            "body": "ภาพดาวเทียมของบ้านและถนนจะปนเข้ามาในผลของสวน "
                    "ถ้าวาดเฉพาะส่วนที่เป็นสวน ผลจะตรงกับสวนของคุณมากขึ้น",
            "primary": "✏️ วาดใหม่", "secondary": "บันทึกตามนี้"})
    if fruit and not veg:
        tree_min = r["tree_fraction_min_raised_bed"] if plot_type == "raised_bed" else r["tree_fraction_min"]
        if field is not None and field >= r["crops_fraction_major"]:
            out.append({
                "code": "looks_like_field_crops",
                "title": "🌱 ภาพดาวเทียมล่าสุดเห็นพื้นที่นี้คล้ายแปลงพืชไร่หรือผัก",
                "body": "ถ้าเป็นสวนที่เพิ่งปลูกและต้นยังเล็ก บันทึกได้เลย "
                        "ถ้าไม่ใช่ ลองดูอีกครั้งว่าขอบเขตอยู่ตรงสวนหรือไม่",
                "primary": "✏️ วาดใหม่", "secondary": "บันทึกตามนี้"})
        elif trees is not None and trees < tree_min and not is_point:
            out.append({
                "code": "low_tree_cover",
                "title": f"🌳 ภาพดาวเทียมเห็นต้นไม้ในขอบเขตนี้ประมาณ {_pct(trees)}%",
                "body": "ถ้าในขอบเขตมีลานโล่ง บ่อ หรือทางเดินรวมอยู่ด้วย "
                        "การวาดเฉพาะส่วนที่ปลูกต้นไม้จะช่วยให้ผลตรงขึ้น",
                "primary": "✏️ วาดใหม่", "secondary": "บันทึกตามนี้"})
    return out


_SHORT_REASON = {
    "has_buildings": "ขอบเขตมีบ้านหรือถนนรวมอยู่",
    "point_only": "ปักไว้จุดเดียว ยังไม่ได้วาดขอบเขต",
    "too_small": "ขอบเขตเล็กมาก",
    "looks_like_field_crops": "ภาพดาวเทียมเห็นคล้ายแปลงพืชไร่",
    "low_tree_cover": "ภาพดาวเทียมเห็นต้นไม้น้อย",
}


def banner(warnings: list[dict] | None) -> str | None:
    """ข้อความแถบบนการ์ดแปลงเดิม — None ถ้าไม่มีคำเตือน"""
    if not warnings:
        return None
    return f"ℹ️ ผลของแปลงนี้อาจยังไม่ตรงกับสวนทั้งหมด — {_SHORT_REASON.get(warnings[0]['code'], 'ลองปรับขอบเขต')}"


def summarize_for_storage(profile: dict, warnings: list[dict]) -> dict:
    """เก็บใน plots.boundary_check (JSONB)"""
    return {"method": profile.get("method"), "land_cover": profile.get("land_cover"),
            "area_rai": profile.get("area_rai"),
            "warnings": [{"code": w["code"], "title": w["title"]} for w in warnings],
            "banner": banner(warnings)}
