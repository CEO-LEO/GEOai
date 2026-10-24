import os
import json
import csv
import asyncio
import logging
import tempfile
import io
import secrets
from datetime import datetime, timedelta, timezone
import traceback
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
# test ตั้ง IAMROOT_DISABLE_DOTENV=1 (ดู conftest.py) กันไม่ให้ backend/.env ตัวจริงใน
# เครื่องนักพัฒนาถูกโหลดเข้ามาปนระหว่างรัน test — เคยทำให้ test admin พัง 6 ตัว
# เพราะ ADMIN_API_KEY จาก .env และเสี่ยงยิงบริการจริงด้วย key จริง
if os.environ.get("IAMROOT_DISABLE_DOTENV") != "1":
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
from fastapi import FastAPI, HTTPException, Depends, Security, Query, BackgroundTasks, Request
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
from line_sender import send_line_message, get_failed_queue, get_retry_stats
import line_quota
import client_telemetry as client_telemetry_mod
from plot_image_service import build_plot_grid_image_url
from database import (save_analysis, get_all_reports, save_plot,
                      get_user_plots, get_plot_history, set_notify,
                      delete_plot, find_nearby_plot, seed_demo_data,
                      upsert_user, save_iot_reading, get_plot_by_id,
                      save_field_observation, get_persistent_wet_points,
                      set_notify_digest, get_latest_risk_by_plots, get_user,
                      get_recent_message_failures, get_scheduler_last_run,
                      set_scheduler_last_run)
from webhook import router as webhook_router
from scheduler import create_scheduler, daily_scan_job, rain_alert_job, run_job_with_catchup
from middleware import RateLimitMiddleware, user_rate_limit, ip_bucket_exceeded
from auth import current_user, liff_id, dev_auth_user
from cache import cache_stats
from i18n import t, Lang

# ── Admin API Key Auth ─────────────────────────────────
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def verify_admin(key: str = Security(_admin_key_header)):
    """
    ตรวจ API key สำหรับ /admin/* และ endpoint ที่สั่ง push ข้อความ LINE ได้

    fail-closed: เดิมถ้าไม่ได้ตั้ง ADMIN_API_KEY จะ "ปล่อยผ่าน" ทุกคำขอ (dev mode) —
    ถ้า env หายไปตอน deploy ทุกคนจะกลายเป็น admin ทันที ตอนนี้ปฏิเสธแทน
    อ่าน env ตอนเรียก ไม่ใช่ตอน import เพื่อให้ key ที่เปลี่ยนใหม่มีผลกับ test/โปรเซสเดิม
    """
    admin_key = os.environ.get("ADMIN_API_KEY", "")
    if not admin_key:
        logger.error("ADMIN_API_KEY is not set — rejecting admin request")
        raise HTTPException(status_code=503, detail="Admin access is not configured")
    if not key or not secrets.compare_digest(key, admin_key):
        raise HTTPException(status_code=403, detail="Invalid admin API key")
    return True


# ── User auth helpers ──────────────────────────────────
# ทุก endpoint ฝั่งผู้ใช้ต้องผ่าน current_user (ตรวจ LINE token ฝั่ง server) — user_id
# ใน URL ต้องตรงกับเจ้าของ token และแปลงต้องเป็นของผู้ใช้คนนั้นจริง
_api_user     = user_rate_limit("api", 60)      # ใช้ทั่วไป
_analyze_user = user_rate_limit("analyze", 3)   # เรียก GEE หนัก (เท่าเพดานเดิม 3 ครั้ง/นาที)
_grid_user    = user_rate_limit("grid", 10)


def _require_self(path_user_id: str, user_id: str) -> None:
    if path_user_id != user_id:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงข้อมูลของผู้ใช้อื่น")


async def _require_own_plot(plot_id: int, user_id: str) -> dict:
    # ตอบ 404 เหมือนกันทั้ง "ไม่มีแปลงนี้" และ "เป็นแปลงของคนอื่น" — ไม่บอกใบ้ว่า plot_id
    # ไหนมีอยู่จริง (plot_id เป็นเลขเรียง ไล่เดาได้)
    plot = await get_plot_by_id(plot_id)
    if not plot or plot.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้")
    return plot


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

    if not os.environ.get("ADMIN_API_KEY"):
        logger.warning("ADMIN_API_KEY not set — /admin/* endpoints will reject every request")
    if dev_auth_user():
        logger.warning("IAMROOT_DEV_AUTH_USER is set — requests without a LINE token are "
                       "treated as that user. Local development only.")

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started — daily scan every day 07:00")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="IAM ROOT API", version="1.0.0", lifespan=lifespan)

# LIFF และ dashboard ถูกเสิร์ฟจาก origin เดียวกับ API จึงไม่ต้องใช้ CORS — เดิมเปิด "*"
# ตอนนี้เปิดเฉพาะ origin ที่ระบุใน CORS_ALLOW_ORIGINS (คั่นด้วย ,) เช่นถ้าย้าย frontend
# ไปโดเมนอื่นในอนาคต (ใช้คู่กับ API_BASE_URL)
_cors_origins = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["POST", "GET", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Line-Token-Type", "X-Admin-Key"],
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
    # ไม่ใช้แล้ว — ผู้ใช้มาจาก LINE token เท่านั้น (auth.current_user) คงไว้ให้ client
    # รุ่นเก่าที่ยังส่งมาไม่โดน 422
    user_id:  str | None = Field(None, max_length=100)
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
    return {"status": "ok", "service": "IAM ROOT", "cache": cache_stats()}


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/")


@app.post("/client/telemetry", status_code=204)
async def client_telemetry(event: client_telemetry_mod.ClientEvent, request: Request):
    """
    เวลาเปิดแอป/ error จากหน้า LIFF — ไม่ต้อง login (ส่งได้แม้ยืนยันตัวตนไม่สำเร็จ ซึ่งเป็น
    error ที่อยากเห็นที่สุด) ไม่มีข้อมูลส่วนบุคคล ขนาดจำกัดด้วย ClientEvent และนับในเพดาน
    rate limit ต่อ IP เหมือน API อื่น
    """
    # เพดานแยก 30 ครั้ง/นาที/IP — กัน log ถูกถมด้วย error ปลอม (เกินแล้วเงียบ ไม่ตอบ error)
    if not ip_bucket_exceeded(request, "telemetry", 30):
        client_telemetry_mod.record(event, request.headers.get("User-Agent", ""))
    return Response(status_code=204)


@app.get("/admin/client-telemetry", dependencies=[Depends(verify_admin)])
async def admin_client_telemetry(days: int = 7):
    """สรุปเวลาเปิดแอป (p50/p90 ต่อขั้นตอน, แยกเวอร์ชัน/แพลตฟอร์ม) + error ล่าสุดจาก LIFF"""
    return client_telemetry_mod.summary(since_s=min(max(days, 1), 30) * 86400)


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
    # API base: same origin (empty) in production, override via env
    api_base = os.environ.get("API_BASE_URL", "")
    sphere_key = os.environ.get("SPHERE_KEY", "")
    js = (
        f"// Auto-generated by IAM ROOT backend — do not edit\n"
        f"window.GEOAI_CONFIG = {{\n"
        f'  API_URL: "{api_base}",\n'
        f'  LIFF_ID: "{liff_id()}",\n'
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
async def analyze(req: AnalysisRequest, user_id: str = Depends(_analyze_user)):
    """
    วิเคราะห์แปลงของผู้ใช้ที่ยืนยันตัวตนแล้ว (user จาก LINE token เท่านั้น)

    Phase 1b: ไม่ push การ์ดผลเข้าแชท LINE แล้ว — ผู้ใช้กดเองในแอปและเห็นผลบนหน้าจอ
    อยู่แล้ว push นี้กินโควตา LINE OA 1 ข้อความทุกครั้งที่กดตรวจ (แพลน Free มีแค่ 300/เดือน)
    ผู้ใช้ยังดูการ์ดในแชทได้จากปุ่ม "ตรวจสอบ" ใน Rich Menu (ส่งแบบ reply ไม่นับโควตา)
    """
    req.user_id = user_id
    logger.info(f"Analyzing plot: lat={req.lat}, lng={req.lng}, user={req.user_id}")
    try:
        data     = await _run_gee_analysis(req.lat, req.lng, polygon=req.polygon)
        message  = format_message(data, req.lat, req.lng)

        # บันทึก user (upsert) + แปลง ก่อน — ต้องรู้ plot_id ก่อนถึงจะฝากให้
        # build_plot_grid_image_url() เก็บรูปย่อของแปลงนี้ถาวรได้ (ดูเหตุผลเต็มที่
        # plot_image_service.py) สลับลำดับมาก่อนขั้นตอนสร้างรูปด้านล่าง
        await upsert_user(req.user_id, req.display_name)
        plot_id = await save_plot(req.user_id, req.lat, req.lng,
                                  req.plot_name, req.area_rai,
                                  polygon=req.polygon)

        # แปลงที่วาดขอบเขตไว้ (polygon) — สร้างรูปแผนที่ + รูปย่อของแปลง (รูปย่อใช้ในหน้า
        # "แปลงของฉัน") ล้มเหลวได้โดยไม่กระทบคำขอหลัก ส่ง image_url/problem_points
        # กลับไปใน response ให้หน้าแอปใช้ได้ (ไม่ push เข้า LINE แล้ว — ดู docstring)
        image_url = None
        problem_points = []
        if req.polygon and len(req.polygon) >= 3:
            img_result = await build_plot_grid_image_url(req.polygon, req.plot_name, plot_id=plot_id)
            image_url = img_result["url"]
            problem_points = img_result["problem_points"]

        await save_analysis(req.user_id, data, message, plot_id=plot_id)
        return {"status": "ok", "plot_id": plot_id, "data": data,
                "image_url": image_url, "problem_points": problem_points}
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


@app.get("/analyze/preview", dependencies=[Depends(verify_admin)])
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


@app.post("/analyze/grid", dependencies=[Depends(_grid_user)])
async def analyze_grid(req: GridRequest):
    """
    วิเคราะห์ตารางจุดความชื้น/น้ำขังละเอียดภายในแปลงที่วาด — สุ่มจุดตาราง
    ทุก spacing_m เมตร แล้วคืนค่าความชื้นดิน + สถานะน้ำขัง (SWAB) รายจุด
    สำหรับวาดเป็น heatmap ทับแปลงในแอป
    """
    try:
        # allow_widen=False (ค่าเริ่มต้น) — ฟีเจอร์นี้ interactive ผู้ใช้กดเองใน LIFF
        # ต้องการระยะห่างคงที่เป๊ะ ไม่ใช่ปรับขนาดช่องเงียบๆ (ผู้ใช้ยืนยันไว้)
        points, actual_spacing_m = await asyncio.wait_for(
            run_in_threadpool(gee_analysis.get_moisture_grid, req.polygon, req.spacing_m),
            timeout=GRID_TIMEOUT_S,
        )
        return {"status": "ok", "count": len(points), "spacing_m": actual_spacing_m, "points": points}
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


@app.get("/weather-alert/preview", dependencies=[Depends(verify_admin)])
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
        soil_data = await run_in_threadpool(analyze_durian_plot, lat, lng)
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
async def user_notify(user_id: str, enabled: bool, me: str = Depends(_api_user)):
    """เปิด/ปิดการแจ้งเตือนเฉพาะตอนเสี่ยง"""
    _require_self(user_id, me)
    ok = await set_notify(user_id, enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="อัพเดตการตั้งค่าไม่สำเร็จ")
    return {"user_id": user_id, "notify_weekly": enabled}


@app.patch("/user/{user_id}/notify-digest")
async def user_notify_digest(user_id: str, enabled: bool, me: str = Depends(_api_user)):
    """เปิด/ปิดสรุปแปลงประจำวัน (ทุกแปลง ทุกเช้า 07:00 ไม่ว่าจะเสี่ยงหรือไม่) — คนละอันกับ /notify"""
    _require_self(user_id, me)
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


_HEALTH_CODE = {"ok": 0, "warn": 1, "critical": 2}
_HEALTH_NAME = {v: k for k, v in _HEALTH_CODE.items()}
_last_health_mem: str | None = None   # สำรองเมื่อ DB ใช้ไม่ได้


@app.get("/admin/messaging/status", dependencies=[Depends(verify_admin)])
async def admin_messaging_status(track: bool = False):
    """
    สถานะการส่งข้อความ LINE: โควตาคงเหลือ, จำนวนที่ส่งไม่สำเร็จ/ถูกข้ามใน 24 ชม.
    health = ok / warn / critical

    track=true (ใช้โดย GitHub Actions ทุกชั่วโมง): เทียบกับสถานะครั้งก่อนที่บันทึกไว้ แล้วคืน
    changed=true เฉพาะตอนสถานะเปลี่ยน → workflow ล้ม (= GitHub อีเมลแจ้ง) เฉพาะตอนนั้น
    ไม่ใช่ทุกชั่วโมงที่ยังมีปัญหา (เจ้าของขอ 2026-10-17 — อีเมลซ้ำทุกชั่วโมงจะทำให้เลิกอ่าน)
    สถานะครั้งก่อนเก็บในตาราง scheduler_runs (job_name="messaging_health",
    last_run_hour = รหัสระดับ 0/1/2) ไม่ต้องเพิ่ม schema
    """
    global _last_health_mem
    await line_quota.refresh(force=True)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = {**line_quota.health(),
              "recent_failures": await get_recent_message_failures(since, limit=20)}
    if track:
        stored = await get_scheduler_last_run("messaging_health")
        if stored is not None:
            previous = _HEALTH_NAME.get(stored.get("last_run_hour"), "ok")
        else:
            previous = _last_health_mem or "ok"
        current = result["health"]
        result["previous_health"] = previous
        result["changed"] = current != previous
        if result["changed"]:
            _last_health_mem = current
            await set_scheduler_last_run("messaging_health",
                                         datetime.now(timezone.utc).date().isoformat(),
                                         _HEALTH_CODE[current])
    return result


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
async def list_plots(user_id: str, me: str = Depends(_api_user)):
    """
    ดึงแปลงทั้งหมดของเกษตรกร — เติม latest_risk_level/latest_analyzed_at ต่อแปลง
    (ผู้ใช้ขอ: ป้ายความสดของข้อมูลบนการ์ด + ย้อมสีขอบเขตแปลงบนแผนที่ตามความเสี่ยง)
    ยิง query เดียวรวมทุกแปลง ไม่วนยิงทีละแปลง (ดู get_latest_risk_by_plots)
    """
    _require_self(user_id, me)
    plots = await get_user_plots(user_id)
    plot_ids = [p["id"] for p in plots if p.get("id") is not None]
    risk_by_plot = await get_latest_risk_by_plots(plot_ids)
    for p in plots:
        info = risk_by_plot.get(p.get("id"))
        p["latest_risk_level"] = info["risk_level"] if info else None
        p["latest_analyzed_at"] = info["analyzed_at"] if info else None
    return {"count": len(plots), "plots": plots}


@app.get("/plots/{user_id}/{plot_id}/history")
async def plot_history(user_id: str, plot_id: int, limit: int = 20,
                       me: str = Depends(_api_user)):
    """ประวัติการวิเคราะห์ของแปลง (เรียงล่าสุดก่อน)"""
    _require_self(user_id, me)
    await _require_own_plot(plot_id, me)
    limit = min(max(limit, 1), 100)
    rows = await get_plot_history(plot_id, limit=limit)
    # เดิม LIFF คำนวณ risk badge เองจาก ndvi_change/displacement_level เท่านั้น
    # (ไม่รวม SWAB/BSI) แนบ risk_level ที่นี่ให้ตรงกับที่อื่นทั้งระบบแทน (ดู formula-audit)
    for r in rows:
        r["risk_level"] = _row_risk_level(r)
    return {"plot_id": plot_id, "count": len(rows), "history": rows}


@app.get("/plots/{user_id}/{plot_id}/persistent-wet")
async def plot_persistent_wet(user_id: str, plot_id: int, days: int = 14,
                              me: str = Depends(_api_user)):
    """
    จุดที่ "ชื้น/น้ำขัง" ซ้ำๆ ต่อเนื่องหลายวัน (จาก grid snapshot รายวัน) —
    สัญญาณบ่งชี้แนวโน้มทางน้ำไหลใต้ผิวดินตรงจุดนั้น ไม่ใช่แค่ฝนตกครั้งเดียว
    ต้องมี daily_scan_job เก็บ snapshot สะสมมาแล้วอย่างน้อยไม่กี่วันถึงจะมีผล
    """
    _require_self(user_id, me)
    await _require_own_plot(plot_id, me)
    days = min(max(days, 3), 90)
    points = await get_persistent_wet_points(plot_id, days=days)
    return {"plot_id": plot_id, "days": days, "count": len(points), "points": points}


@app.get("/plots/{user_id}/{plot_id}/ndvi-timeseries")
async def ndvi_timeseries(user_id: str, plot_id: int, months: int = 24,
                          me: str = Depends(_api_user)):
    """NDVI รายเดือนจาก GEE ย้อนหลัง n เดือน (max 36)"""
    _require_self(user_id, me)
    months = min(max(months, 1), 36)
    plots = await get_user_plots(user_id)
    plot = next((p for p in plots if p["id"] == plot_id), None)
    if not plot:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้")
    try:
        # sync GEE call — รันใน threadpool กันบล็อก event loop ทั้งเซิร์ฟเวอร์
        series = await run_in_threadpool(get_ndvi_timeseries, plot["lat"], plot["lng"], months=months)
        return {"plot_id": plot_id, "months": months, "series": series}
    except Exception as e:
        logger.error(f"NDVI timeseries error: {e}")
        raise HTTPException(status_code=502, detail="ไม่สามารถดึงข้อมูล NDVI ได้")


@app.delete("/plots/{user_id}/{plot_id}")
async def remove_plot(user_id: str, plot_id: int, me: str = Depends(_api_user)):
    """ลบแปลง — เฉพาะเจ้าของที่ยืนยันตัวตนแล้ว (delete_plot กรอง user_id ซ้ำอีกชั้นใน query)"""
    _require_self(user_id, me)
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


@app.post("/iot/reading", dependencies=[Depends(verify_admin)])
async def iot_reading(req: IotReading):
    """
    Gap 2: รับข้อมูล IoT soil sensor → บันทึก DB → แจ้งเตือน LINE ถ้าความชื้นเกินเกณฑ์

    ต้องใช้ admin key — endpoint นี้สั่ง push ข้อความ LINE ไปหาเจ้าของแปลงได้ เดิมไม่มี
    auth เลย ใครก็ส่งค่าเซนเซอร์ปลอมให้ระบบเตือน "เสี่ยงรากเน่า" หาเกษตรกรได้
    (ถ้ามีเซนเซอร์จริงในอนาคต ควรออก key แยกต่อเซนเซอร์แทนการใช้ admin key)
    """
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

    รันผ่าน run_job_with_catchup (ไม่ใช่เรียก daily_scan_job ตรงๆ) — ถ้า GitHub
    Actions ทิ้งรอบ cron ก่อนหน้าไปเฉยๆ (ยืนยันเกิดจริง 21-22 ส.ค. 2569) รอบนี้จะ
    "ตามงาน" ชั่วโมงที่พลาดไปให้ครบด้วย ไม่ใช่แค่ hour เดียวที่ trigger ส่งมา —
    ดู scheduler.py::run_job_with_catchup

    รันเป็น background task แล้วตอบกลับทันที (ไม่รอผลลัพธ์) เพราะสแกนทุกแปลงของ
    ทุกคนอาจใช้เวลาหลายนาที ยิงยาวเกิน timeout ปกติของ cron ภายนอกได้
    """
    background_tasks.add_task(run_job_with_catchup, daily_scan_job, "daily_scan", hour)
    return {"status": "started", "job": "daily_scan", "hour": hour}


@app.post("/admin/trigger/rain-alert", dependencies=[Depends(verify_admin)])
async def trigger_rain_alert(background_tasks: BackgroundTasks, hour: int | None = None):
    """เหมือน trigger_daily_scan แต่สำหรับงานแจ้งเตือนฝน (rain_alert_job)"""
    background_tasks.add_task(run_job_with_catchup, rain_alert_job, "rain_alert", hour)
    return {"status": "started", "job": "rain_alert", "hour": hour}


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


@app.post("/admin/richmenu/setup", dependencies=[Depends(verify_admin)])
async def setup_richmenu(test_user_id: str | None = None, publish_all: bool = False):
    """
    สร้าง + อัปโหลดรูป Rich Menu หลัก (ตั้งค่า/วาดแปลง/ตรวจสอบ/ติดต่อเจ้าหน้าที่)
    ผ่าน LINE Messaging API ตรงๆ — ใช้รูปจริงที่ผู้ใช้ออกแบบเอง (backend/assets/
    richmenu_main.png, 919x624 — ขนาดตามไฟล์ที่ export มาจริง ไม่ upscale เพื่อ
    กันภาพเบลอ) พิกัดพื้นที่กดทั้ง 4 วัดจากไฟล์จริงด้วย pixel analysis
    (หาขอบกล่องขาวของแต่ละการ์ด) ไม่ใช่ค่ากะคร่าวๆ แล้ว

    เหตุผลที่ต้องยิง API เอง แทนที่จะใช้ LINE Official Account Manager (หน้าเว็บ):
    OA Manager ตั้ง action ต่อช่องได้แค่ "ลิงก์ / ข้อความ / คูปอง" เท่านั้น ไม่มี
    "postback" ให้เลือก — แต่ผู้ใช้ต้องการให้กด "ตั้งค่า"/"ตรวจสอบ" แล้วบอทตอบ
    ในแชททันที (ไม่เปิดเว็บ ไม่ต้องพิมพ์ข้อความเอง) ซึ่งทำได้เฉพาะด้วย postback
    action จริง

    ค่าเริ่มต้น "ไม่กระทบผู้ใช้จริงคนไหนเลย": สร้าง+อัปโหลดริชเมนูไว้เฉยๆ
    (ยังไม่ผูกกับใคร) ต้องส่งพารามิเตอร์อย่างใดอย่างหนึ่งเพิ่มถึงจะมีผล:
      - test_user_id=<LINE userId>  → ผูกกับบัญชีเดียวนั้นเพื่อทดสอบ
        (ไม่กระทบผู้ใช้คนอื่น) ใช้ POST /v2/bot/user/{userId}/richmenu/{id}
      - publish_all=true            → ตั้งเป็นเมนูเริ่มต้นให้ผู้ใช้จริงทุกคนทันที
        (แทนที่เมนูเดิมของทุกคน) — ใช้เมื่อพร้อมเผยแพร่จริงเท่านั้น

    เรียกซ้ำได้เรื่อยๆ ตอนแก้ดีไซน์ — ริชเมนูเก่าจะไม่ถูกลบอัตโนมัติ (สร้างใหม่
    ทุกครั้ง) ถ้าไม่ต้องการอันเก่าซ้อนให้ลบเองใน OA Manager หรือยิง
    DELETE /v2/bot/richmenu/{id}
    """
    import httpx as _httpx

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="LINE_CHANNEL_ACCESS_TOKEN not set")

    liff_url = f"https://liff.line.me/{liff_id()}"

    image_path = _BASE_DIR / "backend" / "assets" / "richmenu_main.png"
    if not image_path.exists():
        image_path = Path(__file__).resolve().parent / "assets" / "richmenu_main.png"
    if not image_path.exists():
        raise HTTPException(status_code=500, detail=f"ไม่พบรูปเมนูที่ {image_path}")

    # พิกัดวัดจากไฟล์จริง (919x624) ด้วย connected-component analysis หา bbox
    # ของกล่องขาวแต่ละใบ — ดู scratchpad/gen_richmenu.py ไม่เกี่ยว, ทำแยกครั้งเดียว
    AREAS = [
        {  # ขวาบน — ตั้งค่า
            "bounds": {"x": 636, "y": 30, "width": 908 - 636, "height": 312 - 30},
            "action": {"type": "postback", "label": "ตั้งค่า", "data": "action=settings",
                       "displayText": "⚙️ เมนูตั้งค่าการแจ้งเตือน"},
        },
        {  # ล่างซ้าย — วาดแปลง
            "bounds": {"x": 18, "y": 327, "width": 288 - 18, "height": 610 - 327},
            "action": {"type": "uri", "label": "วาดแปลง", "uri": f"{liff_url}?tab=new-plot"},
        },
        {  # ล่างกลาง — ตรวจสอบ
            "bounds": {"x": 327, "y": 327, "width": 598 - 327, "height": 609 - 327},
            "action": {"type": "postback", "label": "ตรวจสอบ", "data": "action=history",
                       "displayText": "📋 แปลงของฉัน"},
        },
        {  # ล่างขวา — ติดต่อเจ้าหน้าที่
            "bounds": {"x": 636, "y": 329, "width": 908 - 636, "height": 610 - 329},
            "action": {"type": "uri", "label": "ติดต่อเจ้าหน้าที่", "uri": "tel:0829151870"},
        },
    ]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "size": {"width": 919, "height": 624},
        "selected": True,
        "name": "IAM ROOT เมนูหลัก",
        "chatBarText": "เมนู",
        "areas": AREAS,
    }

    async with _httpx.AsyncClient(timeout=30) as client:
        r = await client.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=body)
        if r.status_code != 200:
            raise HTTPException(status_code=502,
                                detail=f"create richmenu failed: {r.status_code} {r.text[:300]}")
        rich_menu_id = r.json()["richMenuId"]

        r2 = await client.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
            content=image_path.read_bytes(),
        )
        if r2.status_code != 200:
            raise HTTPException(status_code=502,
                                detail=f"upload image failed: {r2.status_code} {r2.text[:300]} (richMenuId={rich_menu_id} — ลบทิ้งเองถ้าจำเป็น)")

        linked_to = None
        if test_user_id:
            r3 = await client.post(
                f"https://api.line.me/v2/bot/user/{test_user_id}/richmenu/{rich_menu_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r3.status_code != 200:
                raise HTTPException(status_code=502,
                                    detail=f"link to test user failed: {r3.status_code} {r3.text[:300]} (richMenuId={rich_menu_id})")
            linked_to = f"test_user:{test_user_id}"
        elif publish_all:
            r3 = await client.post(
                f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r3.status_code != 200:
                raise HTTPException(status_code=502,
                                    detail=f"set default failed: {r3.status_code} {r3.text[:300]} (richMenuId={rich_menu_id})")
            linked_to = "all_users"

    logger.info(f"Rich menu created: {rich_menu_id} (linked_to={linked_to})")
    return {"status": "ok", "richMenuId": rich_menu_id, "linked_to": linked_to}


# ── Static file mounts (MUST be after all API routes) ────
app.mount("/liff", StaticFiles(directory=str(_BASE_DIR / "liff"), html=True), name="liff")
app.mount("/dashboard", StaticFiles(directory=str(_BASE_DIR / "dashboard"), html=True), name="dashboard")
