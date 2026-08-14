"""
map_image.py — วาดจุดความชื้น/น้ำขังทับบนภาพถ่ายดาวเทียมจริง เป็นรูปภาพเดียว
ส่งเข้า LINE OA คู่กับข้อความผลวิเคราะห์ (ตามที่ผู้ใช้ขอ — อยากเห็นแผนที่ในแชท
ไม่ใช่แค่ตัวเลข/การ์ด)

สีและสูตร normalize ที่นี่ต้อง "ตรงกับ" swabColor()/buildSwabNormalizer() ใน
liff/index.html เป๊ะ — คนละภาษา (Python vs JS) แต่ต้องให้สีที่เห็นในรูปที่ส่งเข้า LINE
กับสีที่เห็นในแอป LIFF ตรงกัน ไม่งั้นเกษตรกรจะสับสนว่าทำไมสีไม่ตรงกัน
"""

import io
import math

from PIL import Image, ImageDraw

# ── ต้องตรงกับ SWAB_GRADIENT_STOPS ใน liff/index.html เป๊ะ ──────────────
SWAB_GRADIENT_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (-0.45, (183, 28, 28)),    # แดงเข้ม — แล้งจัดสุด
    (-0.30, (229, 57, 53)),    # แดง — เกณฑ์แล้งวิกฤต
    (-0.15, (251, 140, 0)),    # ส้ม — เกณฑ์แห้งเกิน
    (0.00,  (67, 160, 71)),    # เขียว — สมดุลดี
    (0.10,  (41, 182, 246)),   # ฟ้า — เกณฑ์ชื้นเกิน
    (0.30,  (21, 101, 192)),   # น้ำเงิน — เกณฑ์น้ำขังวิกฤต
    (0.45,  (13, 71, 161)),    # น้ำเงินเข้ม — น้ำขังวิกฤตสุด
]


def swab_color(swab_index: float | None) -> tuple[int, int, int]:
    """ไล่เฉดต่อเนื่อง (diverging) ตาม swab_index — พอร์ตตรงจาก swabColor() ฝั่ง JS"""
    v = max(-0.45, min(0.45, swab_index if swab_index is not None else 0.0))
    for i in range(len(SWAB_GRADIENT_STOPS) - 1):
        v0, c0 = SWAB_GRADIENT_STOPS[i]
        v1, c1 = SWAB_GRADIENT_STOPS[i + 1]
        if v <= v1:
            t = (v - v0) / (v1 - v0)
            return tuple(round(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return (153, 153, 153)


def build_normalizer(values: list[float | None]):
    """ยืดช่วงสีให้เต็ม scale ตามข้อมูลจริงของแปลงนี้ — พอร์ตตรงจาก buildSwabNormalizer()"""
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


def render_plot_grid_image(
    satellite_png_bytes: bytes,
    bounds: tuple[float, float, float, float],
    grid_points: list[dict],
    polygon: list[list[float]],
    spacing_m: float = 10,
) -> bytes:
    """
    วาดจุดความชื้น (สีเดียวกับ LIFF) + เส้นขอบเขตแปลง ทับบนภาพถ่ายดาวเทียม
    bounds = (min_lng, min_lat, max_lng, max_lat) ของขอบเขตภาพจริง (จาก
    get_plot_satellite_thumbnail) — ใช้แปลง lat/lng ของแต่ละจุด → พิกเซล
    (แปลงแบบเชิงเส้นตรงๆ พอสำหรับพื้นที่เล็กระดับแปลงเกษตร ไม่ต้องคิดโค้งโลก)
    polygon เป็น [[lng,lat], ...] ตาม convention เดียวกับที่ endpoint อื่นใช้ทั้งระบบ
    """
    img = Image.open(io.BytesIO(satellite_png_bytes)).convert("RGBA")
    w, h = img.size
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

    values = [p.get("swab_index") for p in grid_points]
    normalize = build_normalizer(values)

    for p in grid_points:
        x, y = to_px(p["lat"], p["lng"])
        color = swab_color(normalize(p.get("swab_index")))
        draw.rectangle(
            [x - tile_half_px, y - tile_half_px, x + tile_half_px, y + tile_half_px],
            fill=color + (210,),
        )

    # เส้นขอบเขตแปลงสีขาว ให้เห็นชัดว่าตรงไหนคือแปลงจริง
    if polygon and len(polygon) >= 3:
        poly_px = [to_px(lat, lng) for lng, lat in polygon]
        draw.line(poly_px + [poly_px[0]], fill=(255, 255, 255, 255), width=max(2, int(w / 200)))

    combined = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    combined.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
