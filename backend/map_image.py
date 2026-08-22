"""
map_image.py — วาดจุดความชื้น/น้ำขังทับบนภาพถ่ายดาวเทียมจริง เป็นรูปภาพเดียว
ส่งเข้า LINE OA คู่กับข้อความผลวิเคราะห์ (ตามที่ผู้ใช้ขอ — อยากเห็นแผนที่ในแชท
ไม่ใช่แค่ตัวเลข/การ์ด)

v2 (ปรับตามฟีดแบ็ก "ดูไม่รู้เรื่องเลย"): เดิมใช้ diverging gradient ต่อเนื่อง
256 เฉด ซึ่งอ่านยากมากสำหรับคนที่ไม่คุ้นแผนที่ความร้อน — ไม่มี legend ในตัวรูป
เลยด้วย (รูปที่ส่งเข้า LINE เป็น static image กดดู tooltip ไม่ได้เหมือนแอป LIFF)
เปลี่ยนมาใช้สี "5 กลุ่มชัดเจน" ตรงกับสถานะที่ backend จำแนกไว้แล้วเป๊ะๆ

v3 (ผู้ใช้ขอ "ทำสีในไลน์ให้เหมือนกับเว็บ อ้างอิงจากเว็บ"): พอไปเทียบจริงๆ พบว่า
liff/index.html (showMoistureGrid) ไม่เคยถูกอัปเดตตาม v2 เลย ยังใช้ diverging
gradient + contrast-stretch ต่อเนื่อง (swabColor/buildSwabNormalizer) อยู่แบบ
เดิม — สองที่เลยมีสีไม่ตรงกันมาตลอด พอร์ตกลับมาใช้ต่อเนื่อง+ยืด scale ตามเว็บ
เป๊ะๆ แทน (เว็บทำแบบนี้เพราะแปลงจริงส่วนใหญ่ค่าจะกระจุกแคบๆ ในช่วงเดียว สีคงที่ 5
กลุ่มเลยมักโชว์สีเดียวทั้งภาพทั้งที่ยังมีความต่างจริงอยู่ — ตัวอย่างจริงที่ผู้ใช้เจอ:
สวน 744 จุด swab_index 0.121-0.536 ทุกจุด อยู่ในกลุ่ม "ชื้นเกิน/น้ำขังวิกฤต" หมด
ไม่มีจุดไหนแตะ "สมดุลดี" เลยจริงๆ — สีคงที่เลยเป็นฟ้า/น้ำเงินล้วน) ส่วนหัวเรื่อง/
สรุปสถานะรวมยังใช้ status/status_th ที่ backend จำแนกไว้เป๊ะเหมือนเดิม (ข้อความ
ยังบอกความรุนแรงจริงตรงไปตรงมา) เปลี่ยนแค่ "สีของกระเบื้อง" ให้ตรงกับเว็บ

ฟอนต์: PIL ไม่มีฟอนต์ไทยในตัว ใช้ Kanit (SIL Open Font License, ฟรี redistribute
ได้) เก็บไว้ที่ backend/assets/fonts/ — ต้อง bundle เพราะ default font ของ PIL
วาดตัวอักษรไทยไม่ได้เลย (ขึ้นเป็นกล่องว่าง)
"""

import io
import math
import os

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)


# ── ต้อง "ตรงกับ" status/status_th ที่ _calc_swab() ใน gee_analysis.py คำนวณ
# ไว้แล้วเป๊ะ — ใช้ค่าที่ backend จำแนกมาโดยตรง ไม่คำนวณ threshold ซ้ำที่นี่ กัน
# สอง module ตีความ swab_index ไม่ตรงกัน (บทเรียนจากบั๊ก requirements.txt ที่
# ไฟล์ซ้ำสองที่แล้วหลุดซิงก์กัน — logic การจำแนกก็เสี่ยงแบบเดียวกันถ้าซ้ำ)
STATUS_ORDER = ["drought", "dry", "optimal", "wet", "waterlogged"]

STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    "drought":     (183, 28, 28),   # แดงเข้ม
    "dry":         (251, 140, 0),   # ส้ม
    "optimal":     (67, 160, 71),   # เขียว
    "wet":         (41, 182, 246),  # ฟ้า
    "waterlogged": (13, 71, 161),   # น้ำเงินเข้ม
}

STATUS_LABELS_TH: dict[str, str] = {
    "drought":     "แล้งวิกฤต",
    "dry":         "แห้งเกิน",
    "optimal":     "สมดุลดี",
    "wet":         "ชื้นเกิน",
    "waterlogged": "น้ำขังวิกฤต",
}

_DEFAULT_COLOR = (153, 153, 153)


def status_color(status: str | None) -> tuple[int, int, int]:
    return STATUS_COLORS.get(status or "", _DEFAULT_COLOR)


# ── ไล่สีต่อเนื่อง (diverging) — ต้องตรงกับ SWAB_GRADIENT_STOPS ใน liff/index.html
# เป๊ะทุก anchor (นี่คือแหล่งอ้างอิง — ผู้ใช้ขอให้สีในรูปที่ส่งเข้า LINE ตรงกับเว็บ)
SWAB_GRADIENT_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (-0.45, (183, 28, 28)),    # #b71c1c แดงเข้ม — แล้งจัดสุด
    (-0.30, (229, 57, 53)),    # #e53935 แดง — เกณฑ์แล้งวิกฤต
    (-0.15, (251, 140, 0)),    # #fb8c00 ส้ม — เกณฑ์แห้งเกิน
    (0.00,  (67, 160, 71)),    # #43a047 เขียว — สมดุลดี
    (0.10,  (41, 182, 246)),   # #29b6f6 ฟ้า — เกณฑ์ชื้นเกิน
    (0.30,  (21, 101, 192)),   # #1565c0 น้ำเงิน — เกณฑ์น้ำขังวิกฤต
    (0.45,  (13, 71, 161)),    # #0d47a1 น้ำเงินเข้ม — น้ำขังวิกฤตสุด
]


def swab_color(swab_index: float | None) -> tuple[int, int, int]:
    """ไล่เฉดต่อเนื่องตาม swab_index — พอร์ตตรงจาก swabColor() ใน liff/index.html"""
    v = max(-0.45, min(0.45, swab_index if swab_index is not None else 0.0))
    for i in range(len(SWAB_GRADIENT_STOPS) - 1):
        v0, c0 = SWAB_GRADIENT_STOPS[i]
        v1, c1 = SWAB_GRADIENT_STOPS[i + 1]
        if v <= v1:
            t = (v - v0) / (v1 - v0)
            return tuple(round(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return _DEFAULT_COLOR


def build_normalizer(values: list[float | None]):
    """
    ยืดช่วงสีให้เต็ม scale ตามข้อมูลจริงของแปลงนี้ (contrast stretch) — พอร์ตตรงจาก
    buildSwabNormalizer() ใน liff/index.html เป๊ะ รวม MIN_RANGE กันช่วงข้อมูลแคบ
    เกินจนยืดจน noise เด่นเกินจริงด้วย
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return lambda v: v if v is not None else 0.0
    lo, hi = min(vals), min(0.45, max(vals))
    MIN_RANGE = 0.12
    if hi - lo < MIN_RANGE:
        mid = (hi + lo) / 2
        lo, hi = mid - MIN_RANGE / 2, mid + MIN_RANGE / 2

    def normalize(v):
        if v is None:
            return 0.0
        t = (v - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
        return -0.45 + t * 0.9

    return normalize


def _offset_latlng(lat: float, lng: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """
    หาพิกัดปลายทางเมื่อเดินจาก (lat,lng) ไปทาง bearing_deg (0=เหนือ ตามเข็มนาฬิกา)
    เป็นระยะ distance_m เมตร — พอร์ตตรงจาก offsetLatLng() ใน liff/index.html

    ไม่ import gee_analysis._offset_latlng() (ตัวเดียวกันเป๊ะ) มาใช้ซ้ำ เพราะ
    map_image.py ต้อง import ได้อิสระแม้ ee/gee_analysis พัง (ดู try/except รอบ
    import Pillow ใน plot_image_service.py — จุดประสงค์คือแยก failure domain ของ
    ฟีเจอร์รูปภาพออกจาก GEE) เป็นแค่สูตรเรขาคณิตล้วนๆ ไม่ใช่ threshold ทางธุรกิจที่
    เสี่ยง "หลุดซิงก์" ทางความหมายถ้าซ้ำ เลยพอรับความเสี่ยง duplicate ได้ตรงนี้
    """
    R = 6371000.0
    brng = math.radians(bearing_deg)
    lat_rad = math.radians(lat)
    d_lat = (distance_m * math.cos(brng)) / R * (180 / math.pi)
    d_lng = (distance_m * math.sin(brng)) / (R * math.cos(lat_rad)) * (180 / math.pi)
    return lat + d_lat, lng + d_lng


def _bearing_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """มุมทิศทาง (0=เหนือ ตามเข็มนาฬิกา) จากจุด 1 ไปจุด 2 — พอร์ตจาก bearingBetween() ฝั่ง JS"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lng2 - lng1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compute_flow_graph(points: list[dict], spacing_m: float) -> dict[int, int]:
    """
    คืน dict id(point) → in_degree (จำนวนจุดอื่นที่มีทิศทางน้ำไหลมารวมที่จุดนี้)
    พอร์ตตรงจาก computeFlowGraph() ใน liff/index.html — ใช้หา "จุดรวมน้ำ" (sink)
    สำหรับวงกลมสีทองบนแผนที่ (จุดที่ ≥3 ทิศทางไหลมารวม เหมาะขุดร่องระบายน้ำที่สุด)
    """
    max_dist_deg = (spacing_m / 111320.0) * 1.8
    in_degree: dict[int, int] = {id(p): 0 for p in points}
    for p in points:
        flow_to = p.get("flow_to")
        if not flow_to:
            continue
        bearing = _bearing_between(p["lat"], p["lng"], flow_to["lat"], flow_to["lng"])
        best, best_dist = None, float("inf")
        for q in points:
            if q is p:
                continue
            dist = math.hypot(q["lat"] - p["lat"], q["lng"] - p["lng"])
            if dist > max_dist_deg:
                continue
            q_bearing = _bearing_between(p["lat"], p["lng"], q["lat"], q["lng"])
            diff = abs(q_bearing - bearing)
            if diff > 180:
                diff = 360 - diff
            if diff <= 50 and dist < best_dist:
                best_dist = dist
                best = q
        if best is not None:
            in_degree[id(best)] = in_degree.get(id(best), 0) + 1
    return in_degree


def summarize_grid_status(grid_points: list[dict]) -> tuple[str, tuple[int, int, int]]:
    """
    สรุปภาพรวมทั้งแปลงเป็นประโยคเดียว + สี สำหรับแถบหัวเรื่อง — เลือกสถานะที่
    "รุนแรงที่สุด" ที่พบ (ไม่ใช่ค่าเฉลี่ย) เพราะจุดปัญหาเล็กๆ ในแปลงที่ส่วนใหญ่ปกติ
    ก็ยังสำคัญ ไม่อยากให้ค่าเฉลี่ยกลบจุดที่ต้องรีบดูแลไป
    """
    counts: dict[str, int] = {}
    for p in grid_points:
        st = p.get("status")
        if st:
            counts[st] = counts.get(st, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return "ไม่มีข้อมูลเพียงพอ", _DEFAULT_COLOR

    # ลำดับความรุนแรง: น้ำขัง/แล้งวิกฤต > ชื้นเกิน/แห้งเกิน > สมดุลดี
    severity_order = ["waterlogged", "drought", "wet", "dry", "optimal"]
    worst = next((s for s in severity_order if counts.get(s)), "optimal")

    if worst == "optimal":
        return "ทั้งแปลงชื้นสมดุลดี", STATUS_COLORS["optimal"]

    n = counts[worst]
    label = STATUS_LABELS_TH[worst]
    pct = round(100 * n / total)
    return f"พบจุด{label} {n} จุด ({pct}% ของแปลง)", STATUS_COLORS[worst]


# ป้ายกริดอ้างอิง (A1, B2, ...) — พิมพ์ลงขอบรูปให้ตรงกับป้ายที่การ์ดไลน์อ้างถึงใน
# "จุดที่พบปัญหาในแปลง" (ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้") พอร์ตตรงจาก
# gee_analysis._grid_reference_dims()/_grid_ref_bounds() — เป็นสูตรเรขาคณิตล้วนๆ
# จาก polygon ตรงๆ ไม่ใช่ threshold ทางธุรกิจ พอรับความเสี่ยง duplicate ได้เหมือน
# _offset_latlng/_bearing_between ข้างบน (เหตุผลเดียวกัน — แยก failure domain)
GRID_REF_TARGET_CELLS = 12
GRID_REF_MIN_DIM = 2
GRID_REF_MAX_DIM = 5


def _grid_reference_dims(polygon: list[list[float]]) -> tuple[int, int]:
    lngs = [pt[0] for pt in polygon]
    lats = [pt[1] for pt in polygon]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    mid_lat = (min_lat + max_lat) / 2
    width_m  = max(1.0, (max_lng - min_lng) * 111320 * math.cos(math.radians(mid_lat)))
    height_m = max(1.0, (max_lat - min_lat) * 110540)
    aspect = width_m / height_m

    cols = round((GRID_REF_TARGET_CELLS * aspect) ** 0.5)
    rows = round(GRID_REF_TARGET_CELLS / max(1, cols))
    cols = max(GRID_REF_MIN_DIM, min(GRID_REF_MAX_DIM, cols))
    rows = max(GRID_REF_MIN_DIM, min(GRID_REF_MAX_DIM, rows))
    return rows, cols


def render_plot_grid_image(
    satellite_png_bytes: bytes,
    bounds: tuple[float, float, float, float],
    grid_points: list[dict],
    polygon: list[list[float]],
    spacing_m: float = 10,
    plot_name: str = "",
) -> bytes:
    """
    วาดจุดความชื้น (สีตามกลุ่มสถานะ 5 กลุ่ม สอดคล้อง status_th ที่ backend
    จำแนกไว้แล้ว) + เส้นขอบเขตแปลง ทับบนภาพถ่ายดาวเทียม แล้วเติมแถบหัวเรื่อง
    (สรุปสถานะรวม) ด้านบน + แถบคำอธิบายสี (legend) ด้านล่าง ให้อ่านเข้าใจได้
    จากรูปเดียว ไม่ต้องเปิดแอปเทียบสี

    bounds = (min_lng, min_lat, max_lng, max_lat) ของขอบเขตภาพจริง (จาก
    get_plot_satellite_thumbnail) — ใช้แปลง lat/lng ของแต่ละจุด → พิกเซล
    (แปลงแบบเชิงเส้นตรงๆ พอสำหรับพื้นที่เล็กระดับแปลงเกษตร ไม่ต้องคิดโค้งโลก)
    polygon เป็น [[lng,lat], ...] ตาม convention เดียวกับที่ endpoint อื่นใช้ทั้งระบบ
    """
    sat_img = Image.open(io.BytesIO(satellite_png_bytes)).convert("RGBA")
    w, h = sat_img.size
    min_lng, min_lat, max_lng, max_lat = bounds

    def to_px(lat: float, lng: float) -> tuple[float, float]:
        x = (lng - min_lng) / (max_lng - min_lng) * w
        y = (max_lat - lat) / (max_lat - min_lat) * h   # y กลับด้าน (ภาพนับจากบนลงล่าง)
        return x, y

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ประมาณ meters-per-pixel จากความกว้างจริงของภาพ (ที่ละติจูดกึ่งกลาง) เพื่อกะขนาด
    # กระเบื้องให้ใกล้เคียงสัดส่วนจริงบนพื้นดิน (เหมือน tileSizeM ฝั่ง LIFF)
    mid_lat = (min_lat + max_lat) / 2
    lng_span_m = (max_lng - min_lng) * 111320 * math.cos(math.radians(mid_lat))
    px_per_m = (w / lng_span_m) if lng_span_m > 1e-6 else 1.0
    tile_half_px = max(2.0, (spacing_m * 1.15 / 2) * px_per_m)

    # เส้นตารางบางๆ คั่นระหว่างกระเบื้อง — ผู้ใช้ฟีดแบ็กว่าตอนกระเบื้องสีติดกัน
    # (โดยเฉพาะโซนสีเดียวกันเป็นแพ) มันเบลอเป็นก้อนสีเดียว มองไม่ออกว่าจริงๆ คือ
    # ตาราง 10×10 ม. แยกจุด แยกกัน — เติมเส้นขอบสีขาวโปร่งแสงรอบทุกกระเบื้อง
    # ให้เห็นเป็น "ตาราง" ชัดเจนแทนก้อนสีเรียบ (เหมือน grid line ใน spreadsheet)
    grid_line_w = max(1, min(4, round(px_per_m * spacing_m * 0.02)))
    normalize = build_normalizer([p.get("swab_index") for p in grid_points])
    for p in grid_points:
        x, y = to_px(p["lat"], p["lng"])
        color = swab_color(normalize(p.get("swab_index")))
        # ทึบสี (ไม่เกลี่ย alpha) — สีกลุ่มไม่ต้องไล่เฉด ทึบยิ่งแยกกลุ่มชัดกว่า
        draw.rectangle(
            [x - tile_half_px, y - tile_half_px, x + tile_half_px, y + tile_half_px],
            fill=color + (235,),
            outline=(255, 255, 255, 150),
            width=grid_line_w,
        )

    # ── ลูกศรทิศทางน้ำไหล (แบบห่างๆ ไม่ใช่ทุกจุด กันรกทับกระเบื้อง) ──────────
    # พอร์ตจาก showMoistureGrid() ฝั่ง LIFF (รอบที่ 2) — ผู้ใช้ขอให้รูปที่ส่งเข้า
    # LINE มีข้อมูลชุดเดียวกับที่เห็นในเว็บ ไม่ใช่แค่สีกระเบื้องเฉยๆ
    tile_size_m = spacing_m * 1.12
    chevron_bucket_deg = (spacing_m * 2.5) / 111320.0
    used_buckets: set[tuple[int, int]] = set()
    for p in grid_points:
        flow_to = p.get("flow_to")
        if not flow_to:
            continue
        key = (round(p["lat"] / chevron_bucket_deg), round(p["lng"] / chevron_bucket_deg))
        if key in used_buckets:
            continue
        used_buckets.add(key)
        bearing = _bearing_between(p["lat"], p["lng"], flow_to["lat"], flow_to["lng"])
        size_m = tile_size_m * 0.8
        tip = _offset_latlng(p["lat"], p["lng"], bearing, size_m)
        back_l = _offset_latlng(p["lat"], p["lng"], (bearing + 150) % 360, size_m * 0.7)
        back_r = _offset_latlng(p["lat"], p["lng"], (bearing + 210) % 360, size_m * 0.7)
        chevron_px = [to_px(*tip), to_px(*back_l), to_px(*back_r)]
        draw.polygon(chevron_px, fill=(38, 50, 56, 217), outline=(255, 255, 255, 255), width=2)

    # ── จุดรวมน้ำ (บนสุด) — วงกลมทึบสีม่วง ตัดกับทุกอย่างด้านล่างชัดเจน ──────────
    # พอร์ตจาก showMoistureGrid() ฝั่ง LIFF (รอบที่ 3) — ≥3 ทิศทางไหลมารวม (ไม่ใช่ ≥2
    # เหมือนกัน — เกณฑ์เดียวกับเว็บ กันจุดเด่นกระจายเกลื่อนจนเสียความหมาย)
    #
    # สีเดิม #ffc107 (ทอง) ใกล้เคียงกับสี "แห้งเกิน" ในกลุ่มสถานะ (#FB8C00) มากไป
    # ผู้ใช้ทดสอบจริงแยกแยะไม่ออกในรูปที่ส่งเข้า LINE — เปลี่ยนเป็นม่วง (ไม่ซ้ำกับ
    # 5 สีสถานะเลยสักสี กัน confuse) ต้องเปลี่ยนพร้อมกันทั้งที่นี่และ
    # liff/index.html (sphere.Dot lineColor + legend text) ให้สีตรงกันข้ามแพลตฟอร์ม
    in_degree = _compute_flow_graph(grid_points, spacing_m)
    ring_radius_px = max(4.0, (tile_size_m * 1.4 / 2) * px_per_m)
    sink_color = (171, 71, 188)   # #AB47BC ม่วง — ต้องตรงกับ liff/index.html เป๊ะ
    sink_count = 0
    for p in grid_points:
        if in_degree.get(id(p), 0) < 3:
            continue
        sink_count += 1
        x, y = to_px(p["lat"], p["lng"])
        draw.ellipse(
            [x - ring_radius_px, y - ring_radius_px, x + ring_radius_px, y + ring_radius_px],
            fill=sink_color + (217,), outline=sink_color + (255,), width=2,
        )
    has_flow_arrows = len(used_buckets) > 0

    # เส้นขอบเขตแปลงสีขาว ให้เห็นชัดว่าตรงไหนคือแปลงจริง
    if polygon and len(polygon) >= 3:
        poly_px = [to_px(lat, lng) for lng, lat in polygon]
        draw.line(poly_px + [poly_px[0]], fill=(255, 255, 255, 255), width=max(2, int(w / 200)))

    # ── ป้ายกริดอ้างอิง (A1, B2, ...) ── ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้" ใน
    # การ์ดไลน์ (ดู flex_messages._build_problem_points_section) — ป้ายชุดนี้ต้อง
    # ตรงกับที่การ์ดอ้างถึงเป๊ะ ถึงจะเทียบรูปกับข้อความได้จริง ใช้ bounding box ของ
    # polygon ตรงๆ (ไม่ใช่ bounds ที่มี buffer) ให้ตรงกับสูตรฝั่ง gee_analysis.py
    if polygon and len(polygon) >= 3:
        ref_rows, ref_cols = _grid_reference_dims(polygon)
        p_lngs = [pt[0] for pt in polygon]
        p_lats = [pt[1] for pt in polygon]
        ref_min_lng, ref_max_lng = min(p_lngs), max(p_lngs)
        ref_min_lat, ref_max_lat = min(p_lats), max(p_lats)

        line_color = (255, 255, 255, 110)
        for c in range(1, ref_cols):
            lng_b = ref_min_lng + (ref_max_lng - ref_min_lng) * c / ref_cols
            x, _ = to_px(ref_min_lat, lng_b)
            draw.line([(x, 0), (x, h)], fill=line_color, width=1)
        for r in range(1, ref_rows):
            lat_b = ref_max_lat - (ref_max_lat - ref_min_lat) * r / ref_rows
            _, y = to_px(lat_b, ref_min_lng)
            draw.line([(0, y), (w, y)], fill=line_color, width=1)

        # หมายเหตุ: จงใจไม่ใช้ anchor=/stroke_width= ของ draw.text() (ใหม่กว่า/พึ่ง
        # ตัว layout engine มากกว่า) — ทดสอบจริงบน production แล้วป้ายไม่ขึ้นเลย
        # (เงียบๆ ไม่มี exception ด้วย) ทั้งที่ทำงานถูกต้องทุกอย่างตอนรันโลคัล
        # (สงสัยว่า Pillow build บน Render ต่างจากเครื่อง dev) เปลี่ยนมาวัดตำแหน่ง
        # เองด้วย textbbox() + วาดกล่องพื้นเข้มรองหลังแทนเส้นขอบ (stroke) — ใช้ API
        # พื้นฐานเดียวกับที่ _legend_dot_label ในไฟล์นี้ใช้อยู่แล้วและพิสูจน์แล้วว่า
        # เสถียรบน production จริง
        f_ref = _font("Kanit-Bold.ttf", max(13, int(w / 32)))

        def _draw_ref_label(cx: float, cy: float, text: str):
            tb = draw.textbbox((0, 0), text, font=f_ref)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            tx, ty = cx - tw / 2 - tb[0], cy - th / 2 - tb[1]
            draw.rectangle([tx - 4, ty - 3, tx + tw + 4, ty + th + 3], fill=(0, 0, 0, 150))
            draw.text((tx, ty), text, font=f_ref, fill=(255, 255, 255, 255))

        for c in range(ref_cols):
            lng_mid = ref_min_lng + (ref_max_lng - ref_min_lng) * (c + 0.5) / ref_cols
            x, _ = to_px(ref_min_lat, lng_mid)
            _draw_ref_label(x, 16, str(c + 1))
        for r in range(ref_rows):
            lat_mid = ref_max_lat - (ref_max_lat - ref_min_lat) * (r + 0.5) / ref_rows
            _, y = to_px(lat_mid, ref_min_lng)
            _draw_ref_label(16, y, chr(65 + r))

    map_img = Image.alpha_composite(sat_img, overlay).convert("RGB")

    # ── ประกอบภาพสุดท้าย: แถบหัวเรื่อง (บน) + แผนที่ + แถบ legend (ล่าง) ──
    top_h = 92 if plot_name else 66
    # เพิ่มความสูง legend ทีละบรรทัดเมื่อมีลูกศร/จุดรวมน้ำให้อธิบาย (ตรงกับเว็บ) และ/
    # หรือมีป้ายกริดอ้างอิงให้อธิบาย (มีทุกครั้งที่มี polygon — ผู้ใช้ขอ "บอกจุดที่
    # เกิดปัญหา" ในการ์ด ป้ายในรูปนี้คือกุญแจอ่านการ์ดนั้น ต้องมีคำอธิบายกำกับเสมอ)
    show_flow_note = has_flow_arrows or sink_count > 0
    has_grid_ref = bool(polygon and len(polygon) >= 3)
    bottom_h = 78 + (22 if show_flow_note else 0) + (22 if has_grid_ref else 0)
    canvas = Image.new("RGB", (w, top_h + h + bottom_h), (255, 255, 255))
    cdraw = ImageDraw.Draw(canvas)

    # แถบหัวเรื่อง — พื้นเข้ม ตัวอักษรขาว/สีตามสถานะ อ่านชัดตัดกับพื้นเสมอ
    # (ไม่พึ่งความสว่างของภาพดาวเทียมด้านล่างที่เปลี่ยนไปตามแต่ละแปลง)
    cdraw.rectangle([0, 0, w, top_h], fill=(30, 38, 51))
    headline, headline_color = summarize_grid_status(grid_points)
    pad = 16
    if plot_name:
        f_title = _font("Kanit-Bold.ttf", 22)
        f_sub = _font("Kanit-Regular.ttf", 17)
        cdraw.text((pad, 12), f"แผนที่ความชื้น — {plot_name}", font=f_title, fill=(255, 255, 255))
        cdraw.text((pad, 46), headline, font=f_sub, fill=headline_color)
    else:
        f_sub = _font("Kanit-Bold.ttf", 20)
        cdraw.text((pad, (top_h - 24) // 2), headline, font=f_sub, fill=headline_color)

    canvas.paste(map_img, (0, top_h))

    # แถบ legend — ไล่สีต่อเนื่อง (ตรงกับกระเบื้องด้านบนเป๊ะ ใช้ swab_color() ตัว
    # เดียวกัน) + label 3 จุดอ้างอิง (แห้งสุด/ปานกลาง/แฉะสุด) — สไตล์เดียวกับ
    # .legend-gradient-bar ในเว็บ (ผู้ใช้ขอให้สีในรูปอ้างอิงจากเว็บ)
    legend_y0 = top_h + h
    cdraw.rectangle([0, legend_y0, w, legend_y0 + bottom_h], fill=(246, 247, 249))
    cdraw.line([0, legend_y0, w, legend_y0], fill=(224, 226, 230), width=1)

    bar_x0, bar_x1 = pad, w - pad
    bar_y0, bar_y1 = legend_y0 + 14, legend_y0 + 24
    bar_w = max(1, bar_x1 - bar_x0)
    for i in range(bar_w):
        v = -0.45 + (i / bar_w) * 0.9
        cdraw.line([(bar_x0 + i, bar_y0), (bar_x0 + i, bar_y1)], fill=swab_color(v))
    cdraw.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], outline=(0, 0, 0))

    # หมายเหตุ: ฟอนต์ Kanit ไม่มี glyph อีโมจิ (ทดสอบแล้วขึ้นเป็นกล่องว่าง) — วาดจุดสี
    # จริงด้วย PIL แทนอีโมจิสี (🔴🟢🔵) เหมือนที่แก้ไว้กับจุดรวมน้ำ (วงม่วง) ก่อนหน้านี้
    f_legend = _font("Kanit-Regular.ttf", 13)
    label_y = bar_y1 + 8
    dot_r = 4

    def _legend_dot_label(x: int, text: str, color: tuple[int, int, int], align: str = "left"):
        tb = cdraw.textbbox((0, 0), text, font=f_legend)
        text_w = tb[2] - tb[0]
        total_w = dot_r * 2 + 5 + text_w
        if align == "center":
            x = x - total_w // 2
        elif align == "right":
            x = x - total_w
        cdraw.ellipse([x, label_y + 2, x + dot_r * 2, label_y + 2 + dot_r * 2], fill=color)
        cdraw.text((x + dot_r * 2 + 5, label_y), text, font=f_legend, fill=(51, 51, 51))

    _legend_dot_label(bar_x0, "จุดแห้งสุด", SWAB_GRADIENT_STOPS[0][1], align="left")
    _legend_dot_label((bar_x0 + bar_x1) // 2, "ปานกลาง", SWAB_GRADIENT_STOPS[3][1], align="center")
    _legend_dot_label(bar_x1, "จุดแฉะสุด", SWAB_GRADIENT_STOPS[-1][1], align="right")

    # บรรทัดอธิบายลูกศร/จุดรวมน้ำ — ให้ครบตามที่เห็นในเว็บ (ผู้ใช้ขอ)
    f_note = _font("Kanit-Regular.ttf", 13)
    note_y = label_y + 24
    if show_flow_note:
        x_cursor = pad
        if has_flow_arrows:
            text = "▲ ลูกศร = ทิศทางน้ำไหล"
            cdraw.text((x_cursor, note_y), text, font=f_note, fill=(102, 102, 102))
            x_cursor = cdraw.textbbox((x_cursor, note_y), text, font=f_note)[2] + 14
        if sink_count > 0:
            dot_r = 5
            dot_cy = note_y + 8
            cdraw.ellipse([x_cursor, dot_cy - dot_r, x_cursor + dot_r * 2, dot_cy + dot_r], fill=sink_color)
            x_cursor += dot_r * 2 + 6
            text = f"วงม่วง = จุดน้ำรวมมาก ({sink_count} จุด)"
            cdraw.text((x_cursor, note_y), text, font=f_note, fill=(102, 102, 102))
        note_y += 22

    # บรรทัดอธิบายป้ายกริดอ้างอิง — คู่กับ "จุดที่พบปัญหาในแปลง" ในการ์ดไลน์ (ป้าย
    # ตัวอักษร+เลขต้องตรงกันเป๊ะระหว่างรูปนี้กับข้อความในการ์ด ดู _grid_reference_dims)
    if has_grid_ref:
        # หมายเหตุ: ห้ามใช้อีโมจิในข้อความนี้ — ฟอนต์ Kanit ไม่มี glyph อีโมจิ
        # (ขึ้นเป็นกล่องว่าง ดูคอมเมนต์บนสุดของไฟล์)
        cdraw.text((pad, note_y), "ตัวอักษร+เลข = ตำแหน่งอ้างอิง (ดูจุดที่พบปัญหาในข้อความ)",
                   font=f_note, fill=(102, 102, 102))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def make_list_thumbnail(satellite_png: bytes, size: int = 160) -> bytes:
    """
    ครอปภาพถ่ายดาวเทียมจริง (ตัวเดียวกับที่ render_plot_grid_image ใช้เป็นพื้นหลัง
    — ไม่เรียก GEE เพิ่ม) ให้เป็นสี่เหลี่ยมจัตุรัสเล็กๆ สำหรับใช้เป็นรูปย่อของแปลงใน
    หน้า "แปลงของฉัน" (ผู้ใช้ขอ "ใส่รูป map บอกขนาด/ตำแหน่งแปลง" ช่วยแยกแปลงที่หน้า
    ตาคล้ายกันในลิสต์) — ครอปกึ่งกลางเป็นจัตุรัส (ภาพต้นทางเป็นสี่เหลี่ยมผืนผ้าตาม
    bounds จริงของแปลง) แล้วย่อลงเหลือ size×size พิกเซล เก็บเป็นไฟล์เล็กพอส่งเร็ว
    """
    img = Image.open(io.BytesIO(satellite_png)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img  = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
