"""
GEE Analysis Module
ดึงข้อมูลดาวเทียม Sentinel-1, Sentinel-2, และ SRTM DEM
สำหรับวิเคราะห์สวนทุเรียนจากพิกัด Lat/Long ที่รับมา

v2: เพิ่ม Land Displacement Detection, Fertilizer Recommendation, Yield Estimation
"""

import time
import math
import logging
from datetime import datetime, timedelta, timezone
import ee
from cache import get_cached, set_cached
from rule_engine import predict_yield, calculate_root_rot_risk
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

# ─── ค่าอ้างอิงสำหรับทุเรียน ─────────────────────────
# ผลผลิตฐาน (กก./ไร่/ปี) — ทุเรียนหมอนทองอายุ 8+ ปี สภาพดี
DURIAN_BASE_YIELD_KG_PER_RAI = 1500
# NDVI อ้างอิง (ทุเรียนสุขภาพดีเต็มที่)
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
    วิเคราะห์สวนทุเรียนจากพิกัดที่ให้มา
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


def _calc_swab(moisture_vv: float, bsi: float,
               elevation_diff: float, ndwi: float) -> dict:
    """
    Soil Water-Air Balance (SWAB) Index สำหรับสวนทุเรียน  (v3)

    สมดุลที่เหมาะสม — ดินร่วนปนทราย เนินเขา อ.นายายอาม จ.จันทบุรี:
      น้ำ ≈ 35-55%  |  อากาศ ≈ 20-35%  |  วัสดุดิน ~50% (คงที่)
      หมายเหตุ: รากทุเรียนตื้น 30-50 ซม. ไวต่อน้ำขังมากกว่าพืชอื่น

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
    # อ.นายายอาม: พื้นที่ลาดชัน → น้ำไหลสะสมในที่ต่ำได้เร็ว เพิ่ม weight
    if elevation_diff < 0:
        water_raw += min(15.0, abs(elevation_diff) * 5.0)  # เพิ่มจาก 4.0 → 5.0

    soil_water_pct = round(max(5.0, min(90.0, water_raw)), 1)

    # 2. ประมาณ Air Pore Space
    #    ดินร่วนปนทราย อ.นายายอาม: รูพรุนรวม ≈ 48%
    #    BSI สูง → อัดแน่นจากการเข้มงวดการใช้ที่ดิน → รูพรุนลด
    total_pore    = max(28.0, 48.0 - max(0.0, bsi) * 22.0)
    # cap น้ำที่อยู่ในรูพรุน ไม่เกิน total_pore (น้ำล้นรูพรุน = น้ำขังผิวดิน)
    water_in_pore = min(soil_water_pct, total_pore)
    soil_air_pct  = round(max(0.0, total_pore - water_in_pore), 1)

    # 3. SWAB Index  — 0 = สมดุล, +1 = น้ำสูงสุด, −1 = แห้งสุด
    # อ.นายายอาม: ค่าเหมาะสม 42% (เนินเขา+ดินระบายน้ำดีกว่าที่ราบ)
    OPTIMAL = 42.0  # % น้ำที่เหมาะสมสำหรับทุเรียนบนเนิน อ.นายายอาม
    swab_index = round(max(-1.0, min(1.0,
                    (soil_water_pct - OPTIMAL) / OPTIMAL)), 3)

    # 4. จำแนกสถานะ (thresholds เข้มกว่าเดิมเพราะรากตื้น + ฝนสูงตลอดปี)
    if swab_index > 0.30:   # เตือนเร็วขึ้น (เดิม 0.35)
        status, status_th, severity = (
            "waterlogged", "น้ำขังวิกฤต ⚠️ — รากฝอยขาดออกซิเจน", "high")
        advice = "ขุดร่องระบายน้ำด่วน ลึก ≥ 50 ซม. รอบโคนต้น ฉีด Metalaxyl/Phosphonate ป้องกันรากเน่า หยุดให้น้ำทันที"
    elif swab_index > 0.10:  # เตือนเร็วขึ้น (เดิม 0.15)
        status, status_th, severity = (
            "wet", "ชื้นเกิน 🌊 — ควรระบายน้ำ", "medium")
        advice = "ปรับปรุงร่องระบายน้ำ ลดการให้น้ำ สังเกตใบร่วง/เหลืองที่โคนต้น (สัญญาณรากเน่าระยะแรก)"
    elif swab_index >= -0.15:
        status, status_th, severity = (
            "optimal", "สมดุลดี ✅ — เหมาะกับทุเรียน", "low")
        advice = "รักษาระดับน้ำในดินตามนี้ต่อไป สัดส่วนน้ำ/อากาศเหมาะสมกับรากตื้นของทุเรียน"
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
        "swab_index":     swab_index,
        "ndwi":           round(ndwi, 3),
        "status":         status,
        "status_th":      status_th,
        "severity":       severity,
        "advice":         advice,
    }


# ─── Grid analysis: จุดความชื้น/น้ำขังละเอียดภายในแปลงที่วาด ────────
GRID_MAX_POINTS = 400       # กันแปลงใหญ่ผิดปกติยิง GEE หนักเกินไป
GRID_MIN_SPACING_M = 10     # ต่ำกว่านี้ไม่มีประโยชน์ — ต่ำกว่าความละเอียด pixel จริงของ Sentinel-1/2
FLOW_MIN_SLOPE_DEG = 1.5    # พื้นที่ลาดน้อยกว่านี้ถือว่าแบน ทิศทางไม่น่าเชื่อถือ ไม่วาดลูกศร
# ลูกศร 1 เส้นต่อจุดตาราง (ระยะห่างเท่า spacing_m เดิม) รกจนบังจุดสีของกันเอง —
# วาดแค่ 1 เส้นต่อ "บล็อกหยาบ" (FLOW_BUCKET_FACTOR เท่าของ spacing_m) แทน ให้ลูกศร
# กระจายห่างพอมองเห็นทิศทางโดยรวมได้ ไม่ทับกันเป็นพรืด
FLOW_BUCKET_FACTOR = 3.0
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

    # ประเมินขนาดแปลงก่อน กันแปลงใหญ่ผิดปกติสร้างจุดเป็นพัน (safety cap)
    area_sqm = _safe_getInfo(ee_polygon.area(1), default=0.0)
    est_points = area_sqm / (spacing_m ** 2)
    if est_points > GRID_MAX_POINTS:
        spacing_m = max(GRID_MIN_SPACING_M, int((area_sqm / GRID_MAX_POINTS) ** 0.5) + 1)
        logger.warning(
            f"Grid too dense (~{est_points:.0f} pts at {GRID_MIN_SPACING_M}m) — "
            f"widened spacing to {spacing_m}m to stay under {GRID_MAX_POINTS} points"
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
    คำนวณคำแนะนำปุ๋ยสำหรับทุเรียน (กก./ต้น/ปี)
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
    ประเมินผลผลิตเบื้องต้น (กก./ไร่/ปี) สำหรับทุเรียนหมอนทอง — โมเดลหลัก (v4)

    สมมติฐาน:
    - ทุเรียนอายุ 8+ ปี (ให้ผลผลิตเต็มที่)
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
            "title": "ผลกระทบต่อต้นทุเรียน",
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
