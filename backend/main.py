import os
import json
import csv
import asyncio
import logging
import tempfile
import io
import secrets
import traceback
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

# ── ตั้ง logging + log_buffer ก่อน import module อื่นทุกตัวที่มีโอกาสพัง ──
# เดิมตั้งไว้ท้ายบล็อก import (หลัง map_image/ml_model) ทำให้ warning ตอน import
# พัง (เช่น "map_image unavailable") หลุดไปก่อน log_buffer จะพร้อมจับ — เจอเข้าจริง
# ตอนสืบสาเหตุฟีเจอร์ส่งรูป LINE ไม่ทำงาน: ยิง /admin/logs ดูแล้วไม่เจอ warning นั้น
# เลยสักครั้ง ทั้งที่ควรมี — ย้ายมาไว้บนสุดกันเหตุการณ์แบบนี้ซ้ำกับ dependency ตัวอื่น
# ในอนาคตด้วย
import log_buffer
logging.basicConfig(level=logging.INFO)
log_buffer.install()
logger = logging.getLogger(__name__)

import ee
from fastapi import FastAPI, HTTPException, Depends, Security, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from gee_analysis import analyze_durian_plot, get_ndvi_timeseries, get_ndvi_tile_url
import gee_analysis
from fastapi.concurrency import run_in_threadpool
try:
    from ml_model import get_model_meta as _get_model_meta
except Exception as _ml_model_err:
    logger.warning(f"ml_model unavailable — ML yield model disabled: {_ml_model_err}")
    _get_model_meta = None
from rule_engine import format_message, compute_risk_level
from flex_messages import build_result_flex
from line_sender import send_line_message, get_failed_queue, get_retry_stats
from plot_image_service import build_plot_grid_image_url
from database import (save_analysis, get_all_reports, save_plot,
                      get_user_plots, get_plot_history, set_notify,
                      delete_plot, find_nearby_plot, seed_demo_data,
                      upsert_user, save_iot_reading, get_plot_by_id,
                      save_field_observation, get_persistent_wet_points,
                      set_notify_digest)
from webhook import router as webhook_router
from scheduler import create_scheduler, daily_scan_job, rain_alert_job
from middleware import RateLimitMiddleware
from cache import cache_stats
from i18n import t, Lang

# ── Admin API Key Auth ─────────────────────────────────
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


async def verify_admin(key: str = Security(_admin_key_header)):
    """ตรวจ API key สำหรับ /admin/* endpoints"""
    if not ADMIN_API_KEY:
        return True  # ไม่ได้ตั้ง key → ปล่อยผ่าน (dev mode)
    if not key or not secrets.compare_digest(key, ADMIN_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid admin API key")
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Google Earth Engine and scheduler on startup."""
    try:
        service_account = os.environ["GEE_SERVICE_ACCOUNT"]

        # รองรับทั้ง: path ไปยังไฟล์ หรือ JSON string ตรงๆ ใน env var
        gee_key_raw = os.environ["GEE_KEY_JSON"]
        key_data    = json.loads(gee_key_raw)

        # เขียน JSON ลง temp file ชั่วคราว (GEE SDK ต้องการ path)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as tmp:
            json.dump(key_data, tmp)
            tmp_path = tmp.name

        credentials = ee.ServiceAccountCredentials(service_account, tmp_path)
        ee.Initialize(credentials)
        os.unlink(tmp_path)   # ลบ temp file ทันทีหลัง init
        gee_analysis._gee_ready = True
        logger.info("Google Earth Engine initialized successfully")
    except Exception as e:
        logger.warning(f"GEE not initialized (dev mode): {e} — /analyze will not work")

    # Seed demo data if Supabase not configured
    if not os.environ.get("SUPABASE_URL"):
        seed_demo_data()

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started — daily scan every day 07:00")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="GEOai Durian API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # จำกัด origin ใน production
    allow_methods=["POST", "GET", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

# LINE webhook routes
app.include_router(webhook_router)

_BASE_DIR = Path(__file__).resolve().parent.parent
# ── Fallback: ถ้า liff/dashboard ไม่อยู่ใน parent → ลองจาก cwd ────
if not (_BASE_DIR / "liff").exists():
    _BASE_DIR = Path.cwd()
if not (_BASE_DIR / "liff").exists():
    _BASE_DIR = Path(__file__).resolve().parent  # same dir (monorepo)


class AnalysisRequest(BaseModel):
    lat:      float = Field(..., ge=-90,  le=90)
    lng:      float = Field(..., ge=-180, le=180)
    user_id:  str   = Field(..., min_length=1, max_length=100)
    display_name: str = Field("", max_length=100)
    plot_name: str  = Field("แปลงที่ 1", max_length=50)
    area_rai:  float | None = Field(None, ge=0, le=10000)
    polygon:   list[list[float]] | None = Field(
        None,
        description="Polygon coordinates [[lng,lat], ...]. "
                    "Minimum 3 points. First and last must be the same (closed ring)."
    )


class PlotListRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=100)


# ── GEE call helper ─────────────────────────────────────
# analyze_durian_plot() ใช้ ee SDK แบบ synchronous (blocking I/O) —
# ต้องรันใน threadpool ไม่งั้นจะบล็อก event loop ของทั้งเซิร์ฟเวอร์
# ระหว่างรอ GEE ตอบ (พบจริงจากการทดสอบ: 11-44s ต่อ 1 คำขอ)
#
# เพิ่ม timeout + retry/backoff เพราะ GEE บาง request จะ throttle/ค้าง
# นานผิดปกติเมื่อยิงถี่ต่อเนื่อง — retry แทนที่จะปล่อยให้ค้างไม่จำกัดเวลา
GEE_CALL_TIMEOUT_S   = 40
GEE_MAX_RETRIES      = 2
GEE_RETRY_BACKOFF_S  = [3, 8]


class NoSatelliteDataError(Exception):
    """
    analyze_durian_plot() ไม่ raise error แม้พิกัดไม่มีข้อมูลดาวเทียมเลย (เช่น
    กลางทะเล/ขั้วโลก) — แต่ละ metric จะ fallback เป็นค่า default ของตัวเอง
    (_safe_getInfo) แล้วคืนผลลัพธ์แบบ "สำเร็จ" ที่เป็นข้อมูลปลอมล้วนๆ แทน
    (พบจากการทดสอบจริง: ยิง lat=0,lng=0 ได้ 200 OK พร้อมคำแนะนำปุ๋ยปลอม ทั้งที่
    เป็นกลางทะเล) จุดสังเกต: NDVI และ elevation จะ default เป็น 0.0 พร้อมกัน
    เสมอเมื่อไม่มีข้อมูลจริงเลย (พืชจริงแทบไม่มีทาง NDVI=0.000 พอดี) — ใช้ค่านี้
    เป็นลายนิ้วมือตรวจจับแทนการเชื่อผลลัพธ์ที่ได้มาตรงๆ
    """
    pass


def _looks_like_no_data(data: dict) -> bool:
    return (data.get("ndvi_now") == 0.0
            and data.get("ndvi_prev") == 0.0
            and data.get("elevation") == 0.0)


async def _run_gee_analysis(lat: float, lng: float,
                            polygon: list[list[float]] | None = None) -> dict:
    last_exc: Exception | None = None
    for attempt in range(GEE_MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                run_in_threadpool(analyze_durian_plot, lat, lng, polygon=polygon),
                timeout=GEE_CALL_TIMEOUT_S,
            )
            if _looks_like_no_data(result):
                raise NoSatelliteDataError(f"No satellite data for lat={lat} lng={lng}")
            return result
        except NoSatelliteDataError:
            raise  # ไม่มีข้อมูลจริง — retry ไปก็ไม่ช่วย ให้ fail ทันที
        except asyncio.TimeoutError as e:
            last_exc = e
            logger.warning(
                f"GEE call timed out after {GEE_CALL_TIMEOUT_S}s "
                f"(attempt {attempt + 1}/{GEE_MAX_RETRIES + 1}) lat={lat} lng={lng}"
            )
        except ee.EEException as e:
            last_exc = e
            logger.warning(
                f"GEE error (attempt {attempt + 1}/{GEE_MAX_RETRIES + 1}) "
                f"lat={lat} lng={lng}: {e}"
            )
        if attempt < GEE_MAX_RETRIES:
            await asyncio.sleep(GEE_RETRY_BACKOFF_S[attempt])
    raise last_exc


# คำที่มักปรากฏใน error เมื่อพิกัดไม่มีข้อมูลดาวเทียมครอบคลุม (กลางทะเล/ขั้วโลก/
# นอกพื้นที่ที่ดาวเทียมสแกนถึง) — พบจากการทดสอบจริงว่า Sentinel band บางตัว
# (VV, VH, NDVI, BSI, NDWI, VV_stdDev) จะขาดหายไปในพื้นที่แบบนี้
_NO_DATA_HINTS = ("does not contain key", "no bands", "empty", "not found")


def _gee_failure_detail(exc: Exception) -> tuple[int, str]:
    """แปลง exception จาก GEE ให้เป็นข้อความที่เป็นมิตรและช่วยแก้ปัญหาได้จริง"""
    if isinstance(exc, NoSatelliteDataError):
        return 422, (
            "พิกัดนี้ไม่มีข้อมูลภาพถ่ายดาวเทียม — ตำแหน่งอาจอยู่กลางทะเล ขั้วโลก "
            "หรือนอกพื้นที่ที่ดาวเทียมสำรวจ กรุณาตรวจสอบตำแหน่งปักหมุดว่าอยู่ในแปลงจริง"
        )
    if isinstance(exc, asyncio.TimeoutError):
        return 504, (
            "ดึงข้อมูลดาวเทียมสำหรับพิกัดนี้ไม่สำเร็จ อาจเป็นเพราะตำแหน่งอยู่กลางทะเล "
            "หรือนอกพื้นที่ที่ดาวเทียมมีข้อมูลครอบคลุม กรุณาตรวจสอบตำแหน่งปักหมุดว่าอยู่ในแปลงจริง "
            "แล้วลองวิเคราะห์ใหม่อีกครั้ง"
        )
    if isinstance(exc, ee.EEException):
        msg = str(exc).lower()
        if any(hint in msg for hint in _NO_DATA_HINTS):
            return 502, (
                "ไม่พบข้อมูลภาพถ่ายดาวเทียมสำหรับพิกัดนี้ พื้นที่นี้อาจอยู่กลางทะเล "
                "หรือนอกเขตที่ระบบรองรับ กรุณาตรวจสอบพิกัดที่ปักหมุดอีกครั้ง"
            )
        return 502, "ไม่สามารถดึงข้อมูลดาวเทียมได้ในขณะนี้ กรุณาลองใหม่อีกครั้งในภายหลัง"
    return 500, "เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "GEOai", "cache": cache_stats()}


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/")


@app.get("/liff/config.js")
async def liff_config():
    """
    Inject API_URL and LIFF_ID from env vars into frontend JS

    บั๊กที่เจอ (2026-08-21): env var LIFF_URL ไม่ได้ตั้งไว้บน Render จริง — endpoint
    นี้เลยส่ง LIFF_ID: "" (ว่าง) ให้หน้าเว็บเสมอ ฝั่ง JS (liff/index.html) เจอค่าว่าง
    ก็ fallback ไปใช้ placeholder "YOUR_LIFF_ID" ทำให้ liff.init() พังทุกครั้งที่เปิด
    นอกแอป LINE (บน mobile ยังพอใช้ได้เพราะ native bridge ของแอป LINE เอง "ปิดบัง"
    ปัญหานี้ไว้ได้บางส่วน แต่บน desktop browser พังชัดเจน 100%) — ใช้ fallback ID
    เดียวกับที่ webhook.py::_liff_button() ใช้อยู่แล้ว (ต้องตรงกันเป๊ะ ไม่งั้นปุ่มใน
    LINE OA กับหน้าเว็บที่เปิดตรงๆ จะใช้ LIFF app คนละตัวกัน)
    """
    liff_url = os.environ.get("LIFF_URL") or "https://liff.line.me/2010580115-di08GvXS"
    # Extract LIFF ID from URL: https://liff.line.me/LIFF_ID → LIFF_ID
    liff_id = liff_url.rsplit("/", 1)[-1] if "/" in liff_url else liff_url
    # API base: same origin (empty) in production, override via env
    api_base = os.environ.get("API_BASE_URL", "")
    sphere_key = os.environ.get("SPHERE_KEY", "")
    js = (
        f"// Auto-generated by GEOai backend — do not edit\n"
        f"window.GEOAI_CONFIG = {{\n"
        f'  API_URL: "{api_base}",\n'
        f'  LIFF_ID: "{liff_id}",\n'
        f'  SPHERE_KEY: "{sphere_key}",\n'
        f"}};\n"
    )
    return Response(content=js, media_type="application/javascript")


# ─────────────────────────────────────────────────────────
# Plot grid image — สำหรับส่งภาพแผนที่ความชื้นเข้า LINE OA คู่กับผลวิเคราะห์
# (สร้าง + อัปโหลดขึ้น Supabase Storage อยู่ใน plot_image_service.py ทั้งหมดแล้ว —
# ไม่มี endpoint เสิร์ฟภาพของแอปเองอีกต่อไป เพราะ URL ที่ได้เป็น public URL ของ
# Supabase Storage ตรงๆ ดูเหตุผลที่เลิกใช้แคชในแรมของโปรเซสเองที่ไฟล์นั้น)
# ─────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalysisRequest):
    logger.info(f"Analyzing plot: lat={req.lat}, lng={req.lng}, user={req.user_id}")
    try:
        data     = await _run_gee_analysis(req.lat, req.lng, polygon=req.polygon)
        message  = format_message(data, req.lat, req.lng)
        flex     = build_result_flex(data, req.lat, req.lng, message)

        # บันทึก user (upsert) + แปลง ก่อน — ต้องรู้ plot_id ก่อนถึงจะฝากให้
        # build_plot_grid_image_url() เก็บรูปย่อของแปลงนี้ถาวรได้ (ดูเหตุผลเต็มที่
        # plot_image_service.py) สลับลำดับมาก่อนขั้นตอนสร้างรูปด้านล่าง
        await upsert_user(req.user_id, req.display_name)
        plot_id = await save_plot(req.user_id, req.lat, req.lng,
                                  req.plot_name, req.area_rai,
                                  polygon=req.polygon)

        # แปลงที่วาดขอบเขตไว้ (polygon) เท่านั้นถึงจะมีตารางความชื้นให้ทำภาพ —
        # ของแถมส่งเข้า LINE คู่กับการ์ดผลวิเคราะห์ ล้มเหลวได้โดยไม่กระทบคำขอหลัก
        image_url = None
        if req.polygon and len(req.polygon) >= 3:
            image_url = await build_plot_grid_image_url(req.polygon, req.plot_name, plot_id=plot_id)

        await send_line_message(req.user_id, message, flex=flex, image_url=image_url)
        await save_analysis(req.user_id, data, message, plot_id=plot_id)
        return {"status": "ok", "plot_id": plot_id, "data": data}
    except (asyncio.TimeoutError, ee.EEException, NoSatelliteDataError) as e:
        logger.error(f"GEE failure: lat={req.lat} lng={req.lng}: {e}")
        status_code, detail = _gee_failure_detail(e)
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        # เดิม f"{e}" อย่างเดียว — เจอเข้าจริงว่าบาง exception (เช่น
        # asyncio/httpx timeout ที่ throw เปล่าไม่มีข้อความ) จะ log ออกมาเป็น
        # "Unexpected error: " ว่างเปล่า ไม่รู้เลยว่าพังจากอะไร ใส่ประเภท exception
        # + traceback เข้าไปด้วยเพื่อวินิจฉัยได้จริงตอนเกิดซ้ำ
        logger.error(f"Unexpected error ({type(e).__name__}): {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")


@app.get("/analyze/preview")
async def analyze_preview(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """
    GET endpoint สำหรับทดสอบวิเคราะห์เร็ว (ไม่บันทึก DB / ไม่ส่ง LINE)
    ใช้: /analyze/preview?lat=12.61&lng=102.10
    """
    try:
        data    = await _run_gee_analysis(lat, lng)
        message = format_message(data, lat, lng)
        return {"status": "ok", "data": data, "message": message}
    except (asyncio.TimeoutError, ee.EEException, NoSatelliteDataError) as e:
        logger.error(f"GEE failure: lat={lat} lng={lng}: {e}")
        status_code, detail = _gee_failure_detail(e)
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(f"Preview error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")


class GridRequest(BaseModel):
    polygon: list[list[float]] = Field(
        ..., min_length=3,
        description="พิกัดขอบเขตแปลง [[lng,lat], ...] อย่างน้อย 3 จุด"
    )
    spacing_m: int = Field(10, ge=10, le=50, description="ระยะห่างระหว่างจุดตาราง (เมตร)")


GRID_TIMEOUT_S = 70   # เผื่อเวลาให้แปลงใหญ่ (เพดานจุดขยับ 400→1600 ใน gee_analysis.py)


@app.post("/analyze/grid")
async def analyze_grid(req: GridRequest):
    """
    วิเคราะห์ตารางจุดความชื้น/น้ำขังละเอียดภายในแปลงที่วาด — สุ่มจุดตาราง
    ทุก spacing_m เมตร แล้วคืนค่าความชื้นดิน + สถานะน้ำขัง (SWAB) รายจุด
    สำหรับวาดเป็น heatmap ทับแปลงในแอป
    """
    try:
        points = await asyncio.wait_for(
            run_in_threadpool(gee_analysis.get_moisture_grid, req.polygon, req.spacing_m),
            timeout=GRID_TIMEOUT_S,
        )
        return {"status": "ok", "count": len(points), "spacing_m": req.spacing_m, "points": points}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="วิเคราะห์ตารางความชื้นใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ee.EEException as e:
        logger.error(f"Grid GEE error: {e}")
        raise HTTPException(status_code=502, detail="ไม่สามารถดึงข้อมูลดาวเทียมสำหรับตารางนี้ได้ กรุณาลองใหม่ภายหลัง")
    except Exception as e:
        logger.error(f"Grid analysis error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")


@app.get("/weather-alert/preview")
async def weather_alert_preview(lat: float, lng: float):
    """
    ทดสอบระบบแจ้งเตือนฝน+ดิน (ไม่ส่ง LINE)
    ใช้: /weather-alert/preview?lat=12.61&lng=102.10
    """
    from weather_alert import (
        get_7day_rain, assess_soil_waterlog_risk,
        evaluate_combined_risk,
    )
    try:
        forecast = await get_7day_rain(lat, lng)
        soil_data = analyze_durian_plot(lat, lng)
        soil_profile = assess_soil_waterlog_risk({
            "elevation_diff":           soil_data["elevation_diff"],
            "surface_stability":        soil_data["displacement"]["surface_stability"],
            "displacement_vv_change":   soil_data["displacement"]["vv_change_db"],
            "soil_moisture_vv":         soil_data["soil_moisture_vv"],
            "displacement_level":       soil_data["displacement"]["change_level"],
            "land_impact_severity":     soil_data["land_impact"]["severity"],
        })
        alert = evaluate_combined_risk(forecast, soil_profile)
        return {
            "status": "ok",
            "forecast": dict(forecast),
            "soil_profile": dict(soil_profile),
            "alert": dict(alert),
        }
    except Exception as e:
        logger.error(f"Weather alert preview error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _row_risk_level(r: dict) -> str:
    """
    ระดับความเสี่ยงรวมของแถวรายงานที่ดึงมาจาก Supabase (ใช้ร่วมกันทั้ง /admin/stats,
    /admin/reports, /plots/.../history) — เดิมแต่ละ endpoint คำนวณเองจากคอลัมน์ flat
    บางส่วน (ไม่รวม SWAB/BSI/land_impact เลย) ทำให้ dashboard นับ "เสี่ยงสูง/ปานกลาง"
    ตกหล่นเทียบกับที่ LIFF แสดง (ดู formula-audit)

    ลำดับความสำคัญ: full_data.overall_risk_level (คำนวณตอนวิเคราะห์สด, ครบทุกปัจจัย)
    > คำนวณจาก full_data ทั้งก้อน (แถวหลัง migrate_v5 ที่ยังไม่มี field นี้)
    > ประกอบจากคอลัมน์ flat เท่าที่มี (แถวเก่าก่อน migrate_v5 — ไม่มี SWAB ให้กู้คืน
    เพราะไม่เคยถูกบันทึกไว้เลย แต่ยังรวม displacement/topsoil/land_impact ได้ครบกว่าเดิม)
    """
    full_data = r.get("full_data") or {}
    overall = full_data.get("overall_risk_level")
    if overall in ("high", "medium", "ok"):
        return overall
    if full_data:
        return compute_risk_level(full_data)
    return compute_risk_level({
        "ndvi_change":        r.get("ndvi_change", 0),
        "elevation_diff":     r.get("elevation_diff", 0),
        "soil_moisture_vv":   r.get("soil_moisture_vv", -15),
        "displacement":       {"change_level": r.get("displacement_level", "low")},
        "topsoil_risk_level": r.get("risk_level", "low"),  # คอลัมน์ DB "risk_level" = BSI/topsoil (คนละอันกับ overall)
        "land_impact":        {"severity": r.get("land_impact_severity", "low")},
    })


@app.get("/admin/stats", dependencies=[Depends(verify_admin)])
async def admin_stats(lang: Lang = "th"):
    """สถิติรวมสำหรับ dashboard (รองรับ ?lang=en)"""
    reports = await get_all_reports(limit=1000)
    if not reports:
        return {"total": 0, "high_risk": 0, "medium_risk": 0, "ok": 0,
                "unique_users": 0, "avg_ndvi": None,
                "unstable_count": 0, "avg_yield": None,
                "labels": {k: t(f"stats.{k}", lang)
                           for k in ("total", "high_risk", "medium_risk", "ok",
                                     "unique_users", "avg_ndvi")}}

    levels       = [_row_risk_level(r) for r in reports]
    ndvi_vals    = [r["ndvi_now"] for r in reports if r.get("ndvi_now") is not None]
    unique_users = len({r["user_id"] for r in reports})

    # v2 aggregate stats
    unstable_count = sum(1 for r in reports
                         if r.get("displacement_level") in ("high", "medium"))
    yield_vals = [r["yield_estimated_kg"] for r in reports
                  if r.get("yield_estimated_kg") is not None and r["yield_estimated_kg"] > 0]
    avg_yield = round(sum(yield_vals) / len(yield_vals)) if yield_vals else None

    return {
        "total":          len(reports),
        "high_risk":      levels.count("high"),
        "medium_risk":    levels.count("medium"),
        "ok":             levels.count("ok"),
        "unique_users":   unique_users,
        "avg_ndvi":       round(sum(ndvi_vals) / len(ndvi_vals), 3) if ndvi_vals else None,
        "unstable_count": unstable_count,
        "avg_yield":      avg_yield,
        "labels": {k: t(f"stats.{k}", lang)
                   for k in ("total", "high_risk", "medium_risk", "ok",
                             "unique_users", "avg_ndvi")},
    }


@app.patch("/user/{user_id}/notify")
async def user_notify(user_id: str, enabled: bool):
    """เปิด/ปิดการแจ้งเตือนเฉพาะตอนเสี่ยง"""
    ok = await set_notify(user_id, enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="อัพเดตการตั้งค่าไม่สำเร็จ")
    return {"user_id": user_id, "notify_weekly": enabled}


@app.patch("/user/{user_id}/notify-digest")
async def user_notify_digest(user_id: str, enabled: bool):
    """เปิด/ปิดสรุปแปลงประจำวัน (ทุกแปลง ทุกเช้า 07:00 ไม่ว่าจะเสี่ยงหรือไม่) — คนละอันกับ /notify"""
    ok = await set_notify_digest(user_id, enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="อัพเดตการตั้งค่าไม่สำเร็จ")
    return {"user_id": user_id, "notify_daily_digest": enabled}


@app.get("/admin/logs", dependencies=[Depends(verify_admin)])
async def admin_logs(level: str | None = None, limit: int = 50):
    """ดึง log entries ล่าสุด (WARNING ขึ้นไป) สำหรับ monitoring"""
    return {
        "stats":   log_buffer.get_stats(),
        "entries": log_buffer.get_entries(level=level, limit=limit),
    }


@app.get("/admin/failed-messages", dependencies=[Depends(verify_admin)])
async def admin_failed_messages(limit: int = 50):
    """ดูข้อความ LINE push ที่ส่งไม่สำเร็จหลัง retry"""
    return {
        "stats":    get_retry_stats(),
        "messages": get_failed_queue(limit=limit),
    }


@app.get("/admin/reports", dependencies=[Depends(verify_admin)])
async def admin_reports(limit: int = 100):
    """API สำหรับ admin dashboard ดึงรายงานทั้งหมด"""
    reports = await get_all_reports(limit=limit)
    # แนบ risk_level ที่คำนวณแบบเดียวกับ /admin/stats ให้ dashboard อ่านตรงๆ
    # แทนที่จะคำนวณเองฝั่ง JS (เคยเป็นสาเหตุ dashboard/LIFF ไม่ตรงกัน — ดู formula-audit)
    for r in reports:
        r["risk_level"] = _row_risk_level(r)
    return {"count": len(reports), "reports": reports}


@app.get("/admin/ndvi-tiles", dependencies=[Depends(verify_admin)])
async def admin_ndvi_tiles(months: int = 3):
    """
    XYZ tile URL ของ NDVI raster จริงจาก GEE สำหรับซ้อนบนแผนที่ dashboard
    คืน {available, tile_url, palette, ...} — available=false ถ้า GEE ยังไม่พร้อม
    """
    months = max(1, min(months, 12))
    tiles = await run_in_threadpool(get_ndvi_tile_url, months)
    if not tiles:
        return {"available": False}
    return {"available": True, **tiles}


def _yield_confidence(analysis: dict) -> dict:
    """
    ประเมินความแม่นยำ/ความเชื่อมั่นของการพยากรณ์ผลผลิต
    จาก (1) ความสอดคล้องระหว่าง ML กับ rule-based (2) ความสมบูรณ์ของข้อมูล
    """
    ml_kg   = analysis.get("predicted_yield_kg_per_rai")
    y_est   = analysis.get("yield_estimate") or {}
    rule_kg = y_est.get("estimated_kg_per_rai")
    low, high = y_est.get("range_low"), y_est.get("range_high")

    agreement = None
    if ml_kg and rule_kg and max(ml_kg, rule_kg) > 0:
        agreement = round(1 - abs(ml_kg - rule_kg) / max(ml_kg, rule_kg), 3)

    # ความสมบูรณ์ข้อมูล (NDVI/ความชื้น/ระดับ ครบไหม)
    fields = ["ndvi_now", "soil_moisture_vv", "elevation_diff", "bsi"]
    completeness = sum(1 for f in fields if analysis.get(f) is not None) / len(fields)

    # ML ทำงานจริงไหม (มี prediction ที่ต่างจาก rule = โมเดลถูกใช้)
    ml_used = bool(ml_kg and ml_kg > 0)

    parts = [completeness]
    if agreement is not None:
        parts.append(agreement)
    confidence = round(sum(parts) / len(parts) * 100)
    # ปรับลดถ้าใช้ rule-based ล้วน
    if not ml_used:
        confidence = round(confidence * 0.85)

    return {
        "confidence_pct": confidence,
        "agreement_pct":  round(agreement * 100) if agreement is not None else None,
        "completeness_pct": round(completeness * 100),
        "ml_used":        ml_used,
        "ml_kg":          ml_kg,
        "rule_kg":        rule_kg,
        "range_low":      low,
        "range_high":     high,
        "quality":        y_est.get("quality"),
        "quality_label":  y_est.get("quality_label"),
        "factors":        y_est.get("factors"),
    }


@app.get("/admin/plot-detail", dependencies=[Depends(verify_admin)])
async def admin_plot_detail(lat: float, lng: float, months: int = 24):
    """
    รายละเอียดแปลงสำหรับ dashboard: time-series NDVI (จาก GEE) +
    ผลวิเคราะห์ล่าสุด (cache/mock) + ความแม่นยำโมเดล + feature importance
    """
    months = max(3, min(months, 36))
    analysis = await run_in_threadpool(analyze_durian_plot, lat, lng)
    try:
        series = await run_in_threadpool(get_ndvi_timeseries, lat, lng, months)
    except Exception as e:
        logger.warning(f"timeseries failed: {e}")
        series = []

    model_meta = _get_model_meta() if _get_model_meta else {"available": False}

    return {
        "lat": lat, "lng": lng,
        "timeseries": series,
        "confidence": _yield_confidence(analysis),
        "model": model_meta,
        "swab": analysis.get("swab"),
        "fertilizer": analysis.get("fertilizer"),
    }


@app.get("/plots/{user_id}")
async def list_plots(user_id: str):
    """ดึงแปลงทั้งหมดของเกษตรกร"""
    plots = await get_user_plots(user_id)
    return {"count": len(plots), "plots": plots}


@app.get("/plots/{user_id}/{plot_id}/history")
async def plot_history(user_id: str, plot_id: int, limit: int = 20):
    """ประวัติการวิเคราะห์ของแปลง (เรียงล่าสุดก่อน)"""
    rows = await get_plot_history(plot_id, limit=limit)
    # เดิม LIFF คำนวณ risk badge เองจาก ndvi_change/displacement_level เท่านั้น
    # (ไม่รวม SWAB/BSI) แนบ risk_level ที่นี่ให้ตรงกับที่อื่นทั้งระบบแทน (ดู formula-audit)
    for r in rows:
        r["risk_level"] = _row_risk_level(r)
    return {"plot_id": plot_id, "count": len(rows), "history": rows}


@app.get("/plots/{user_id}/{plot_id}/persistent-wet")
async def plot_persistent_wet(user_id: str, plot_id: int, days: int = 14):
    """
    จุดที่ "ชื้น/น้ำขัง" ซ้ำๆ ต่อเนื่องหลายวัน (จาก grid snapshot รายวัน) —
    สัญญาณบ่งชี้แนวโน้มทางน้ำไหลใต้ผิวดินตรงจุดนั้น ไม่ใช่แค่ฝนตกครั้งเดียว
    ต้องมี daily_scan_job เก็บ snapshot สะสมมาแล้วอย่างน้อยไม่กี่วันถึงจะมีผล
    """
    days = min(max(days, 3), 90)
    points = await get_persistent_wet_points(plot_id, days=days)
    return {"plot_id": plot_id, "days": days, "count": len(points), "points": points}


@app.get("/plots/{user_id}/{plot_id}/ndvi-timeseries")
async def ndvi_timeseries(user_id: str, plot_id: int, months: int = 24):
    """NDVI รายเดือนจาก GEE ย้อนหลัง n เดือน (max 36)"""
    months = min(months, 36)
    plots = await get_user_plots(user_id)
    plot = next((p for p in plots if p["id"] == plot_id), None)
    if not plot:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้")
    try:
        series = get_ndvi_timeseries(plot["lat"], plot["lng"], months=months)
        return {"plot_id": plot_id, "months": months, "series": series}
    except Exception as e:
        logger.error(f"NDVI timeseries error: {e}")
        raise HTTPException(status_code=502, detail="ไม่สามารถดึงข้อมูล NDVI ได้")


@app.delete("/plots/{user_id}/{plot_id}")
async def remove_plot(user_id: str, plot_id: int):
    """ลบแปลง (ตรวจสอบสิทธิ์ user_id ก่อนลบ)"""
    ok = await delete_plot(user_id, plot_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้ หรือไม่มีสิทธิ์ลบ")
    return {"status": "deleted", "plot_id": plot_id}


@app.get("/admin/reports/export.csv", dependencies=[Depends(verify_admin)])
async def export_csv(limit: int = 1000):
    """Export รายงานทั้งหมดเป็น CSV ดาวน์โหลดได้"""
    reports = await get_all_reports(limit=limit)

    fields = ["id", "user_id", "lat", "lng",
              "ndvi_now", "ndvi_prev", "ndvi_change",
              "soil_moisture_vv", "elevation", "elevation_diff",
              "displacement_vv_change", "surface_stability", "displacement_level",
              "fertilizer_n", "fertilizer_p", "fertilizer_k",
              "fertilizer_ca", "fertilizer_mg", "fertilizer_level",
              "yield_estimated_kg", "yield_quality",
              "land_impact_severity", "land_impact_score",
              "created_at"]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in reports:
        writer.writerow(r)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=geoai_reports.csv"}
    )


class IotReading(BaseModel):
    plot_id:      int
    sensor_id:    str   = Field(..., min_length=1, max_length=50)
    depth_cm:     int   = Field(..., ge=1, le=200)
    moisture_pct: float = Field(..., ge=0, le=100)
    temp_c:       float = Field(..., ge=-10, le=60)
    timestamp:    str | None = None


@app.post("/iot/reading")
async def iot_reading(req: IotReading):
    """Gap 2: รับข้อมูล IoT soil sensor → บันทึก DB → แจ้งเตือน LINE ถ้าความชื้นเกินเกณฑ์"""
    reading_id = await save_iot_reading(
        req.plot_id, req.sensor_id, req.depth_cm,
        req.moisture_pct, req.temp_c, req.timestamp,
    )

    alert_sent = False
    if req.moisture_pct > 80 and req.depth_cm >= 20:
        plot = await get_plot_by_id(req.plot_id)
        if plot:
            msg = (
                f"🚨 แจ้งเตือน IoT Sensor — {plot.get('name', 'แปลง')}\n"
                f"💧 ความชื้นดินที่ราก {req.depth_cm} ซม. = {req.moisture_pct:.1f}% เกินเกณฑ์\n"
                f"🌡️ อุณหภูมิดิน {req.temp_c:.1f}°C\n"
                f"⚠️ เสี่ยงรากเน่า — ขุดร่องระบายน้ำทันที!"
            )
            await send_line_message(plot["user_id"], msg)
            alert_sent = True
            logger.warning(
                f"IoT alert: plot {req.plot_id} sensor {req.sensor_id} "
                f"moisture={req.moisture_pct}% depth={req.depth_cm}cm"
            )

    return {"status": "ok", "reading_id": reading_id, "alert_sent": alert_sent}


class FieldObservation(BaseModel):
    plot_id:            int
    actual_yield_kg:    float = Field(..., ge=0, le=10000)
    root_rot_occurred:  bool
    observation_date:   str


@app.post("/admin/trigger/daily-scan", dependencies=[Depends(verify_admin)])
async def trigger_daily_scan(background_tasks: BackgroundTasks, hour: int | None = None):
    """
    เรียกงานสแกนความเสี่ยงรายวัน (daily_scan_job) ด้วยตนเอง — สำหรับผูกกับ cron
    ภายนอก (GitHub Actions — ดู .github/workflows/keep-alive.yml) ให้ยิงมาทุกชั่วโมง
    ตามช่วงเวลาที่เปิดให้ผู้ใช้เลือกได้ (05:00-10:00 น.)

    เหตุผลที่ต้องมี endpoint นี้: Render free tier ให้บริการ sleep service ทิ้งหลังไม่มี
    traffic 15 นาที — ตัวจับเวลาในโปรเซส (APScheduler) จะไม่ทำงานเลยถ้าแอปกำลังหลับ
    อยู่พอดีตอนถึงเวลา (ไม่ catch-up ทีหลังด้วย) request ที่ยิงมาที่ endpoint นี้เองจะ
    ปลุกแอปให้ตื่นก่อน (Render wake ตอนมี HTTP request เข้า) แล้วค่อยสั่งรันงานจริง

    hour: ชั่วโมง (เวลาไทย) ที่ถูกทริกเกอร์มา — daily_scan_job จะประมวลผลเฉพาะผู้ใช้
    ที่ตั้ง notify_hour ตรงกับค่านี้ ไม่ใส่ (None) = ประมวลผลทุกคน (ใช้ตอนทดสอบด้วยมือ)

    รันเป็น background task แล้วตอบกลับทันที (ไม่รอผลลัพธ์) เพราะสแกนทุกแปลงของ
    ทุกคนอาจใช้เวลาหลายนาที ยิงยาวเกิน timeout ปกติของ cron ภายนอกได้
    """
    background_tasks.add_task(daily_scan_job, hour)
    return {"status": "started", "job": "daily_scan", "hour": hour}


@app.post("/admin/trigger/rain-alert", dependencies=[Depends(verify_admin)])
async def trigger_rain_alert(background_tasks: BackgroundTasks, hour: int | None = None):
    """เหมือน trigger_daily_scan แต่สำหรับงานแจ้งเตือนฝน (rain_alert_job)"""
    background_tasks.add_task(rain_alert_job, hour)
    return {"status": "started", "job": "rain_alert", "hour": hour}


@app.post("/admin/seed-nayaiam", dependencies=[Depends(verify_admin)])
async def seed_nayaiam():
    """Seed 5-year historical test data for Na Yai Am durian garden"""
    import random, httpx as _httpx
    from datetime import datetime, timezone, timedelta

    SUPA_URL = os.environ.get("SUPABASE_URL", "")
    SUPA_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not SUPA_URL or not SUPA_KEY:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    USER_ID = "U211bed9a05c229e9775c133e13f95117"
    LAT, LNG = 12.9648, 101.9669
    random.seed(2024)
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    records = []

    for i in range(20):
        dt = now - timedelta(days=90 * i)
        month = dt.month
        is_rainy = 5 <= month <= 10
        trend = i / 20.0
        base = 0.62 if is_rainy else 0.44
        base -= trend * 0.08
        ndvi_now = round(base + random.uniform(-0.04, 0.04), 3)
        ndvi_prev = round(ndvi_now - random.uniform(-0.06, 0.06), 3)
        ndvi_change = round(ndvi_now - ndvi_prev, 3)
        moisture = round(random.uniform(-12, -9) if is_rainy else random.uniform(-17, -13), 1)
        elev = round(random.uniform(45, 55), 1)
        elev_diff = round(random.uniform(-0.8, 0.8), 1)
        stability = round(random.uniform(0.75, 0.95), 2)
        fert_n = round(random.uniform(0.6, 1.0), 2)
        fert_p = round(random.uniform(0.2, 0.4), 2)
        fert_k = round(random.uniform(0.8, 1.2), 2)
        fert_ca = round(random.uniform(0.15, 0.30), 2)
        fert_mg = round(random.uniform(0.08, 0.18), 2)
        is_harvest = month in (12, 1, 2, 3)
        yield_kg = random.randint(1100, 1500) if is_harvest else random.randint(800, 1200)
        yield_q = "high" if yield_kg > 1300 else ("medium" if yield_kg > 1000 else "low")
        label = "ดีมาก" if ndvi_now > 0.60 else ("ดี" if ndvi_now > 0.45 else "ปานกลาง")
        season = "หน้าฝน" if is_rainy else "หน้าแล้ง"
        msg = (f"แปลงทุเรียนนายายอาม {dt.strftime('%b %Y')} | NDVI={ndvi_now:.3f}({label}) {season} | "
               f"ความชื้น={moisture}dB | ผลผลิต={yield_kg}กก./ไร่ | ปุ๋ย N={fert_n} P={fert_p} K={fert_k}")
        records.append({
            "user_id": USER_ID, "lat": LAT, "lng": LNG,
            "ndvi_now": ndvi_now, "ndvi_prev": ndvi_prev, "ndvi_change": ndvi_change,
            "soil_moisture_vv": moisture, "elevation": elev, "elevation_diff": elev_diff,
            "displacement_vv_change": round(random.uniform(-1.2, 0.5), 2),
            "displacement_vh_change": round(random.uniform(-0.9, 0.4), 2),
            "surface_stability": stability,
            "displacement_level": "low" if stability > 0.80 else "medium",
            "fertilizer_n": fert_n, "fertilizer_p": fert_p, "fertilizer_k": fert_k,
            "fertilizer_ca": fert_ca, "fertilizer_mg": fert_mg, "fertilizer_level": "maintenance",
            "yield_estimated_kg": yield_kg, "yield_quality": yield_q,
            "land_impact_severity": "low", "land_impact_score": random.randint(10, 30),
            "message": msg, "created_at": dt.isoformat(),
        })

    records.sort(key=lambda r: r["created_at"])
    headers = {
        "apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    async with _httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{SUPA_URL}/rest/v1/analyses", headers=headers, json=records)

    if resp.status_code in (200, 201):
        logger.info(f"Seeded {len(records)} Na Yai Am records")
        return {"status": "ok", "inserted": len(records), "range": f"{records[0]['created_at'][:7]} → {records[-1]['created_at'][:7]}"}
    raise HTTPException(status_code=500, detail=f"Supabase error {resp.status_code}: {resp.text[:200]}")


@app.post("/admin/feedback", dependencies=[Depends(verify_admin)])
async def save_feedback(req: FieldObservation):
    """Gap 5: บันทึก field observation จริงจากเกษตรกร — ใช้ retrain ML model"""
    obs_id = await save_field_observation(
        req.plot_id, req.actual_yield_kg,
        req.root_rot_occurred, req.observation_date,
    )
    if obs_id is None:
        raise HTTPException(status_code=500, detail="บันทึกไม่สำเร็จ")
    return {"status": "ok", "observation_id": obs_id, "plot_id": req.plot_id}


# ── Static file mounts (MUST be after all API routes) ────
app.mount("/liff", StaticFiles(directory=str(_BASE_DIR / "liff"), html=True), name="liff")
app.mount("/dashboard", StaticFiles(directory=str(_BASE_DIR / "dashboard"), html=True), name="dashboard")
