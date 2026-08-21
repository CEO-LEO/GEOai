"""
map_image.py — วาดจุดความชื้น/น้ำขังทับบนภาพถ่ายดาวเทียมจริง เป็นรูปภาพเดียว
ส่งเข้า LINE OA คู่กับข้อความผลวิเคราะห์ (ตามที่ผู้ใช้ขอ — อยากเห็นแผนที่ในแชท
ไม่ใช่แค่ตัวเลข/การ์ด)

v2 (ปรับตามฟีดแบ็ก "ดูไม่รู้เรื่องเลย"): เดิมใช้ diverging gradient ต่อเนื่อง
256 เฉด ซึ่งอ่านยากมากสำหรับคนที่ไม่คุ้นแผนที่ความร้อน — ไม่มี legend ในตัวรูป
เลยด้วย (รูปที่ส่งเข้า LINE เป็น static image กดดู tooltip ไม่ได้เหมือนแอป LIFF)
เปลี่ยนมาใช้สี "5 กลุ่มชัดเจน" ตรงกับสถานะที่ backend จำแนกไว้แล้วเป๊ะๆ (status
ใน get_moisture_grid()/​_calc_swab()) แทนการไล่เฉด — ไม่ต้องคิดเลขเทียบสีเอง
แค่ดูว่า "เขียว=ดี แดง/ส้ม=แห้ง ฟ้า/น้ำเงิน=ชื้น/น้ำขัง" ก็เข้าใจได้ทันที แถมสี
ตรงกับคำอธิบายข้อความ (status_th) ที่เกษตรกรเห็นคู่กันอยู่แล้ว ไม่ใช่สเกลใหม่ที่
ต้องมาเรียนรู้เพิ่ม พร้อมชื่อกลุ่ม + แถบหัวเรื่อง/สรุปสถานะ วาดลงในรูปเลย
(ไม่ต้องพึ่ง legend ในแอป เพราะรูปนี้ยืนอิสระในแชท LINE)

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
    for p in grid_points:
        x, y = to_px(p["lat"], p["lng"])
        color = status_color(p.get("status"))
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

    # ── จุดรวมน้ำ (บนสุด) — วงกลมทึบสีทอง ตัดกับทุกอย่างด้านล่างชัดเจน ──────────
    # พอร์ตจาก showMoistureGrid() ฝั่ง LIFF (รอบที่ 3) — ≥3 ทิศทางไหลมารวม (ไม่ใช่ ≥2
    # เหมือนกัน — เกณฑ์เดียวกับเว็บ กันจุดเด่นกระจายเกลื่อนจนเสียความหมาย)
    in_degree = _compute_flow_graph(grid_points, spacing_m)
    ring_radius_px = max(4.0, (tile_size_m * 1.4 / 2) * px_per_m)
    gold = (255, 193, 7)
    sink_count = 0
    for p in grid_points:
        if in_degree.get(id(p), 0) < 3:
            continue
        sink_count += 1
        x, y = to_px(p["lat"], p["lng"])
        draw.ellipse(
            [x - ring_radius_px, y - ring_radius_px, x + ring_radius_px, y + ring_radius_px],
            fill=gold + (217,), outline=gold + (255,), width=2,
        )
    has_flow_arrows = len(used_buckets) > 0

    # เส้นขอบเขตแปลงสีขาว ให้เห็นชัดว่าตรงไหนคือแปลงจริง
    if polygon and len(polygon) >= 3:
        poly_px = [to_px(lat, lng) for lng, lat in polygon]
        draw.line(poly_px + [poly_px[0]], fill=(255, 255, 255, 255), width=max(2, int(w / 200)))

    map_img = Image.alpha_composite(sat_img, overlay).convert("RGB")

    # ── ประกอบภาพสุดท้าย: แถบหัวเรื่อง (บน) + แผนที่ + แถบ legend (ล่าง) ──
    top_h = 92 if plot_name else 66
    # เพิ่มความสูง legend อีกบรรทัดเมื่อมีลูกศร/จุดรวมน้ำให้อธิบาย (ตรงกับเว็บ) —
    # แปลงพื้นที่ราบ/ไม่มีสัญญาณทิศทางน้ำไหลชัดเจนจะไม่มีลูกศรเลย ไม่ต้องเปลืองที่
    show_flow_note = has_flow_arrows or sink_count > 0
    bottom_h = 100 if show_flow_note else 78
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

    # แถบ legend — พื้นขาว สี่เหลี่ยมสี + label ภาษาไทย เรียงแนวนอนตามลำดับ
    # แห้ง → สมดุล → ชื้น ให้อ่านเป็น "สเกล" เดียวกับสีที่เห็นด้านบน
    legend_y0 = top_h + h
    cdraw.rectangle([0, legend_y0, w, legend_y0 + bottom_h], fill=(246, 247, 249))
    cdraw.line([0, legend_y0, w, legend_y0], fill=(224, 226, 230), width=1)

    f_legend = _font("Kanit-Regular.ttf", 15)
    n = len(STATUS_ORDER)
    col_w = w / n
    swatch = 18
    for i, status in enumerate(STATUS_ORDER):
        cx = int(i * col_w + col_w / 2)
        sw_x0 = cx - int(col_w / 2) + 10
        sw_y0 = legend_y0 + 16
        cdraw.rectangle(
            [sw_x0, sw_y0, sw_x0 + swatch, sw_y0 + swatch],
            fill=STATUS_COLORS[status],
        )
        label = STATUS_LABELS_TH[status]
        tb = cdraw.textbbox((0, 0), label, font=f_legend)
        text_w = tb[2] - tb[0]
        max_text_w = col_w - swatch - 24
        # label ยาวเกินคอลัมน์ (จอเล็ก) ตัดขึ้นบรรทัดที่สองแทนล้นทับคอลัมน์ข้างๆ
        if text_w > max_text_w and len(label) > 2:
            mid = len(label) // 2
            cdraw.text((sw_x0 + swatch + 6, sw_y0 - 2), label[:mid], font=f_legend, fill=(51, 51, 51))
            cdraw.text((sw_x0 + swatch + 6, sw_y0 + 16), label[mid:], font=f_legend, fill=(51, 51, 51))
        else:
            cdraw.text((sw_x0 + swatch + 6, sw_y0 + 1), label, font=f_legend, fill=(51, 51, 51))

    # บรรทัดอธิบายลูกศร/จุดรวมน้ำ — ให้ครบตามที่เห็นในเว็บ (ผู้ใช้ขอ)
    # หมายเหตุ: ฟอนต์ Kanit ไม่มี glyph อีโมจิ (ทดสอบแล้วขึ้นเป็นกล่องว่าง) — ▲ เป็น
    # สัญลักษณ์เรขาคณิตธรรมดา (U+25B2) เรนเดอร์ได้ปกติ แต่ 🟡 ต้องวาดเป็นวงกลมจริง
    # ด้วย PIL แทน ไม่ใช้ตัวอักษรอีโมจิ
    if show_flow_note:
        f_note = _font("Kanit-Regular.ttf", 13)
        note_y = legend_y0 + 42
        x_cursor = pad
        if has_flow_arrows:
            text = "▲ ลูกศร = ทิศทางน้ำไหล"
            cdraw.text((x_cursor, note_y), text, font=f_note, fill=(102, 102, 102))
            x_cursor = cdraw.textbbox((x_cursor, note_y), text, font=f_note)[2] + 14
        if sink_count > 0:
            dot_r = 5
            dot_cy = note_y + 8
            cdraw.ellipse([x_cursor, dot_cy - dot_r, x_cursor + dot_r * 2, dot_cy + dot_r], fill=gold)
            x_cursor += dot_r * 2 + 6
            text = f"วงทอง = จุดน้ำรวมมาก ({sink_count} จุด)"
            cdraw.text((x_cursor, note_y), text, font=f_note, fill=(102, 102, 102))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
