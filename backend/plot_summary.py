"""
plot_summary.py — ประโยคสรุปภาษาคน + คำอธิบายสี จากตารางจุดความชื้นของแปลง (Phase 1e)

เทมเพลตเติมจากข้อมูลจริงเท่านั้น (ไม่ใช้ AI แต่ง): จำนวนจุดตามประเภท + ทิศในแปลง (position_desc)
ใช้สเกลสีเทียบกันเองในแปลงชุดเดียวกับแผนที่ (gee_analysis._plot_relative_normalizer — ตรงกับ LIFF/map_image)
  แล้ววัดห่างจาก "ค่ากลาง (มัธยฐาน) ของแปลง": ชื้นกว่าส่วนอื่น ≥ +0.15 · แห้งกว่าส่วนอื่น ≤ −0.15
  (เกณฑ์เดียวกับการแตะจุดใน LIFF) — ไม่ใช้ศูนย์ของสเกลตรง ๆ เพราะทดสอบกับแปลงจริงแล้ว จุดแห้งไม่กี่จุด
  ทำให้ 83% ของแปลงกลายเป็น "ชื้นกว่าส่วนอื่น" ซึ่งไม่จริง
  จุดที่น้ำไหลมารวม: ≥ 3 จุดรอบข้างไหลเข้า (ตรงกับวงม่วงบนแผนที่)
สวนยกร่อง: ไม่สรุปเรื่องจุดชื้น/จุดน้ำรวม (น้ำในร่อง) — บอกข้อจำกัดแทน
"""

import math
import statistics
from collections import Counter

import gee_analysis

REL_THRESHOLD = 0.15
SINK_MIN_INFLOW = 3
DOMINANT_SHARE = 0.5
MIN_POINTS_TO_COMPARE = 5     # น้อยกว่านี้ (แปลงเล็กมาก) เทียบ "จุดนี้กับส่วนอื่น" ไม่มีความหมาย

COLOR_NOTE_TH = ("สีบนแผนที่เทียบกันเองภายในแปลงนี้: น้ำเงิน = ชื้นกว่าส่วนอื่น · เขียว = กลาง ๆ · "
                 "แดง = แห้งกว่าส่วนอื่น — ไม่ได้บอกว่าแห้งหรือแฉะเกินไปจริง · 1 ช่อง ≈ 10×10 เมตร")
RAISED_BED_SUMMARY_TH = "สวนยกร่อง: ไม่สรุปจุดชื้นและจุดน้ำรวม เพราะน้ำในร่องทำให้ภาพดาวเทียมเห็นเป็นจุดชื้น"


def _bearing(lat1, lng1, lat2, lng2) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lng2 - lng1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def sink_points(points: list[dict], spacing_m: float = 10) -> list[dict]:
    """
    จุดที่น้ำไหลมารวม — ตรรกะเดียวกับ map_image._compute_flow_graph / LIFF computeFlowGraph
    (หาเพื่อนบ้านในทิศที่น้ำไหล ±50° ระยะ ≤ 1.8 ช่อง) แต่จัดกลุ่มจุดเป็นช่องก่อน ไม่วนเทียบทุกคู่
    """
    max_d = (spacing_m / 111320.0) * 1.8
    buckets: dict[tuple[int, int], list[dict]] = {}
    for p in points:
        buckets.setdefault((int(p["lat"] // max_d), int(p["lng"] // max_d)), []).append(p)
    inflow: dict[int, int] = {}
    for p in points:
        ft = p.get("flow_to")
        if not ft:
            continue
        bearing = _bearing(p["lat"], p["lng"], ft["lat"], ft["lng"])
        bi, bj = int(p["lat"] // max_d), int(p["lng"] // max_d)
        best, best_d = None, float("inf")
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for q in buckets.get((bi + di, bj + dj), ()):
                    if q is p:
                        continue
                    d = math.hypot(q["lat"] - p["lat"], q["lng"] - p["lng"])
                    if d > max_d:
                        continue
                    diff = abs(_bearing(p["lat"], p["lng"], q["lat"], q["lng"]) - bearing)
                    if diff > 180:
                        diff = 360 - diff
                    if diff <= 50 and d < best_d:
                        best, best_d = q, d
        if best is not None:
            inflow[id(best)] = inflow.get(id(best), 0) + 1
    return [p for p in points if inflow.get(id(p), 0) >= SINK_MIN_INFLOW]


def _where(group: list[dict]) -> str:
    """
    ทิศที่จุดกลุ่มนี้รวมกันอยู่ — นับแยกแกนเหนือ/ใต้ กับตะวันออก/ตะวันตก จาก position_desc
    (เช่น "มุมเหนือตะวันตก" + "ฝั่งเหนือ" + "มุมเหนือตะวันออก" = ฝั่งเหนือ) แล้วประกอบคำแบบเดียวกับ
    gee_analysis._describe_grid_position
    """
    descs = [p.get("position_desc") or "" for p in group]
    descs = [d for d in descs if d]
    if not descs:
        return ""
    share = lambda word: sum(word in d for d in descs) / len(descs)   # noqa: E731
    ns = next((w for w in ("เหนือ", "ใต้") if share(w) >= DOMINANT_SHARE), "")
    ew = next((w for w in ("ตะวันตก", "ตะวันออก") if share(w) >= DOMINANT_SHARE), "")
    if ns and ew:
        return f"ส่วนใหญ่อยู่มุม{ns}{ew}ของแปลง"
    if ns or ew:
        return f"ส่วนใหญ่อยู่ฝั่ง{ns or ew}ของแปลง"
    if Counter(descs).get("กลางแปลง", 0) / len(descs) >= DOMINANT_SHARE:
        return "ส่วนใหญ่อยู่กลางแปลง"
    return "กระจายหลายส่วนของแปลง"


def summarize(points: list[dict], polygon: list | None = None, plot_type: str | None = None,
              spacing_m: float = 10) -> dict:
    """
    คืน {"summary_th", "lines", "color_note_th", "counts"} — lines = ประโยคย่อยเรียงตามความสำคัญ
    points ต้องมี lat/lng/swab_index (และ flow_to ถ้ามี) · position_desc จะเติมให้ถ้ามี polygon
    """
    raised = plot_type == "raised_bed"
    if not points:
        return {"summary_th": "ยังไม่มีข้อมูลจุดความชื้นของแปลงนี้", "lines": [],
                "color_note_th": COLOR_NOTE_TH, "counts": {}}
    if len(points) < MIN_POINTS_TO_COMPARE:
        return {"summary_th": "แปลงนี้เล็กเกินกว่าจะเทียบความชื้นระหว่างจุดในแปลงได้", "lines": [],
                "color_note_th": COLOR_NOTE_TH, "counts": {"points": len(points)}}
    if polygon and len(polygon) >= 3 and not all(p.get("position_desc") for p in points):
        gee_analysis.assign_grid_reference(points, polygon)
    norm = gee_analysis._plot_relative_normalizer([p.get("swab_index") for p in points])
    rel = {id(p): norm(p.get("swab_index")) for p in points}
    mid = statistics.median(rel.values())
    wet = [p for p in points if rel[id(p)] - mid >= REL_THRESHOLD]
    dry = [p for p in points if rel[id(p)] - mid <= -REL_THRESHOLD]
    sinks = [] if raised else sink_points(points, spacing_m)

    lines: list[str] = []
    if dry:
        lines.append(f"🔴 จุดที่แห้งกว่าส่วนอื่น {len(dry)} จุด {_where(dry)}".strip())
    if wet and not raised:
        lines.append(f"🔵 จุดที่ชื้นกว่าส่วนอื่น {len(wet)} จุด {_where(wet)}".strip())
    if sinks:
        lines.append(f"🟣 จุดที่น้ำจากรอบข้างไหลมารวม {len(sinks)} จุด {_where(sinks)}".strip())
    if raised:
        lines.append(RAISED_BED_SUMMARY_TH)

    if not wet and not dry and not sinks:
        head = "ความชื้นทั่วทั้งแปลงใกล้เคียงกัน ไม่มีส่วนไหนต่างจากส่วนอื่นชัดเจน"
    elif raised and not dry:
        head = "ไม่พบส่วนที่แห้งกว่าส่วนอื่นชัดเจน"
    else:
        head = "บางส่วนของแปลงชื้นหรือแห้งต่างจากส่วนอื่น — แตะจุดสีบนแผนที่เพื่อดูวิธีดูแล"
    return {"summary_th": head, "lines": lines, "color_note_th": COLOR_NOTE_TH,
            "counts": {"points": len(points), "wet": 0 if raised else len(wet), "dry": len(dry),
                       "sinks": len(sinks)}}


def short_text(s: dict) -> str:
    """ข้อความเดียวสำหรับการ์ด LINE (ไม่ยาวเกิน)"""
    return "\n".join([s["summary_th"], *s["lines"]])


def image_headline(s: dict) -> str:
    """หัวรูปแผนที่ (สั้น ไม่มีอีโมจิ — ฟอนต์ในรูปไม่มี glyph) ใช้แทนหัวจากสูตรเดิมในโหมดกำลังปรับปรุง"""
    c = s.get("counts") or {}
    parts = []
    if c.get("dry"):
        parts.append(f"แห้งกว่าส่วนอื่น {c['dry']} จุด")
    if c.get("wet"):
        parts.append(f"ชื้นกว่าส่วนอื่น {c['wet']} จุด")
    if c.get("sinks"):
        parts.append(f"น้ำไหลมารวม {c['sinks']} จุด")
    if parts:
        return " · ".join(parts)
    return "ความชื้นใกล้เคียงกันทั้งแปลง" if c.get("points", 0) >= MIN_POINTS_TO_COMPARE else s["summary_th"]