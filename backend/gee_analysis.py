"""
GEE Analysis Module
ดึงข้อมูลดาวเทียม Sentinel-1, Sentinel-2, และ SRTM DEM
สำหรับวิเคราะห์สวนต้นไม้จากพิกัด Lat/Long ที่รับมา

v2: เพิ่ม Land Displacement Detection, Fertilizer Recommendation, Yield Estimation
"""

import time
import math
import logging
from datetime import datetime, timedelta, timezone
import ee
import httpx
from cache import get_cached, set_cached
from rule_engine import predict_yield, calculate_root_rot_risk, compute_risk_level
try:
    from ml_model import predict_from_ml as _predict_from_ml
    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False

logger = logging.getLogger(__name__)

# Flag ถูก set เป็น True หลัง ee.Initialize() สำเร็จ (main.py)
_gee_ready = False

# ─── Retry config ────────────────────────────────────
MAX_RETRIES    = 3
BACKOFF_BASE_S = 2  # exponential: 2s, 4s, 8s

# Buffer รัศมีรอบจุดที่ปักหมุด (เมตร)
PLOT_BUFFER_M       = 100   # พื้นที่แปลง
SURROUNDING_BUFFER_M = 500  # รอบข้างสำหรับเปรียบเทียบ elevation

# ─── ค่าอ้างอิงสำหรับต้นไม้ ─────────────────────────
# ผลผลิตฐาน (กก./ไร่/ปี) — ต้นไม้อายุ 8+ ปี สภาพดี
# (ชื่อตัวแปร DURIAN_* คงไว้ตามเดิม — เปลี่ยนแล้วต้องตามแก้ทุกจุดที่ใช้ เสี่ยงพลาด
# โดยไม่ได้ประโยชน์กับผู้ใช้จริง คำอธิบาย/ข้อความที่ผู้ใช้เห็นแก้ให้แล้ว)
DURIAN_BASE_YIELD_KG_PER_RAI = 1500
# NDVI อ้างอิง (ต้นไม้สุขภาพดีเต็มที่)
DURIAN_OPTIMAL_NDVI = 0.70
# จำนวนต้นโดยเฉลี่ยต่อไร่ (ระยะ 8×8 ม.)
DURIAN_TREES_PER_RAI = 25


# ─── Mock / Dev mode analysis ────────────────────────

def _mock_analyze(lat: float, lng: float) -> dict:
    """
    สร้างผลวิเคราะห์จำลองจาก lat/lng โดยไม่ต้องเรียก GEE
    ใช้ hash ของพิกัดเพื่อให้ค่าออกมาคงที่สำหรับจุดเดิม (deterministic)
    """
    # deterministic seed จากพิกัด (ปัดทศนิยม 4 ตำแหน่ง ~11 ม.)
    seed = abs(hash((round(lat, 4), round(lng, 4))))
    r = lambda lo, hi: lo + (seed % 10000) / 10000 * (hi - lo)

    # แปลง seed ย่อยสำหรับแต่ละตัวแปร
    s1 = abs(hash(seed + 1))
    s2 = abs(hash(seed + 2))
    s3 = abs(hash(seed + 3))
    s4 = abs(hash(seed + 4))
    s5 = abs(hash(seed + 5))
    r1 = lambda lo, hi: lo + (s1 % 10000) / 10000 * (hi - lo)
    r2 = lambda lo, hi: lo + (s2 % 10000) / 10000 * (hi - lo)
    r3 = lambda lo, hi: lo + (s3 % 10000) / 10000 * (hi - lo)
    r4 = lambda lo, hi: lo + (s4 % 10000) / 10000 * (hi - lo)
    r5 = lambda lo, hi: lo + (s5 % 10000) / 10000 * (hi - lo)

    ndvi_now = round(r(0.25, 0.75), 3)
    ndvi_prev = round(r1(0.30, 0.72), 3)
    ndvi_change = round(ndvi_now - ndvi_prev, 3)
    soil_moisture_vv = round(r2(-18.0, -8.0), 2)
    elevation = round(r3(20.0, 150.0), 1)
    elevation_diff = round(r4(-3.0, 2.0), 1)
    bsi  = round(r(0.0, 0.4), 3)
    ndwi = round(r5(-0.35, 0.25), 3)   # MNDWI mock — neg=dry veg, pos=wet surface

    if bsi > 0.2 and abs(elevation_diff) > 1.5:
        topsoil_risk_level = "high"
    elif bsi > 0.2:
        topsoil_risk_level = "medium"
    else:
        topsoil_risk_level = "low"

    # ── Displacement (mock SAR) ──
    vv_change = round(r1(-3.5, 3.5), 2)
    vh_change = round(r2(-2.5, 2.5), 2)
    stability = round(max(0.0, min(1.0, 0.3 + r3(0.0, 0.65))), 2)
    abs_vv = abs(vv_change)
    if abs_vv >= 3.0 or stability < 0.3:
        change_level = "high"
    elif abs_vv >= 1.5 or stability < 0.5:
        change_level = "medium"
    else:
        change_level = "low"

    displacement = {
        "vv_change_db": vv_change,
        "vh_change_db": vh_change,
        "surface_stability": stability,
        "change_level": change_level,
    }

    # ── ใช้ฟังก์ชันจริงในการคำนวณ (logic อยู่ใน pure python ไม่ต้องใช้ GEE) ──
    fertilizer = _recommend_fertilizer(ndvi_now, ndvi_change, soil_moisture_vv, elevation_diff)
    yield_est = _estimate_yield(ndvi_now, ndvi_change, soil_moisture_vv, elevation_diff, bsi)
    land_impact = _assess_land_impact(displacement, elevation_diff, soil_moisture_vv, ndvi_change)

    # ── Soil Water-Air Balance (v3) ──
    swab = _calc_swab(soil_moisture_vv, bsi, elevation_diff, ndwi)

    # ── Yield forecast (v4): ใช้ yield_estimate (rule-based + BSI) เป็นค่าหลักเสมอ ──
    # เดิมใช้โมเดล ML (RandomForest) เป็นหลัก แต่ backtest จริง 20 ตัวอย่างพบว่าทายสูงกว่า
    # yield_estimate อย่างมีระบบ (+7.8% ถึง +68%, เฉลี่ย +37%) เพราะเทรนจาก logic คนละสูตร
    # (predict_yield แบบขั้นบันได) บนข้อมูลจำลองล้วนๆ — ปิดใช้งานไว้ก่อน ไม่ลบโค้ด/โมเดล
    # เผื่อ retrain ด้วยข้อมูลจริงจาก field_observations ในอนาคตแล้วนำกลับมาใช้
    # ml_yield = _predict_from_ml(ndvi_now, bsi, elevation_diff,
    #                             swab["swab_index"], swab["soil_water_pct"]) if _ML_AVAILABLE else -1
    predicted_yield = yield_est["estimated_kg_per_rai"]

    # ── Mock SAR wetness streak (Gap 4) ──
    ws_seed = abs(hash(seed + 6)) % 10
    wetness_streak = {
        "wetness_days": ws_seed,
        "is_prolonged": ws_seed >= 6,
    }

    result = {
        "lat": lat,
        "lng": lng,
        "ndvi_now": ndvi_now,
        "ndvi_prev": ndvi_prev,
        "ndvi_change": ndvi_change,
        "soil_moisture_vv": soil_moisture_vv,
        "elevation": elevation,
        "elevation_diff": elevation_diff,
        "bsi": bsi,
        "topsoil_risk_level": topsoil_risk_level,
        "predicted_yield_kg_per_rai": predicted_yield,
        "displacement": displacement,
        "fertilizer": fertilizer,
        "yield_estimate": yield_est,
        "land_impact": land_impact,
        "swab":        swab,
        "wetness_streak": wetness_streak,
    }
    result["root_rot_risk"] = calculate_root_rot_risk(result)
    # จุดคำนวณเดียวของ "ระดับความเสี่ยงรวม" — ทุกที่ (scheduler, LINE flex, ข้อความ
    # LINE, dashboard, LIFF) ควรอ่าน field นี้แทนคำนวณเอง (ดู formula-audit)
    result["overall_risk_level"] = compute_risk_level(result)
    logger.info(f"[MOCK] analyze ({lat:.4f}, {lng:.4f}) → NDVI={ndvi_now}, "
                f"displacement={change_level}, yield={yield_est['estimated_kg_per_rai']}kg, "
                f"root_rot={result['root_rot_risk']['level']}")
    set_cached(lat, lng, result)
    return result


def get_sar_wetness_streak(lat: float, lng: float) -> dict:
    """
    Gap 4: ดึง Sentinel-1 VV backscatter ย้อนหลัง 30 วัน
    นับจำนวนภาพติดต่อกัน (newest→oldest) ที่ VV > -10 dB → wetness_days
    is_prolonged = True ถ้า wetness_days >= 6
    """
    if not _gee_ready:
        seed = abs(hash((round(lat, 4), round(lng, 4)))) % 10
        return {"wetness_days": seed, "is_prolonged": seed >= 6}

    today = datetime.today()
    start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")
    point = ee.Geometry.Point([lng, lat])
    area  = point.buffer(PLOT_BUFFER_M)

    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(area)
        .filterDate(start, end)
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .select("VV")
        .sort("system:time_start")
    )

    def to_feat(img):
        val = img.reduceRegion(ee.Reducer.mean(), area, 10).get("VV")
        return ee.Feature(None, {"vv": val})

    try:
        fc = _retry_gee(s1.map(to_feat).getInfo)
        vals = [
            f["properties"].get("vv")
            for f in fc.get("features", [])
        ]
        # Count consecutive wet images (newest → oldest)
        streak = 0
        for v in reversed(vals):
            if v is not None and v > -10:
                streak += 1
            else:
                break
        # Each S1 pass ≈ 6 calendar days
        wetness_days = streak * 6
    except Exception as e:
        logger.warning(f"SAR wetness streak failed: {e}")
        wetness_days = 0

    return {"wetness_days": wetness_days, "is_prolonged": wetness_days >= 6}


# ─── WeatherNext (Google DeepMind AI forecast, via Earth Engine) ─────
# ต้องขอสิทธิ์เข้าถึง dataset ก่อน (WeatherNext Data Request form) —
# ถ้ายังไม่ได้รับอนุมัติ ee.ImageCollection(...) หรือ getInfo() จะ raise
# ee.EEException (permission denied) — ให้ caller (weather_alert.get_7day_rain)
# จับแล้ว fallback ไป Open-Meteo แทน ไม่ต้อง retry เพราะ permission error
# ไม่ใช่ปัญหาชั่วคราวที่ retry แล้วจะหาย
WEATHERNEXT_COLLECTION = "projects/gcp-public-data-weathernext/assets/weathernext_2_0_0_mean"


def get_weathernext_rain_forecast(lat: float, lng: float, days: int = 7) -> dict:
    """
    พยากรณ์ฝนล่วงหน้าจาก Google DeepMind WeatherNext 2 (ensemble mean, ผ่าน Earth Engine)
    คืนรูปแบบเดียวกับ weather_alert.WeatherForecast

    ความละเอียดพื้นที่: 0.25° (~27.8 กม./จุด) — หยาบกว่า point-forecast ทั่วไปมาก
    เหมาะกับแนวโน้มฝนระดับภูมิภาค ไม่ใช่พยากรณ์รายจุดละเอียด อัปเดตทุก 6 ชม.
    (00/06/12/18 UTC) ล่วงหน้าได้ถึง 15 วัน

    ฟังก์ชัน sync/blocking (เรียก GEE) — ผู้เรียกต้องห่อด้วย run_in_threadpool
    เหมือนฟังก์ชัน GEE อื่นๆ ไม่งั้นจะบล็อก event loop
    """
    if not _gee_ready:
        raise RuntimeError("GEE not initialized — WeatherNext unavailable")

    point = ee.Geometry.Point([lng, lat])

    # หา init time ล่าสุดที่น่าจะเผยแพร่แล้ว (ย้อนหลังอย่างน้อย 6 ชม. จากรอบ
    # 6-ชม.ปัจจุบัน กันกรณี Google ยังประมวลผล/เผยแพร่ไม่เสร็จ)
    now = datetime.now(timezone.utc)
    latest_init = (now - timedelta(hours=(now.hour % 6) + 6)).replace(
        minute=0, second=0, microsecond=0)
    init_str = latest_init.strftime("%Y-%m-%dT%H:00:00Z")

    coll = (
        ee.ImageCollection(WEATHERNEXT_COLLECTION)
        .filter(ee.Filter.eq("start_time", init_str))
        .filter(ee.Filter.lte("forecast_hour", days * 24))
        .select(["total_precipitation_6hr"])
    )

    def to_feat(img):
        val = img.reduceRegion(ee.Reducer.mean(), point, 27830).get("total_precipitation_6hr")
        return ee.Feature(None, {"precip_m": val, "forecast_hour": img.get("forecast_hour")})

    fc = _retry_gee(coll.map(to_feat).getInfo)
    steps = fc.get("features", [])
    if not steps:
        raise RuntimeError(f"WeatherNext: no forecast steps for init_time={init_str}")

    # รวมเป็นรายวัน (4 ช่วง 6 ชม./วัน) แปลงหน่วย เมตร → มม.
    daily_totals: dict[int, float] = {}
    for feat in steps:
        props = feat["properties"]
        fh = props.get("forecast_hour")
        precip_mm = (props.get("precip_m") or 0.0) * 1000.0
        if fh is None:
            continue
        day_idx = (int(fh) - 1) // 24
        daily_totals[day_idx] = daily_totals.get(day_idx, 0.0) + precip_mm

    daily_rain = [round(daily_totals.get(i, 0.0), 1) for i in range(days)]
    total      = sum(daily_rain)
    max_daily  = max(daily_rain) if daily_rain else 0.0
    rainy_days = sum(1 for v in daily_rain if v > 5.0)

    return {
        "total_rain_mm": round(total, 1),
        "max_daily_mm":  round(max_daily, 1),
        "rainy_days":    rainy_days,
        "is_heavy_rain": total >= 60.0 or max_daily >= 40.0,
        "source":        "weathernext",
        "init_time":     init_str,
    }


def _retry_gee(fn, *args, **kwargs):
    """
    Wrap a GEE call with exponential backoff retry.
    Retries on ee.EEException (server-side / transient errors).
    """
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            # Only retry on GEE-specific exceptions
            if type(exc).__name__ != "EEException" and not isinstance(exc, ee.EEException):
                raise
            last_exc = exc
            wait = BACKOFF_BASE_S ** attempt
            logger.warning(
                f"GEE call failed (attempt {attempt + 1}/{MAX_RETRIES}): {exc} "
                f"— retrying in {wait}s"
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    logger.error(f"GEE call failed after {MAX_RETRIES} retries: {last_exc}")
    raise last_exc


def _safe_getInfo(computation, default=None):
    """getInfo() with retry + fallback to default on failure."""
    try:
        val = _retry_gee(computation.getInfo)
        return float(val) if val is not None else default
    except Exception:
        logger.warning(f"getInfo failed even after retries, using default={default}")
        return default


def analyze_durian_plot(lat: float, lng: float,
                        polygon: list[list[float]] | None = None) -> dict:
    """
    วิเคราะห์สวนต้นไม้จากพิกัดที่ให้มา
    ถ้ามี polygon → ใช้ ee.Geometry.Polygon แทน buffer รอบจุด
    ตรวจ cache ก่อน — ถ้า hit คืนค่าทันที ไม่เรียก GEE
    ถ้า GEE ยังไม่ init → ใช้ mock data
    """
    cached = get_cached(lat, lng)
    if cached:
        return cached

    if not _gee_ready:
        logger.info("GEE not ready — using mock analysis")
        return _mock_analyze(lat, lng)

    point = ee.Geometry.Point([lng, lat])

    if polygon and len(polygon) >= 3:
        plot_area = ee.Geometry.Polygon([polygon])
    else:
        plot_area = point.buffer(PLOT_BUFFER_M)

    surrounding_area = point.buffer(SURROUNDING_BUFFER_M)

    today       = datetime.today()
    end_date    = today.strftime("%Y-%m-%d")
    start_date  = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    # ช่วงปีก่อน (สำหรับ baseline NDVI)
    start_prev  = (today - timedelta(days=730)).strftime("%Y-%m-%d")
    end_prev    = (today - timedelta(days=365)).strftime("%Y-%m-%d")

    ndvi_now, ndvi_prev         = _get_ndvi(plot_area, start_date, end_date, start_prev, end_prev)
    soil_moisture_vv            = _get_soil_moisture(plot_area, start_date, end_date)
    elevation, elevation_diff   = _get_elevation(plot_area, surrounding_area)
    bsi                         = _get_bsi(plot_area, start_date, end_date)
    ndwi                        = _get_ndwi(plot_area, start_date, end_date)

    if bsi > 0.2 and abs(elevation_diff) > 1.5:
        topsoil_risk_level = "high"
    elif bsi > 0.2:
        topsoil_risk_level = "medium"
    else:
        topsoil_risk_level = "low"

    # ── v2: Land displacement (SAR temporal change) ──
    displacement = _get_land_displacement(plot_area, start_date, end_date, start_prev, end_prev)

    ndvi_change = round(ndvi_now - ndvi_prev, 3)

    # ── v2: Fertilizer recommendation ──
    fertilizer = _recommend_fertilizer(ndvi_now, ndvi_change, soil_moisture_vv, elevation_diff)

    # ── v2: Yield estimation (v4: เพิ่ม BSI) ──
    yield_est = _estimate_yield(ndvi_now, ndvi_change, soil_moisture_vv, elevation_diff, bsi)

    # ── v2: Impact analysis from land changes ──
    land_impact = _assess_land_impact(displacement, elevation_diff, soil_moisture_vv, ndvi_change)

    # ── v3: Soil Water-Air Balance ──
    swab = _calc_swab(soil_moisture_vv, bsi, elevation_diff, ndwi)

    # ── Yield forecast (v4): ใช้ yield_estimate (rule-based + BSI) เป็นค่าหลักเสมอ ──
    # เดิมใช้โมเดล ML (RandomForest) เป็นหลัก แต่ backtest จริง 20 ตัวอย่างพบว่าทายสูงกว่า
    # yield_estimate อย่างมีระบบ (+7.8% ถึง +68%, เฉลี่ย +37%) เพราะเทรนจาก logic คนละสูตร
    # (predict_yield แบบขั้นบันได) บนข้อมูลจำลองล้วนๆ — ปิดใช้งานไว้ก่อน ไม่ลบโค้ด/โมเดล
    # เผื่อ retrain ด้วยข้อมูลจริงจาก field_observations ในอนาคตแล้วนำกลับมาใช้
    # ml_yield = _predict_from_ml(ndvi_now, bsi, elevation_diff,
    #                             swab["swab_index"], swab["soil_water_pct"]) if _ML_AVAILABLE else -1
    predicted_yield = yield_est["estimated_kg_per_rai"]

    # ── Gap 4: SAR wetness streak ──
    wetness_streak = get_sar_wetness_streak(lat, lng)

    result = {
        "lat": lat,
        "lng": lng,
        "ndvi_now":         round(ndvi_now, 3),
        "ndvi_prev":        round(ndvi_prev, 3),
        "ndvi_change":      ndvi_change,
        "soil_moisture_vv": round(soil_moisture_vv, 2),
        "elevation":        round(elevation, 1),
        "elevation_diff":   round(elevation_diff, 1),
        "bsi":              round(bsi, 3),
        "topsoil_risk_level": topsoil_risk_level,
        "predicted_yield_kg_per_rai": predicted_yield,
        # v2 fields
        "displacement":     displacement,
        "fertilizer":       fertilizer,
        "yield_estimate":   yield_est,
        "land_impact":      land_impact,
        # v3 fields
        "swab":             swab,
        "wetness_streak":   wetness_streak,
    }
    result["root_rot_risk"] = calculate_root_rot_risk(result)
    # จุดคำนวณเดียวของ "ระดับความเสี่ยงรวม" — ทุกที่ (scheduler, LINE flex, ข้อความ
    # LINE, dashboard, LIFF) ควรอ่าน field นี้แทนคำนวณเอง (ดู formula-audit)
    result["overall_risk_level"] = compute_risk_level(result)
    set_cached(lat, lng, result)
    return result


# ─────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────

def _get_ndvi(area, start_now, end_now, start_prev, end_prev):
    """คำนวณ NDVI เฉลี่ยจาก Sentinel-2 ช่วงปัจจุบัน และปีก่อน"""

    def collection(start, end):
        return (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(area)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
        )

    def mean_ndvi(col):
        img = col.mean()
        return _safe_getInfo(
            img.reduceRegion(ee.Reducer.mean(), area, 10).get("NDVI"),
            default=0.0,
        )

    return mean_ndvi(collection(start_now, end_now)), mean_ndvi(collection(start_prev, end_prev))


def _get_soil_moisture(area, start_date, end_date):
    """
    ดึงค่า Backscatter VV จาก Sentinel-1 (proxy ของความชื้นดิน)
    ค่า VV สูง (ใกล้ 0 dB) = ชื้นมาก / น้ำขัง
    ค่า VV ต่ำ (ต่ำกว่า -15 dB) = แห้ง
    """
    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(area)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    val = _safe_getInfo(
        s1.mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VV"),
        default=-15.0,
    )
    return val


def _get_elevation(plot_area, surrounding_area):
    """
    ดึงระดับความสูง (SRTM DEM) ของแปลง และค่าเฉลี่ยรอบข้าง
    elevation_diff < 0 = แปลงอยู่ต่ำกว่ารอบข้าง = เสี่ยงน้ำขัง
    """
    dem = ee.Image("USGS/SRTMGL1_003")

    elev = _safe_getInfo(
        dem.reduceRegion(ee.Reducer.mean(), plot_area, 30).get("elevation"),
        default=0.0,
    )
    elev_surr = _safe_getInfo(
        dem.reduceRegion(ee.Reducer.mean(), surrounding_area, 30).get("elevation"),
        default=0.0,
    )

    return elev, elev - elev_surr


def _get_bsi(area, start_date, end_date):
    """
    คำนวณค่า BSI (Bare Soil Index) จาก Sentinel-2
    BSI = ((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))
    B11=SWIR1, B4=RED, B8=NIR, B2=BLUE
    """
    def calc_bsi(img):
        return img.expression(
            '((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))',
            {
                'B11': img.select('B11'),
                'B4': img.select('B4'),
                'B8': img.select('B8'),
                'B2': img.select('B2')
            }
        ).rename("BSI")

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(area)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .map(calc_bsi)
    )

    val = _safe_getInfo(
        s2.mean().reduceRegion(ee.Reducer.mean(), area, 10).get("BSI"),
        default=0.0,
    )
    return val


def _get_ndwi(area, start_date: str, end_date: str) -> float:
    """
    คำนวณ MNDWI (Modified Normalized Difference Water Index) จาก Sentinel-2
    MNDWI = (B3 − B11) / (B3 + B11)   Green − SWIR1
    ค่าบวก (>0) = น้ำ/ดินเปียกมาก  |  ค่าลบ (<0) = พืชปกคลุม/ดินแห้ง
    """
    def calc_mndwi(img):
        return img.normalizedDifference(["B3", "B11"]).rename("NDWI")

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(area)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .map(calc_mndwi)
    )
    val = _safe_getInfo(
        s2.mean().reduceRegion(ee.Reducer.mean(), area, 10).get("NDWI"),
        default=-0.2,
    )
    return val


SWAB_LEVEL_RANGE = 5  # เพดานระดับ ±5 — ดูเหตุผลการเลือกช่วงนี้ที่ swab_index_to_level()


def swab_index_to_level(swab_index: float, level_range: int = SWAB_LEVEL_RANGE) -> int:
    """
    แปลง swab_index (-1..+1) → "ระดับ" จำนวนเต็ม -5..+5 (0 = สมดุลดี, ติดลบ = ค่อนไป
    ทางแห้ง, บวก = ค่อนไปทางชื้น/น้ำขัง) — ผู้ใช้ขอเปลี่ยนจากเปอร์เซ็นต์เป็นตัวเลข
    ระดับที่ "เทียบง่าย" กว่า (เหมือนเทอร์โมมิเตอร์ที่ทุกคนคุ้นจากพยากรณ์อากาศ)

    ผูกกับขอบเขตสถานะจริงที่ _calc_swab() ใช้จำแนกอยู่แล้วด้านล่าง ไม่ใช่เลขคิดเอง:
    โซน "สมดุลดี" (-0.15..+0.10) ทั้งช่วง = ระดับ 0 (ไม่แบ่งย่อย เพราะ "ดีอยู่แล้ว"
    ไม่จำเป็นต้องไล่เกรด) ถัดจากนั้นนับทีละ 0.15 หน่วย (เท่ากับความกว้างจริงของโซน
    แห้งเกิน/ชื้นเกินเดิม) เป็น 1 ระดับ

    เลือกเพดาน ±5 (ไม่ใช่ ±3 หรือ ±10): ±3 แคบไป — เคสน้ำขังวิกฤตจริงที่เจอ (index
    0.448) จะชนเพดาน "+3" ทันทีทั้งที่ยังแย่กว่านี้ได้อีก แยกความรุนแรงเคสร้ายแรงไม่
    ออก ส่วน ±10 กว้างเกินจริง — index สูงสุดตามทฤษฎี (±1.0) แปลงได้ไม่ถึงระดับ 7
    อยู่ดี ระดับ 8/9/10 ไม่มีทางเกิดขึ้นจริงเลย
    """
    # epsilon กัน float imprecision ตอนหารด้วย 0.15 (เลขที่ไม่ลงตัวเป๊ะในฐาน 2) —
    # ไม่ใส่แล้วค่าอย่าง swab_index=-0.6 จะได้ระดับ -3 แทนที่จะเป็น -4 ที่ถูกต้อง
    # เพราะ (-0.15 - (-0.6)) ลอยเป็น 0.44999999999999996 ไม่ใช่ 0.45 เป๊ะ
    eps = 1e-9
    if -0.15 <= swab_index <= 0.10:
        level = 0
    elif swab_index > 0.10:
        level = 1 + int((swab_index - 0.10 + eps) / 0.15)
    else:
        level = -1 - int((-0.15 - swab_index + eps) / 0.15)
    return max(-level_range, min(level_range, level))


def _calc_swab(moisture_vv: float, bsi: float,
               elevation_diff: float, ndwi: float) -> dict:
    """
    Soil Water-Air Balance (SWAB) Index สำหรับสวนต้นไม้  (v3)

    สมดุลที่เหมาะสม — ดินร่วนปนทราย พื้นที่ลาดเนิน:
      น้ำ ≈ 35-55%  |  อากาศ ≈ 20-35%  |  วัสดุดิน ~50% (คงที่)
      หมายเหตุ: รากตื้น 30-50 ซม. ไวต่อน้ำขังมากกว่าพืชอื่น

    ที่มาของข้อมูล:
      moisture_vv   : Sentinel-1 VV backscatter (dB) → proxy volumetric water
      bsi           : Bare Soil Index               → compaction / pore loss
      elevation_diff: ระดับสูงต่ำจากรอบข้าง (ม.)   → drainage capacity
      ndwi          : MNDWI จาก Sentinel-2          → surface water status
    """
    # 1. แปลง VV backscatter → soil_water_pct (% ของปริมาตรดิน)
    #    สอบเทียบ: −20 dB (แห้ง) → ~20%  |  −5 dB (เปียก) → ~65%
    water_raw = 20.0 + (moisture_vv - (-20.0)) / 15.0 * 45.0

    # ปรับด้วย MNDWI (บวก = ผิวน้ำ/ดินเปียกมาก)
    if ndwi > 0.0:
        water_raw += ndwi * 15.0

    # ปรับด้วยระดับพื้นที่ (ต่ำกว่ารอบข้าง → น้ำขังสะสม)
    # พื้นที่ลาดชัน → น้ำไหลสะสมในที่ต่ำได้เร็ว เพิ่ม weight
    if elevation_diff < 0:
        water_raw += min(15.0, abs(elevation_diff) * 5.0)  # เพิ่มจาก 4.0 → 5.0

    # สัญญาณดิบ (อาจเกิน total_pore จริงถ้าน้ำท่วมผิวดินเกินความจุที่รูพรุนเก็บได้)
    # ใช้ตัวนี้คำนวณ swab_index/การจำแนกสถานะ — ไม่ cap เพราะต้องจับสัญญาณ "น้ำเกิน
    # ความจุ = ท่วมผิวดินจริง" ให้ทัน ไม่ให้ค่าคงที่ๆ นิ่งอยู่ที่ total_pore
    soil_water_raw = round(max(5.0, min(90.0, water_raw)), 1)

    # 2. แบ่งพื้นที่รูพรุน (pore space) เป็นน้ำ/อากาศ + วัสดุดินที่เหลือ
    #    ดินร่วนปนทราย: รูพรุนรวม ≈ 48%
    #    BSI สูง → อัดแน่นจากการเข้มงวดการใช้ที่ดิน → รูพรุนลด
    #
    #    เดิม (รอบแรก) soil_air_pct = total_pore - min(soil_water_pct, total_pore) โดยที่
    #    soil_water_pct ที่ "แสดงผล" ยังเป็นค่าดิบไม่ cap (สูงได้ถึง 90%) — เลยเกิด
    #    ปัญหา: ทุกครั้งที่ดินชื้น/ปกติ (ซึ่งเป็นเกือบทุกกรณีของสวนแถบฝนตกชุกแบบนี้)
    #    soil_water_pct มักเกิน total_pore (28-48%) อยู่แล้ว → air ติดค่า 0% แทบตลอดเวลา
    #
    #    รอบสอง แก้ด้วยการ hard-cap soil_water_pct ที่ total_pore ตรงๆ — สรุปผลรวม
    #    เป็น 100% ถูกต้องแล้ว แต่ยังไม่พอ: raw ตามสูตรค่าปกติ (เช่น vv=-6.9dB ของ
    #    แปลงที่ดูปกติดี ไม่ได้ถูกเตือนน้ำขัง) คำนวณได้ราว 59% ซึ่งเกิน total_pore
    #    (สูงสุด 48%) อยู่ดี → ติดเพดาน air=0% ซ้ำอีก ทั้งที่แปลงนั้นไม่ได้น้ำขังจริง
    #    (ยืนยันจากภาพหน้าจอผู้ใช้ส่งมา 2026-08-17: น้ำ 48% ลม 0% ทั้งที่สถานะเป็น
    #    แค่ "ชื้นเกิน" ไม่ใช่ "น้ำขังวิกฤต") — ปัญหาจริงคือ hard cap ตัดจบทันทีที่ raw
    #    แตะ total_pore แทนที่จะไล่ลงแบบสัดส่วน
    #
    #    รอบนี้: มองน้ำที่ "แสดงผล" เป็นสัดส่วนความอิ่มตัวของรูพรุน (saturation ratio)
    #    ไม่ใช่ค่า raw ตรงๆ — raw เทียบกับ SATURATION_REF (ค่า raw ที่ถือว่า "อิ่มตัว
    #    เต็มรูพรุน 100%" ซึ่งตั้งไว้ต่ำกว่าเพดาน clamp สูงสุด 90 พอสมควร เพราะ raw
    #    ระดับ 90 ต้องการทุกปัจจัยสุดขั้วพร้อมกัน — สภาพน้ำท่วมจริงมักอยู่แถว 60-70)
    #    → water_pct ไล่ระดับต่อเนื่องเข้าใกล้ total_pore เมื่อ raw เพิ่ม แทนที่จะ
    #    กระโดดชนเพดานทันที ทำให้ air_pct ไล่ลงเป็นสัดส่วนแทนตัดจบที่ 0 กะทันหัน
    #    (air=0 จริงยังเกิดได้ตอนน้ำท่วมรุนแรงสุดๆ ตามที่ควรจะเป็น แค่ไม่ใช่ทุกครั้งที่
    #    แค่ "ชื้นเกินปกติ" ธรรมดา) — swab_index/สถานะยังใช้ soil_water_raw ตรงๆ
    #    เหมือนเดิมด้านล่าง ไม่กระทบ จุดนี้แก้แค่ตัวเลขแสดงผลสามค่านี้เท่านั้น
    SATURATION_REF = 70.0
    total_pore      = max(28.0, 48.0 - max(0.0, bsi) * 22.0)
    solid_pct       = round(100.0 - total_pore, 1)
    saturation_ratio = min(1.0, soil_water_raw / SATURATION_REF)
    soil_water_pct  = round(saturation_ratio * total_pore, 1)
    soil_air_pct    = round(max(0.0, total_pore - soil_water_pct), 1)

    # 3. SWAB Index  — 0 = สมดุล, +1 = น้ำสูงสุด, −1 = แห้งสุด
    # ค่าเหมาะสม 42% (พื้นที่ลาดเนิน+ดินระบายน้ำดีกว่าที่ราบ)
    # ใช้ soil_water_raw (ไม่ cap) ไม่ใช่ soil_water_pct ที่แสดงผล — กันสถานะ
    # "น้ำขังวิกฤต" ไม่ไวพอตอนน้ำท่วมผิวดินเกินความจุรูพรุนไปมากๆ
    OPTIMAL = 42.0  # % น้ำที่เหมาะสมสำหรับต้นไม้บนพื้นที่ลาดเนิน
    swab_index = round(max(-1.0, min(1.0,
                    (soil_water_raw - OPTIMAL) / OPTIMAL)), 3)

    # 4. จำแนกสถานะ (thresholds เข้มกว่าเดิมเพราะรากตื้น + ฝนสูงตลอดปี)
    #
    # v4 (ผู้ใช้ขอ "ปรับคำให้เกษตรทั่วไปเข้าใจ"): "รากฝอย" → "รากเล็กๆ" (คำที่คุ้น
    # กว่า) และ advice ของ waterlogged เดิมพุ่งชื่อสารเคมี (Metalaxyl/Phosphonate)
    # ขึ้นก่อนโดยไม่บอกว่ามันคืออะไร/ไปหาซื้อได้จากไหน — สลับให้บอก "ยาป้องกันรากเน่า"
    # (จุดประสงค์) ก่อน ชื่อสารเป็นตัวอย่างในวงเล็บ + บอกแหล่งซื้อ (ร้านขายยาเกษตร)
    if swab_index > 0.30:   # เตือนเร็วขึ้น (เดิม 0.35)
        status, status_th, severity = (
            "waterlogged", "น้ำขังวิกฤต ⚠️ — รากเล็กๆ ขาดอากาศหายใจ", "high")
        advice = ("ขุดร่องระบายน้ำด่วน ลึก ≥ 50 ซม. รอบโคนต้น หยุดให้น้ำทันที "
                   "ฉีดยาป้องกันรากเน่า (เช่น Metalaxyl หรือ Phosphonate หาซื้อได้ที่ร้านขายยาเกษตร)")
    elif swab_index > 0.10:  # เตือนเร็วขึ้น (เดิม 0.15)
        status, status_th, severity = (
            "wet", "ชื้นเกิน 🌊 — ควรระบายน้ำ", "medium")
        advice = "ปรับปรุงร่องระบายน้ำ ลดการให้น้ำ สังเกตใบร่วง/เหลืองที่โคนต้น (สัญญาณรากเน่าระยะแรก)"
    elif swab_index >= -0.15:
        status, status_th, severity = (
            "optimal", "สมดุลดี ✅ — เหมาะกับต้นไม้", "low")
        advice = "รักษาระดับน้ำในดินตามนี้ต่อไป สัดส่วนน้ำ/อากาศเหมาะสมกับรากตื้นของต้นไม้"
    elif swab_index >= -0.30:  # เตือนเร็วขึ้น (เดิม -0.35) เพราะเนินระบายน้ำเร็ว
        status, status_th, severity = (
            "dry", "แห้งเกิน 🏜️ — ควรเพิ่มน้ำ", "medium")
        advice = "เพิ่มความถี่การให้น้ำ (เนินเขาแห้งเร็ว) คลุมโคนต้นด้วยฟางป้องกันการระเหย"
    else:
        status, status_th, severity = (
            "drought", "แล้งวิกฤต 🔥 — ต้นขาดน้ำรุนแรง", "high")
        advice = "ให้น้ำทันที! บนเนินอาจแห้งเร็วมาก ตรวจสอบระบบน้ำหยด อาจต้องให้น้ำทางใบ"

    return {
        "soil_water_pct": soil_water_pct,
        "soil_air_pct":   soil_air_pct,
        "soil_solid_pct": solid_pct,   # วัสดุดิน — คงที่ตามเนื้อดิน ไม่ผันตามฝนแล้ว
        "swab_index":     swab_index,
        "swab_level":     swab_index_to_level(swab_index),
        "ndwi":           round(ndwi, 3),
        "status":         status,
        "status_th":      status_th,
        "severity":       severity,
        "advice":         advice,
    }


# ─── Grid analysis: จุดความชื้น/น้ำขังละเอียดภายในแปลงที่วาด ────────
# เดิม 400 จุด — แปลงใหญ่ (หลักร้อยไร่ขึ้นไป) เจอ auto-widen ระยะห่างจริงจาก 10ม.
# ขึ้นไปหลายสิบเมตรบ่อยๆ โดยผู้ใช้ไม่รู้ตัว (log จริงเจอ "~5573 pts at 10m widened
# to 38m") ทำให้นำไปวัด/เทียบระยะทางจริงไม่ได้ตามที่ผู้ใช้ต้องการ — ขยับเพดานขึ้น
# เป็น 1600 (รองรับ ~100 ไร่ที่ระยะห่างจริง 10ม. ก่อนต้อง widen) ร่วมกับขยาย
# GRID_TIMEOUT_S ให้สอดคล้องกันใน main.py — แปลงที่ใหญ่กว่านั้นยังต้อง widen อยู่
# แต่ frontend จะแจ้งระยะห่างจริงที่ใช้ให้เห็นชัดเจนแทนที่จะเงียบแบบเดิม (ดู
# liff/index.html showMoistureGrid)
GRID_MAX_POINTS = 1600      # กันแปลงใหญ่ผิดปกติยิง GEE หนักเกินไป
GRID_MIN_SPACING_M = 10     # ต่ำกว่านี้ไม่มีประโยชน์ — ต่ำกว่าความละเอียด pixel จริงของ Sentinel-1/2
FLOW_MIN_SLOPE_DEG = 1.5    # พื้นที่ลาดน้อยกว่านี้ถือว่าแบน ทิศทางไม่น่าเชื่อถือ ไม่วาดลูกศร
# v1 วาดเส้นตรงทุกจุดจนทับกันรก → sparsify เหลือ 1 เส้นต่อบล็อก 3x spacing
# v2 (ตอนนี้): ฝั่ง frontend เปลี่ยนจากเส้นตรงเป็นรูปหยดน้ำ/หมุดทิศทางแบบทึบสีเดียวกับ
# จุดความชื้น (ไม่ใช่เส้นสีน้ำเงินคาดกลางแล้ว) ทับกันน้อยกว่าเดิมมาก จึงกลับมาแสดง
# ให้ใกล้เคียงทุกจุดที่มีทิศทางน่าเชื่อถือได้อีกครั้งตามที่ผู้ใช้อยากเห็น "หลายๆ อัน"
FLOW_BUCKET_FACTOR = 1.0
FLOW_ARROW_LEN_FACTOR = 0.8  # ความยาวลูกศร = spacing_m คูณค่านี้ กันยาวจนล้ำเข้าบล็อกข้างๆ


def _offset_latlng(lat: float, lng: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """
    หาพิกัดปลายทางเมื่อเดินจาก (lat,lng) ไปทาง bearing_deg (0=เหนือ, 90=ตะวันออก,
    ตามเข็มนาฬิกา — เดียวกับ convention ของ ee.Terrain.aspect) เป็นระยะ distance_m เมตร
    ใช้สูตร spherical law of cosines แบบมาตรฐาน (แม่นยำพอสำหรับระยะไม่กี่สิบเมตร)
    """
    R = 6371000.0  # รัศมีโลกเฉลี่ย (เมตร)
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    brng = math.radians(bearing_deg)
    ang_dist = distance_m / R

    lat2 = math.asin(
        math.sin(lat1) * math.cos(ang_dist) +
        math.cos(lat1) * math.sin(ang_dist) * math.cos(brng)
    )
    lng2 = lng1 + math.atan2(
        math.sin(brng) * math.sin(ang_dist) * math.cos(lat1),
        math.cos(ang_dist) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lng2)


def get_moisture_grid(polygon: list[list[float]], spacing_m: int = 10) -> list[dict]:
    """
    สุ่มจุดตาราง (grid) ภายในขอบเขตแปลงที่ผู้ใช้วาด แล้วดึงค่าความชื้น/น้ำขัง
    ทีละจุดแบบ batched — ยิง GEE ครั้งเดียว (image.sample) แทนที่จะวนยิงทีละจุด

    คืน list ของ {"lat","lng","soil_moisture_vv","ndwi","bsi",
                  "swab_index","status","status_th","severity"}

    หมายเหตุ: ใช้ elevation_diff ของทั้งแปลง (ค่าเดียว ไม่ได้คำนวณต่อจุด) เพราะ
    เป็นค่าที่วัดระดับ "แปลงเทียบรอบข้าง" ไม่ใช่ตัวแปรที่ต่างกันมากภายในแปลง
    เดียวกันซึ่งมักเล็กกว่าหลักร้อยเมตร — จุดต่างกันหลักๆ มาจาก VV/BSI/NDWI จริง
    """
    if not _gee_ready:
        raise RuntimeError("GEE not initialized — grid analysis unavailable")
    if not polygon or len(polygon) < 3:
        raise ValueError("ต้องมี polygon อย่างน้อย 3 จุด")

    spacing_m = max(GRID_MIN_SPACING_M, spacing_m)
    ee_polygon = ee.Geometry.Polygon([polygon])

    # v2 (ผู้ใช้ขอ "ตารางไม่ควรปรับขนาดช่องได้ เพราะเราใช้ 10x10 ตายตัว"): เดิม
    # แปลงใหญ่ผิดปกติจะขยาย spacing_m ขึ้นเงียบๆ (แม้จะแจ้ง frontend แล้วก็ตาม)
    # ทำให้ขนาดกระเบื้องไม่คงที่ ผู้ใช้เทียบขนาดจุดข้ามแปลงไม่ได้ — เปลี่ยนเป็น
    # "ปฏิเสธตรงๆ" แทนถ้าแปลงใหญ่เกินจะยิง GEE ไหวที่ spacing คงที่นี้ ให้ผู้ใช้
    # แบ่งวาดเป็นแปลงย่อยแทน ไม่ใช่ได้กระเบื้องขนาดเพี้ยนแบบไม่รู้ตัว
    area_sqm = _safe_getInfo(ee_polygon.area(1), default=0.0)
    est_points = area_sqm / (spacing_m ** 2)
    if est_points > GRID_MAX_POINTS:
        max_area_rai = (GRID_MAX_POINTS * spacing_m ** 2) / 1600  # 1 ไร่ = 1600 ตร.ม.
        raise ValueError(
            f"แปลงใหญ่เกินไปสำหรับตารางจุดความชื้นที่ระยะห่างคงที่ {spacing_m}ม./ช่อง "
            f"(รองรับได้ไม่เกินประมาณ {max_area_rai:.0f} ไร่) — กรุณาแบ่งวาดเป็นแปลงย่อยแทน"
        )

    today = datetime.now(timezone.utc)
    # VV (SAR) ไม่โดนเมฆบัง ใช้ช่วงสั้นให้ค่าความชื้นเป็นปัจจุบันที่สุด
    vv_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    # S2 (BSI/NDWI) โดนเมฆบังได้ — หน้าฝนอาจไม่มีภาพปลอดเมฆใน 30 วัน ใช้ช่วงยาวกว่า
    # (เท่ากับที่ analyze_durian_plot ใช้) เพิ่มโอกาสเจอภาพที่ใช้ได้
    s2_start = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    # VV backscatter (ความชื้นดิน proxy)
    vv_img = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(ee_polygon).filterDate(vv_start, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV").mean().rename("VV")
    )

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(ee_polygon).filterDate(s2_start, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )
    bsi_img = s2.map(lambda img: img.expression(
        '((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))',
        {'B11': img.select('B11'), 'B4': img.select('B4'),
         'B8': img.select('B8'), 'B2': img.select('B2')}
    ).rename("BSI")).mean()
    ndwi_img = s2.map(lambda img: img.normalizedDifference(["B3", "B11"]).rename("NDWI")).mean()

    # ── ทิศทางน้ำไหล (จากภูมิประเทศ SRTM) ──
    # aspect = ทิศที่พื้นที่ลาดลง (0=เหนือ, 90=ตะวันออก, ... ตามเข็มนาฬิกา) = ทิศที่น้ำไหล
    # slope  = ความชันเป็นองศา ใช้กรองจุดที่แบนเกินไปจนทิศทางไม่มีความหมาย
    dem = ee.Image("USGS/SRTMGL1_003")
    slope_img  = ee.Terrain.slope(dem).rename("SLOPE")
    aspect_img = ee.Terrain.aspect(dem).rename("ASPECT")

    composite = (
        vv_img.addBands(bsi_img).addBands(ndwi_img)
        .addBands(slope_img).addBands(aspect_img)
    )

    samples = _retry_gee(
        composite.addBands(ee.Image.pixelLonLat())
        .sample(region=ee_polygon, scale=spacing_m, geometries=False)
        .limit(GRID_MAX_POINTS)
        .getInfo
    )

    # ── ตั้งค่าความห่างของลูกศร (กันรกทับจุดกันเอง) ──
    flow_bucket_deg  = (spacing_m * FLOW_BUCKET_FACTOR) / 111320.0
    flow_arrow_len_m = spacing_m * FLOW_ARROW_LEN_FACTOR
    used_flow_buckets: set[tuple[int, int]] = set()

    points = []
    for feat in samples.get("features", []):
        props = feat["properties"]
        p_lat = props.get("latitude")
        p_lng = props.get("longitude")
        vv    = props.get("VV")
        bsi   = props.get("BSI")
        ndwi  = props.get("NDWI")
        slope_deg  = props.get("SLOPE")
        aspect_deg = props.get("ASPECT")
        if p_lat is None or p_lng is None or vv is None:
            continue
        swab = _calc_swab(vv, bsi or 0.0, 0.0, ndwi or -0.2)

        # ── ลูกศรทิศทางน้ำไหล: มีความหมายเฉพาะจุดที่ลาดพอสมควร และวาดแค่ 1 เส้น
        # ต่อบล็อกหยาบ (ดู FLOW_BUCKET_FACTOR) กันลูกศรทึบจนบังจุดสีของกันเอง
        flow_to = None
        if slope_deg is not None and slope_deg >= FLOW_MIN_SLOPE_DEG and aspect_deg is not None:
            bucket_key = (round(p_lat / flow_bucket_deg), round(p_lng / flow_bucket_deg))
            if bucket_key not in used_flow_buckets:
                used_flow_buckets.add(bucket_key)
                f_lat, f_lng = _offset_latlng(p_lat, p_lng, aspect_deg, flow_arrow_len_m)
                flow_to = {"lat": round(f_lat, 6), "lng": round(f_lng, 6)}

        points.append({
            "lat": round(p_lat, 6),
            "lng": round(p_lng, 6),
            "soil_moisture_vv": round(vv, 2),
            "soil_water_pct": swab["soil_water_pct"],
            "bsi":  round(bsi, 3) if bsi is not None else None,
            "ndwi": round(ndwi, 3) if ndwi is not None else None,
            "swab_index":  swab["swab_index"],
            "status":      swab["status"],
            "status_th":   swab["status_th"],
            "severity":    swab["severity"],
            "advice":      swab["advice"],
            "slope_deg":   round(slope_deg, 1) if slope_deg is not None else None,
            "flow_to":     flow_to,
        })

    if not points:
        raise RuntimeError("Grid sample returned no points — polygon may be too small for the grid spacing")

    return points


# ─────────────────────────────────────────────────────────
# ป้ายกริดอ้างอิง (A1, B2, ...) + จุดที่พบปัญหา
# ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้" — พิกัด GPS จริงใช้ประโยชน์ไม่ได้เลยถ้ายืน
# อยู่ในสวนไม่มีเครื่อง GPS แต่ "ไปช่อง C2" นับจากขอบแปลงได้ด้วยตา ป้ายชุดเดียวกันนี้
# ต้องพิมพ์ลงบนรูปแผนที่ (map_image.py) ด้วยให้ตรงกันเป๊ะ — คำนวณจาก bounding box
# ของ polygon ล้วนๆ (ไม่ใช้ GEE) เลยพอร์ต/ทำซ้ำฟังก์ชันนี้ไว้ใน map_image.py ตรงๆ ได้
# โดยไม่ต้อง import ข้ามไฟล์ (เหมือน _bearing_between/_compute_flow_graph ที่ทำไว้
# แบบเดียวกันแล้ว — กันสองไฟล์ผูกกันจนแก้ไฟล์หนึ่งพังอีกไฟล์)
# ─────────────────────────────────────────────────────────
GRID_REF_TARGET_CELLS = 12   # จำนวนช่องกริดอ้างอิงที่พยายามให้ได้ (ก่อนปรับตามสัดส่วนแปลง)
GRID_REF_MIN_DIM = 2
GRID_REF_MAX_DIM = 5


def _grid_reference_dims(polygon: list[list[float]]) -> tuple[int, int]:
    """
    คำนวณจำนวนแถว/คอลัมน์ของกริดอ้างอิง (สำหรับตั้งชื่อช่อง A1, B2, ...) จาก
    สัดส่วนจริงของแปลง (กว้าง/ยาว เป็นเมตร) — แปลงยาวรีจะได้กริดยาวรีตาม ไม่ใช่
    บังคับสี่เหลี่ยมจัตุรัสเสมอ คืน (rows, cols) จำกัดไว้ 2-5 ช่องต่อด้าน (เกินนี้
    จำยากเกินไปในรูปเล็กๆ ที่ส่งเข้า LINE)
    """
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


def _grid_ref_bounds(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    """(min_lng, min_lat, max_lng, max_lat) ของ polygon ตรงๆ — คนละอันกับ bounds
    ที่ get_plot_satellite_thumbnail คืน (อันนั้นมี buffer เผื่อขอบภาพด้วย ไม่ใช้
    ทำกริดอ้างอิงเพราะจะทำให้ช่องขอบๆ ไม่พอดีกับขอบแปลงจริง)"""
    lngs = [pt[0] for pt in polygon]
    lats = [pt[1] for pt in polygon]
    return min(lngs), min(lats), max(lngs), max(lats)


def _describe_grid_position(row_idx: int, rows: int, col_idx: int, cols: int) -> str:
    """
    บรรยายตำแหน่งช่อง (row_idx, col_idx) เป็นภาษาคน โดยใช้ทิศจริง (เหนือ/ใต้/
    ตะวันออก/ตะวันตก) แทนคำว่า "บน/ล่าง" ที่มีความหมายแค่บนหน้าจอ — มีประโยชน์กว่า
    เวลายืนอยู่ในสวนจริงและรู้ทิศ (row 0 = เหนือสุด เพราะละติจูดมากสุด, col 0 =
    ตะวันตกสุด เพราะลองจิจูดน้อยสุด)
    """
    ns = ""
    if rows > 1:
        frac = row_idx / (rows - 1)
        if frac < 0.34:
            ns = "เหนือ"
        elif frac > 0.66:
            ns = "ใต้"
    ew = ""
    if cols > 1:
        frac = col_idx / (cols - 1)
        if frac < 0.34:
            ew = "ตะวันตก"
        elif frac > 0.66:
            ew = "ตะวันออก"

    if ns and ew:
        return f"มุม{ns}{ew}ของแปลง"
    if ns:
        return f"ฝั่ง{ns}ของแปลง"
    if ew:
        return f"ฝั่ง{ew}ของแปลง"
    return "กลางแปลง"


def assign_grid_reference(points: list[dict], polygon: list[list[float]]) -> None:
    """
    เติม grid_label ("A1", "B2", ...) + position_desc (ภาษาคน) ให้ทุกจุดใน points
    (แก้ในตัว list เดิมเลย ไม่คืนค่าใหม่) — ใช้ทั้งตอนเลือก "จุดที่พบปัญหา" สำหรับ
    การ์ดไลน์ (select_problem_points) และตอนพิมพ์ป้ายกริดลงรูปแผนที่ (map_image.py
    ต้องเรียกฟังก์ชันแบบเดียวกันนี้ — ดูคอมเมนต์บนสุดของหมวดนี้)
    """
    if not points or not polygon or len(polygon) < 3:
        return
    rows, cols = _grid_reference_dims(polygon)
    min_lng, min_lat, max_lng, max_lat = _grid_ref_bounds(polygon)
    lng_span = max(1e-9, max_lng - min_lng)
    lat_span = max(1e-9, max_lat - min_lat)

    for p in points:
        col_idx = min(cols - 1, max(0, int((p["lng"] - min_lng) / lng_span * cols)))
        row_idx = min(rows - 1, max(0, int((max_lat - p["lat"]) / lat_span * rows)))
        p["grid_label"] = f"{chr(65 + row_idx)}{col_idx + 1}"
        p["position_desc"] = _describe_grid_position(row_idx, rows, col_idx, cols)


_PROBLEM_STATUS_LABEL = {
    "waterlogged": "น้ำขังหนัก",
    "wet":         "ชื้นเกิน",
    "dry":         "แห้งเกิน",
    "drought":     "แล้งจัด",
}

_RELATIVE_WET_COLOR = "#1565C0"
_RELATIVE_DRY_COLOR = "#E65100"


def _plot_relative_normalizer(values: list[float | None]):
    """
    พอร์ตตรงจาก map_image.build_normalizer() / liff buildSwabNormalizer() —
    ต้องให้ผลตรงกันเป๊ะ เพราะสีของกระเบื้องบนรูปแผนที่คำนวณจากสูตรเดียวกันนี้
    (contrast-stretch ตามช่วงข้อมูลจริงของแปลงนั้นๆ ไม่ใช่ threshold ตายตัว)

    ใช้เช็คว่าจุดหนึ่งอยู่ "ฝั่งชื้น" หรือ "ฝั่งแห้ง" เทียบกับจุดอื่นในแปลงเดียวกัน —
    ผู้ใช้เจอเคสจริง: จุดที่จัดว่า "ชื้นเกิน" ตาม threshold ตายตัวของทั้งระบบ แต่
    อยู่ในแปลงที่ชื้นทั้งแปลง (ค่าจริงกระจุกตัวสูง) เลยออกสีค่อนไปทางแดง/แห้งใน
    รูป (เพราะเป็นจุดที่ "ชื้นน้อยที่สุด" ในแปลงนั้น) ขัดกับคำว่า "ชื้นเกิน" ที่เห็น
    ในข้อความ — ฟังก์ชันนี้ทำให้คำอธิบายอ้างอิงสีเดียวกับที่ตาเห็นในรูปเสมอ
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return lambda v: 0.0
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


def select_problem_points(points: list[dict], max_points: int = 3) -> list[dict]:
    """
    เลือกจุดที่ "มีปัญหา" เด่นที่สุดในแปลง (ไม่ใช่ optimal) สำหรับใส่ในการ์ดแทนคำ
    แนะนำวิธีแก้ทั่วไป — ต้องเรียก assign_grid_reference(points, polygon) ก่อนเสมอ
    ให้แต่ละจุดมี grid_label/position_desc พร้อมแล้ว

    หลักการ: เอาจุดที่ "เปียกสุด" กับ "แห้งสุด" มาก่อนเสมอถ้ามี (ครอบคลุมทั้งสอง
    ปัญหาถ้าแปลงมีทั้งคู่) แล้วเติมที่เหลือด้วยจุดที่รุนแรงรองลงมา (เรียงตาม
    |swab_index| ห่างจากศูนย์/สมดุล) จนครบ max_points

    ป้ายข้อความ: จุดเปียก/แห้งสุดจริงในแปลงใช้สถานะจริง (status_th thresholds)
    ได้เสมอ เพราะเป็นจุดที่ swab_index สูงสุด/ต่ำสุด → รับประกันว่าเป็นสีเข้มสุด
    ของฝั่งนั้นบนรูปด้วยเสมอ (ไม่มีทางขัดกัน) — จุดเสริมอื่นๆ (ไม่ใช่จุดสุดขั้ว)
    ใช้คำเทียบเชิงสัมพัทธ์แทน ("ชื้น/แห้งกว่าจุดอื่นในแปลงนี้") ไม่ใช้ threshold
    ตายตัว กันขัดกับสีที่ยืดสเกลตามข้อมูลจริงของแปลงนั้น (ดู _plot_relative_normalizer)

    คืน list ของ dict {"grid_label","position_desc","status","label_th",
    "swab_index","lat","lng","color_hint"} — color_hint เป็น None สำหรับจุด
    สุดขั้ว (ให้ผู้เรียกใช้สีตาม status จริงได้เลย) หรือสีเทียบสัมพัทธ์สำหรับจุดเสริม
    """
    problem = [p for p in points if p.get("status") not in (None, "optimal")
              and p.get("grid_label")]
    if not problem:
        return []

    wettest = max(problem, key=lambda p: p.get("swab_index", 0.0))
    driest  = min(problem, key=lambda p: p.get("swab_index", 0.0))

    # กันช่องกริดเดียวกันโผล่ซ้ำในลิสต์ (แปลงถี่ๆ มีหลายจุดข้อมูลอยู่ในช่องอ้างอิง
    # เดียวกันได้ปกติ — dedupe ตาม grid_label ไม่ใช่แค่พิกัดเป๊ะๆ ไม่งั้นผู้ใช้เห็น
    # "C2 น้ำขังหนัก" ซ้ำกัน 2 บรรทัดโดยไม่ได้ข้อมูลใหม่เพิ่มเลย)
    selected: list[dict] = []
    seen_labels: set[str] = set()
    for p in (wettest, driest):
        if p["grid_label"] not in seen_labels:
            selected.append(p)
            seen_labels.add(p["grid_label"])

    for p in sorted(problem, key=lambda p: abs(p.get("swab_index", 0.0)), reverse=True):
        if len(selected) >= max_points:
            break
        if p["grid_label"] not in seen_labels:
            selected.append(p)
            seen_labels.add(p["grid_label"])

    normalize = _plot_relative_normalizer([p.get("swab_index") for p in points])

    result = []
    for p in selected[:max_points]:
        if p is wettest or p is driest:
            label_th   = _PROBLEM_STATUS_LABEL.get(p.get("status"), p.get("status_th", "—"))
            color_hint = None
        else:
            rel = normalize(p.get("swab_index"))
            if rel >= 0:
                label_th, color_hint = "ชื้นกว่าจุดอื่นในแปลงนี้", _RELATIVE_WET_COLOR
            else:
                label_th, color_hint = "แห้งกว่าจุดอื่นในแปลงนี้", _RELATIVE_DRY_COLOR
        result.append({
            "grid_label":    p["grid_label"],
            "position_desc": p["position_desc"],
            "status":        p.get("status"),
            "label_th":      label_th,
            "swab_index":    p.get("swab_index"),
            "lat":           p.get("lat"),
            "lng":           p.get("lng"),
            "color_hint":    color_hint,
        })
    return result


THUMBNAIL_BUFFER_M    = 25    # ขอบเผื่อรอบแปลงในภาพ (เมตร) กันขอบแปลงชิดขอบภาพเกินไป
THUMBNAIL_DIMENSIONS  = 640   # ความกว้าง/สูงภาพ (พิกเซล) — LINE preview ไม่ต้องใหญ่มาก


def get_plot_satellite_thumbnail(
    polygon: list[list[float]],
    buffer_m: float = THUMBNAIL_BUFFER_M,
    dimensions: int = THUMBNAIL_DIMENSIONS,
) -> tuple[bytes, tuple[float, float, float, float]]:
    """
    ดึงภาพถ่ายดาวเทียมจริง (Sentinel-2 true color) ของแปลงเป็น PNG bytes — ใช้เป็น
    พื้นหลังสำหรับซ้อนจุดความชื้น แล้วส่งเป็นรูปเข้า LINE OA คู่กับข้อความผลวิเคราะห์

    คืน (png_bytes, bounds) — bounds คือ (min_lng, min_lat, max_lng, max_lat) ของ
    ขอบเขตจริงที่ภาพครอบคลุม ใช้แปลงพิกัด lat/lng ของแต่ละจุด → ตำแหน่งพิกเซลในภาพ
    ทีหลัง (ดู map_image.py) ต้องคืนค่านี้ด้วยเพราะ region ถูกขยายจาก polygon ตาม
    buffer_m ไม่ใช่ bounding box ของ polygon ตรงๆ
    """
    if not _gee_ready:
        raise RuntimeError("GEE not initialized — thumbnail unavailable")

    ee_polygon = ee.Geometry.Polygon([polygon])
    region = ee_polygon.buffer(buffer_m).bounds()
    ring = _retry_gee(region.coordinates().getInfo)[0]
    lngs = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    bounds = (min(lngs), min(lats), max(lngs), max(lats))

    # หน้าฝนอาจไม่มีภาพเดี่ยวปลอดเมฆสนิทเลยในช่วงสั้นๆ — เดิมใช้ .first() (เลือกภาพ
    # ที่เมฆน้อยสุด 1 ภาพ) พังจริงตอนทดสอบ: ถ้า collection ว่าง .first() คืน null
    # แล้ว getThumbURL ต่อ null image throw "Parameter 'input' is required"
    # ใช้ .median() แทน (composite รวมหลายภาพ ทนทานกว่า — pattern เดียวกับ
    # _get_ndvi/_get_bsi ในไฟล์นี้) และขยายช่วงเป็น 180 วัน เพราะภาพนี้เป็นแค่
    # "รูปอ้างอิงหน้าตาแปลงคร่าวๆ" ไม่ต้องสดเท่าข้อมูล NDVI/ความชื้นที่วัดค่าจริง
    today = datetime.now(timezone.utc)
    start_date = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end_date   = today.strftime("%Y-%m-%d")

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )
    img = s2.median()

    vis_params = {
        "bands": ["B4", "B3", "B2"],   # true color RGB
        "min": 300, "max": 2800, "gamma": 1.3,
        "region": region, "dimensions": dimensions, "format": "png",
    }
    url = _retry_gee(img.getThumbURL, vis_params)

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content, bounds


def _get_land_displacement(area, start_now, end_now, start_prev, end_prev) -> dict:
    """
    ตรวจจับการเปลี่ยนแปลงพื้นผิวดิน (Land Displacement) ด้วย Sentinel-1 SAR
    ใช้การเปรียบเทียบ VV/VH backscatter ระหว่างปีปัจจุบัน vs ปีก่อน
    + ค่า Temporal Variability (CV) เพื่อประเมินความเสถียรของพื้นผิว

    - vv_change_db: การเปลี่ยนแปลง VV เฉลี่ย (dB) ระหว่างปี
      ค่าเปลี่ยนมาก (>2 dB) = พื้นผิวเปลี่ยนแปลงชัดเจน (ดินทรุด/น้ำท่วม/สิ่งปลูกสร้าง)
    - vh_change_db: การเปลี่ยนแปลง VH เฉลี่ย (dB) — ตอบสนองต่อ vegetation structure
    - surface_stability: ค่าเสถียรภาพพื้นผิว (0-1) จาก temporal CV ของ VV
      ใกล้ 1 = เสถียร, ใกล้ 0 = ไม่เสถียร (ดินขยับบ่อย)
    """
    def _s1_collection(start, end):
        return (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(area)
            .filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        )

    s1_now  = _s1_collection(start_now, end_now)
    s1_prev = _s1_collection(start_prev, end_prev)

    # ── VV/VH mean comparison (year-over-year) ──
    vv_now  = _safe_getInfo(
        s1_now.select("VV").mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VV"),
        default=-15.0)
    vv_prev = _safe_getInfo(
        s1_prev.select("VV").mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VV"),
        default=-15.0)

    vh_now  = _safe_getInfo(
        s1_now.select("VH").mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VH"),
        default=-22.0)
    vh_prev = _safe_getInfo(
        s1_prev.select("VH").mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VH"),
        default=-22.0)

    vv_change = round(vv_now - vv_prev, 2)
    vh_change = round(vh_now - vh_prev, 2)

    # ── Temporal Variability (StdDev / Mean) — ใช้ VV ทั้ง 2 ปี ──
    s1_all = _s1_collection(start_prev, end_now).select("VV")
    vv_stddev = _safe_getInfo(
        s1_all.reduce(ee.Reducer.stdDev()).reduceRegion(
            ee.Reducer.mean(), area, 10).get("VV_stdDev"),
        default=1.5)
    vv_mean = _safe_getInfo(
        s1_all.mean().reduceRegion(ee.Reducer.mean(), area, 10).get("VV"),
        default=-15.0)

    # CV (coefficient of variation) — ใช้ค่าสัมบูรณ์ของ mean เพราะ VV เป็น dB (ค่าลบ)
    abs_mean = abs(vv_mean) if vv_mean != 0 else 1.0
    cv = vv_stddev / abs_mean
    # แปลง CV เป็น stability score (0-1): CV ต่ำ = เสถียรสูง
    stability = round(max(0.0, min(1.0, 1.0 - cv)), 2)

    # ── สรุประดับการเปลี่ยนแปลง ──
    abs_vv_change = abs(vv_change)
    if abs_vv_change >= 3.0 or stability < 0.3:
        change_level = "high"      # เปลี่ยนแปลงมาก
    elif abs_vv_change >= 1.5 or stability < 0.5:
        change_level = "medium"    # เปลี่ยนแปลงปานกลาง
    else:
        change_level = "low"       # เสถียร

    return {
        "vv_change_db":      vv_change,
        "vh_change_db":      vh_change,
        "surface_stability": stability,
        "change_level":      change_level,
    }


def _recommend_fertilizer(ndvi_now: float, ndvi_change: float,
                          moisture_vv: float, elev_diff: float) -> dict:
    """
    คำนวณคำแนะนำปุ๋ยสำหรับต้นไม้ (กก./ต้น/ปี)
    อ้างอิงจากคู่มือกรมวิชาการเกษตร + ปรับตามข้อมูลดาวเทียม

    สูตรปุ๋ย: N-P-K + ธาตุรอง (Ca, Mg, Zn)
    ปรับตาม: NDVI (สุขภาพใบ), ความชื้น, ระดับพื้นที่
    """
    # ── กำหนด base rate ตาม NDVI ──
    if ndvi_now >= 0.65:
        # สุขภาพดี → ปุ๋ยบำรุงปกติ (เน้น K สำหรับผลผลิต)
        n_kg, p_kg, k_kg = 1.5, 0.5, 2.5
        ca_kg, mg_kg = 0.0, 0.0
        level = "maintenance"
        note = "ต้นสมบูรณ์ดี ใส่ปุ๋ยบำรุงตามปกติ เน้นโพแทสเซียม (K) เพื่อคุณภาพผล"
    elif ndvi_now >= 0.45:
        # โทรมปานกลาง → เพิ่ม N, Mg ฟื้นฟูใบ
        n_kg, p_kg, k_kg = 2.0, 0.8, 3.0
        ca_kg, mg_kg = 0.3, 0.5
        level = "recovery"
        note = "ต้นเริ่มโทรม ควรเพิ่มไนโตรเจน (N) และแมกนีเซียม (Mg) ฟื้นฟูใบ"
    elif ndvi_now >= 0.30:
        # โทรมมาก → เพิ่ม N, Ca, Mg เต็มที่
        n_kg, p_kg, k_kg = 2.5, 1.0, 3.5
        ca_kg, mg_kg = 1.0, 0.8
        level = "intensive"
        note = "ต้นโทรมหนัก ควรใส่ปุ๋ยฟื้นฟูเข้มข้น พร้อมตรวจโรคราก"
    else:
        # วิกฤต → ต้องตรวจดินก่อน
        n_kg, p_kg, k_kg = 0.0, 0.0, 0.0
        ca_kg, mg_kg = 0.0, 0.0
        level = "critical"
        note = "ต้นวิกฤต! ควรตรวจดินและรากก่อนใส่ปุ๋ย ปรึกษาผู้เชี่ยวชาญ"

    # ── ปรับตามแนวโน้ม NDVI ──
    if ndvi_change < -0.15 and level != "critical":
        # NDVI ลดมาก → เพิ่ม N 20%
        n_kg *= 1.2
        note += " | NDVI ลดลงมาก เพิ่มไนโตรเจนพิเศษ"

    # ── ปรับตามความชื้นดิน ──
    if moisture_vv > -10.0:
        # ชื้นมาก → ลด N (ชะล้างง่าย), เพิ่ม Ca (ป้องกันรากเน่า)
        n_kg *= 0.8
        ca_kg += 0.5
        note += " | ดินชื้นมาก ลด N เพิ่ม Ca ป้องกันรากเน่า"

    # ── ปรับตามระดับพื้นที่ ──
    if elev_diff < -1.5:
        # พื้นที่ต่ำ → เพิ่ม K (ทนทานต่อน้ำขัง), Ca
        k_kg *= 1.15
        ca_kg += 0.3
        note += " | แปลงต่ำ เพิ่ม K, Ca ช่วยรากทนน้ำ"

    return {
        "n_kg_per_tree":   round(n_kg, 1),
        "p_kg_per_tree":   round(p_kg, 1),
        "k_kg_per_tree":   round(k_kg, 1),
        "ca_kg_per_tree":  round(ca_kg, 1),
        "mg_kg_per_tree":  round(mg_kg, 1),
        "level":           level,
        "formula_display": f"{round(n_kg,1)}-{round(p_kg,1)}-{round(k_kg,1)}",
        "note":            note,
    }


def _estimate_yield(ndvi_now: float, ndvi_change: float,
                    moisture_vv: float, elev_diff: float,
                    bsi: float = 0.0) -> dict:
    """
    ประเมินผลผลิตเบื้องต้น (กก./ไร่/ปี) สำหรับต้นไม้ — โมเดลหลัก (v4)

    สมมติฐาน:
    - ต้นไม้อายุ 8+ ปี (ให้ผลผลิตเต็มที่)
    - ผลผลิตฐาน ~1,500 กก./ไร่/ปี ที่ NDVI 0.70 (สวนสมบูรณ์)
    - ปรับด้วย factor จากสุขภาพพืช, ความชื้น, ระดับพื้นที่, แนวโน้ม, หน้าดินเปิดโล่ง (BSI)

    v4: เพิ่ม BSI factor — ใช้ threshold เดียวกับ topsoil_risk_level (>0.2) เพื่อความสอดคล้อง
    กับส่วนอื่นของระบบ (ดินเปิดโล่ง/อัดแน่น → รากดูดซึมน้ำ-ธาตุอาหารแย่ลง → ผลผลิตลด)
    """
    base = DURIAN_BASE_YIELD_KG_PER_RAI

    # Factor 1: สุขภาพพืช (NDVI ratio)
    ndvi_factor = min(1.0, max(0.1, ndvi_now / DURIAN_OPTIMAL_NDVI))

    # Factor 2: แนวโน้ม NDVI (ลดลงมาก = ผลผลิตลด)
    if ndvi_change < -0.20:
        trend_factor = 0.70
    elif ndvi_change < -0.10:
        trend_factor = 0.85
    elif ndvi_change > 0.05:
        trend_factor = 1.05
    else:
        trend_factor = 1.0

    # Factor 3: ความชื้นดิน (ชื้นเกิน = เสี่ยงรากเน่า)
    if moisture_vv > -8.0:
        moisture_factor = 0.70   # น้ำขังรุนแรง
    elif moisture_vv > -10.0:
        moisture_factor = 0.85   # ชื้นเกิน
    elif moisture_vv < -18.0:
        moisture_factor = 0.80   # แห้งเกินไป
    else:
        moisture_factor = 1.0

    # Factor 4: ระดับพื้นที่
    if elev_diff < -2.0:
        elev_factor = 0.75   # แอ่งน้ำลึก
    elif elev_diff < -1.5:
        elev_factor = 0.85   # ต่ำกว่ารอบข้าง
    else:
        elev_factor = 1.0

    # Factor 5 (v4): หน้าดินเปิดโล่ง (BSI) — threshold เดียวกับ topsoil_risk_level
    # เพื่อความสอดคล้องกันทั้งระบบ ดินอัดแน่น/ไม่มีพืชคลุม → รากดูดซึมน้ำ-ธาตุอาหารแย่ลง
    if bsi > 0.30:
        bsi_factor = 0.80    # ดินเปิดโล่งมาก
    elif bsi > 0.20:
        bsi_factor = 0.90    # ดินเปิดโล่งปานกลาง
    else:
        bsi_factor = 1.0     # ปกติ (มีพืชคลุม)

    total_factor = ndvi_factor * trend_factor * moisture_factor * elev_factor * bsi_factor
    estimated_kg = round(base * total_factor)

    # ── ช่วง confidence ──
    low  = round(estimated_kg * 0.80)
    high = round(estimated_kg * 1.20)

    # ── ระดับผลผลิต ──
    if estimated_kg >= 1200:
        quality = "high"
        quality_label = "ดี"
    elif estimated_kg >= 800:
        quality = "medium"
        quality_label = "ปานกลาง"
    elif estimated_kg >= 400:
        quality = "low"
        quality_label = "ต่ำ"
    else:
        quality = "very_low"
        quality_label = "ต่ำมาก"

    return {
        "estimated_kg_per_rai": estimated_kg,
        "range_low":            low,
        "range_high":           high,
        "quality":              quality,
        "quality_label":        quality_label,
        "adjustment_factor":    round(total_factor, 2),
        "factors": {
            "ndvi":      round(ndvi_factor, 2),
            "trend":     round(trend_factor, 2),
            "moisture":  round(moisture_factor, 2),
            "elevation": round(elev_factor, 2),
            "bsi":       round(bsi_factor, 2),
        },
    }


def _assess_land_impact(displacement: dict, elev_diff: float,
                        moisture_vv: float, ndvi_change: float) -> dict:
    """
    ประเมินผลกระทบจากการเปลี่ยนแปลงของพื้นดิน

    วิเคราะห์ว่า displacement + elevation + moisture ส่งผลต่อ:
    1. ระบบราก (root_risk)
    2. ระบบระบายน้ำ (drainage_risk)
    3. ความเสถียรของต้น (stability_risk)
    """
    impacts = []
    severity = "low"
    risk_score = 0  # 0-100

    vv_change = displacement["vv_change_db"]
    stability = displacement["surface_stability"]
    change_level = displacement["change_level"]

    # ── 1. ดินไม่เสถียร → กระทบราก ──
    if stability < 0.3:
        risk_score += 35
        impacts.append({
            "type": "root_damage",
            "icon": "🌱",
            "title": "ระบบรากเสี่ยงเสียหาย",
            "detail": "พื้นดินขยับมาก ราก T-Root อาจขาดหรือเสียหาย "
                      "ควรตรวจสอบรากและเพิ่มการพยุงลำต้น",
        })
    elif stability < 0.5:
        risk_score += 15
        impacts.append({
            "type": "root_stress",
            "icon": "🌱",
            "title": "รากอาจเครียด",
            "detail": "พื้นดินมีการเปลี่ยนแปลงปานกลาง ควรสังเกตใบเหลืองหรือใบร่วงผิดปกติ",
        })

    # ── 2. พื้นดินขยับ + อยู่ต่ำ → ระบายน้ำแย่ลง ──
    if change_level in ("high", "medium") and elev_diff < -1.0:
        risk_score += 25
        impacts.append({
            "type": "drainage_change",
            "icon": "💧",
            "title": "ระบบระบายน้ำเปลี่ยน",
            "detail": f"พื้นที่ต่ำกว่ารอบข้าง {abs(elev_diff):.1f} ม. "
                      "และพื้นดินมีการเคลื่อนตัว ร่องน้ำเดิมอาจไม่พอ "
                      "ควรขุดร่องระบายน้ำใหม่",
        })

    # ── 3. VV เปลี่ยนมากผิดปกติ → อาจมีการทรุดตัว ──
    if abs(vv_change) >= 3.0:
        risk_score += 30
        direction = "เพิ่มขึ้น (อาจมีน้ำขังมากขึ้น)" if vv_change > 0 else "ลดลง (ดินแห้งขึ้นหรือทรุดตัว)"
        impacts.append({
            "type": "subsidence",
            "icon": "⛰️",
            "title": "สัญญาณดินทรุด/เปลี่ยนสภาพ",
            "detail": f"ค่าสะท้อนกลับ SAR {direction} {abs(vv_change):.1f} dB จากปีก่อน "
                      "แปลงอาจมีการทรุดตัวหรือสภาพพื้นผิวเปลี่ยนไป",
        })
    elif abs(vv_change) >= 1.5:
        risk_score += 10
        impacts.append({
            "type": "surface_change",
            "icon": "🔄",
            "title": "พื้นผิวเปลี่ยนแปลงเล็กน้อย",
            "detail": f"ค่า SAR เปลี่ยน {abs(vv_change):.1f} dB — เฝ้าระวังและติดตามต่อเนื่อง",
        })

    # ── 4. ผลกระทบต่อต้นไม้ (ประเมินจาก NDVI + displacement ร่วมกัน) ──
    if change_level in ("high", "medium") and ndvi_change < -0.10:
        risk_score += 20
        impacts.append({
            "type": "crop_decline",
            "icon": "🍂",
            "title": "ผลกระทบต่อต้นไม้",
            "detail": f"สุขภาพพืชลดลง {abs(ndvi_change)*100:.0f}% ร่วมกับดินเปลี่ยนแปลง "
                      "สาเหตุอาจมาจากรากถูกกระทบ ควรตรวจสอบทั้งรากและดินรอบโคนต้น",
        })

    # ── สรุประดับ severity ──
    risk_score = min(100, risk_score)
    if risk_score >= 50:
        severity = "high"
    elif risk_score >= 25:
        severity = "medium"
    else:
        severity = "low"

    if not impacts:
        impacts.append({
            "type": "stable",
            "icon": "✅",
            "title": "พื้นดินเสถียร",
            "detail": "ไม่พบการเปลี่ยนแปลงที่ผิดปกติของพื้นผิวดิน",
        })

    return {
        "severity":    severity,
        "risk_score":  risk_score,
        "impacts":     impacts,
    }


def get_ndvi_timeseries(lat: float, lng: float, months: int = 24) -> list[dict]:
    """
    คำนวณ NDVI เฉลี่ยรายเดือนจาก Sentinel-2 ย้อนหลัง n เดือน
    คืน list[{"month": "2024-06", "ndvi": 0.58}, ...]
    """
    point = ee.Geometry.Point([lng, lat])
    area  = point.buffer(PLOT_BUFFER_M)

    today      = datetime.today()
    start_date = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    end_date   = today.strftime("%Y-%m-%d")

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(area)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")
             .set("month", img.date().format("YYYY-MM")))
    )

    # Group by month using distinct month labels
    months_list = _retry_gee(
        s2.aggregate_array("month").distinct().sort().getInfo
    )

    if not months_list:
        return []

    results = []
    for m in months_list:
        monthly = s2.filter(ee.Filter.eq("month", m))
        val = _safe_getInfo(
            monthly.mean().reduceRegion(ee.Reducer.mean(), area, 10).get("NDVI"),
            default=None,
        )
        if val is not None:
            results.append({"month": m, "ndvi": round(val, 3)})

    return results


# NDVI raster palette — แดง(เสียหาย) → เหลือง → เขียว(สมบูรณ์)
NDVI_PALETTE = ["#b91c1c", "#f43f5e", "#fb923c", "#fbbf24", "#a3e635", "#16a34a"]


def get_ndvi_tile_url(months: int = 3) -> dict | None:
    """
    สร้าง XYZ tile URL ของ NDVI raster (median ย้อนหลัง n เดือน) จาก Sentinel-2
    ผ่าน ee.Image.getMapId — ใช้ซ้อนบนแผนที่ดาวเทียมใน dashboard
    คืน {"tile_url": "...{z}/{x}/{y}...", "palette": [...], "min": 0, "max": 0.8}
    หรือ None ถ้า GEE ยังไม่พร้อม
    """
    if not _gee_ready:
        return None

    today = datetime.today()
    start = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    try:
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        )
        ndvi = s2.median().normalizedDifference(["B8", "B4"]).rename("NDVI")
        vis  = {"min": 0.0, "max": 0.8, "palette": NDVI_PALETTE}
        mapid = _retry_gee(ndvi.getMapId, vis)
        url = mapid["tile_fetcher"].url_format
        return {"tile_url": url, "palette": NDVI_PALETTE, "min": 0.0, "max": 0.8,
                "period": f"{start} → {end}"}
    except Exception as e:
        logger.error(f"NDVI tile error: {e}")
        return None
