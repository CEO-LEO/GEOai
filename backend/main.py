import os
import json
import csv
import asyncio
import logging
import tempfile
import io
import time
import base64
import binascii
import secrets
from datetime import date, datetime, timedelta, timezone
import traceback
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal

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
import risk_v2
import risk_display
import crops as crops_mod
import boundary
import recommendations
import plot_summary
import report_service
import access
import areas
import privacy
import photo_service
import validation
import province
import seasons
import trees
from crop_classifier import service as crop_service, store as crop_store, evaluate as crop_evaluate, \
    labels as crop_labels, references as crop_references
from plot_image_service import build_plot_grid_image_url
from database import (save_analysis, get_all_reports, save_plot,
                      get_user_plots, get_plot_history, set_notify,
                      delete_plot, find_nearby_plot, seed_demo_data,
                      upsert_user, save_iot_reading, get_plot_by_id,
                      save_field_observation, get_persistent_wet_points,
                      set_notify_digest, get_latest_risk_by_plots, get_user,
                      get_recent_message_failures, get_scheduler_last_run,
                      set_scheduler_last_run, get_shadow_rows,
                      update_plot, set_plot_crops, archive_plot_boundary,
                      merge_plot_history, get_active_plots, get_plot_crop_ids,
                      list_recommendation_rules, save_recommendation_rule,
                      get_recommendation_rule_history, get_latest_plot_signals,
                      get_user_access, get_share_flags, get_plots_for_officer, list_users_admin,
                      set_user_role, create_invite, get_invite, record_invite_use, list_invites,
                      expire_invite, log_audit, list_audit_logs, log_event,
                      get_user_privacy, save_consent, set_deletion_request, export_user_data, purge_user,
                      SUPABASE_URL as _SUPABASE_URL, SUPABASE_KEY as _SUPABASE_KEY,
                      get_photo_by_client_id, insert_photo, list_photos, get_photo, delete_photo_row,
                      photo_paths_for_user, insert_validation, list_validations,
                      get_events_since, get_users_created, set_rain_alert_freq,
                      get_tree, get_tree_by_client_id, list_trees, insert_tree, update_tree,
                      get_tree_log, get_tree_log_by_client_id, list_tree_logs, insert_tree_log, delete_tree_log)
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


async def _access_for(user_id: str) -> dict:
    hit = access.cached(user_id)
    return hit if hit is not None else access.remember(user_id, await get_user_access(user_id))


async def _require_plot_access(plot_id: int, user_id: str, action: str = access.VIEW,
                               audit_action: str | None = None) -> dict:
    """
    Phase 2a: เจ้าของ / เจ้าหน้าที่ในพื้นที่ (เจ้าของยินยอมแบ่งปัน) / ผู้ดูแล — ดู access.plot_access_reason
    ไม่มีสิทธิ์ตอบ 404 เหมือน "ไม่มีแปลงนี้" · ทุกการเข้าถึงแปลงของคนอื่นบันทึกใน audit_logs (PDPA)
    """
    plot = await get_plot_by_id(plot_id)
    if not plot:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้")
    if plot.get("user_id") == user_id:
        return plot
    acc = await _access_for(user_id)
    owner_shares = False
    if acc["role"] == "officer" and plot.get("created_by") != user_id:
        owner_shares = (await get_share_flags([plot["user_id"]])).get(plot["user_id"], False)
    reason = access.plot_access_reason(acc, user_id, plot, owner_shares, action)
    if not reason:
        raise HTTPException(status_code=404, detail="ไม่พบแปลงนี้")
    await log_audit(user_id, acc["role"], audit_action or f"{action}_plot", "plot", plot_id,
                    plot.get("user_id"), {"reason": reason})
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

    # 1f: ภาษาไทยในรูป/PDF วางสระ-วรรณยุกต์ถูกต้องเมื่อ Pillow มี raqm — ดูได้ที่ /health (text_layout)
    if _text_layout() == "basic":
        logger.warning("Pillow text layout: basic (no raqm) — Thai tone marks in images/PDF may be misplaced")

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
    # 1c: ตรวจแปลงเดิมซ้ำ → ส่ง plot_id มา (ไม่ต้องหาแปลงใกล้เคียงซ้ำ) · แปลงใหม่ส่งชนิดพืช/ประเภทสวนมาได้
    plot_id:   int | None = Field(None, ge=1)
    plot_type: Literal["flat", "raised_bed", "vegetable_bed"] | None = None
    crop_ids:  list[str] | None = Field(None, max_length=crops_mod.MAX_CROPS_PER_PLOT)
    # 2a: เจ้าหน้าที่วาดแปลงแทนเกษตรกร — ชื่อเกษตรกร (ข้อมูลส่วนบุคคล เก็บเฉพาะเมื่อผู้วาดเป็นเจ้าหน้าที่)
    owner_label: str | None = Field(None, max_length=60)


class BoundaryCheckRequest(BaseModel):
    lat:       float = Field(..., ge=-90, le=90)
    lng:       float = Field(..., ge=-180, le=180)
    polygon:   list[list[float]] | None = None
    plot_type: Literal["flat", "raised_bed", "vegetable_bed"] | None = None
    crop_ids:  list[str] | None = Field(None, max_length=crops_mod.MAX_CROPS_PER_PLOT)


class PlotUpdateRequest(BaseModel):
    """แก้แปลงเดิม — ส่งเฉพาะช่องที่เปลี่ยน · แก้ขอบเขตแล้วประวัติวิเคราะห์ยังอยู่กับแปลงเดิม"""
    name:      str | None = Field(None, min_length=1, max_length=50)
    plot_type: Literal["flat", "raised_bed", "vegetable_bed"] | None = None
    crop_ids:  list[str] | None = Field(None, max_length=crops_mod.MAX_CROPS_PER_PLOT)
    polygon:   list[list[float]] | None = Field(None, min_length=4)
    area_rai:  float | None = Field(None, ge=0, le=10000)


class PlotMergeRequest(BaseModel):
    source_plot_id: int = Field(..., ge=1)


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


def _text_layout() -> str:
    try:
        from PIL import features
        return "raqm" if features.check("raqm") else "basic"
    except Exception:
        return "unavailable"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "IAM ROOT", "cache": cache_stats(), "text_layout": _text_layout()}


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


@app.get("/admin/risk-shadow", dependencies=[Depends(verify_admin)])
async def admin_risk_shadow(days: int = 7, format: str = "json"):
    """
    เทียบระดับความเสี่ยงวิธีเดิม vs วิธีใหม่ (รันคู่) ในช่วง n วันล่าสุด — ใช้ทำรายงานรายสัปดาห์
    format=markdown คืนตารางภาษาไทยพร้อมวางในรายงาน (ไม่มีพิกัด/ชื่อเจ้าของ)
    """
    days = min(max(days, 1), 60)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    summary = risk_v2.summarize_shadow(await get_shadow_rows(since))
    if format == "markdown":
        return Response(content=risk_v2.shadow_markdown(summary, days),
                        media_type="text/markdown; charset=utf-8")
    return summary


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
        f'  RISK_DISPLAY: "{"interim" if risk_display.interim() else "legacy"}",\n'
        f'  AGRONOMY_NUMBERS: "{"on" if risk_display.agronomy_numbers() else "off"}",\n'
        f"}};\n"
    )
    return Response(content=js, media_type="application/javascript")


# ─────────────────────────────────────────────────────────
# Plot grid image — สำหรับส่งภาพแผนที่ความชื้นเข้า LINE OA คู่กับผลวิเคราะห์
# (สร้าง + อัปโหลดขึ้น Supabase Storage อยู่ใน plot_image_service.py ทั้งหมดแล้ว —
# ไม่มี endpoint เสิร์ฟภาพของแอปเองอีกต่อไป เพราะ URL ที่ได้เป็น public URL ของ
# Supabase Storage ตรงๆ ดูเหตุผลที่เลิกใช้แคชในแรมของโปรเซสเองที่ไฟล์นั้น)
# ─────────────────────────────────────────────────────────

BOUNDARY_BACKGROUND_TIMEOUT_S = 30   # ตรวจขอบเขตเบื้องหลัง (หลังบันทึก/แก้แปลง) ไม่มีใครรอ


SUPABASE_CONFIGURED = bool(_SUPABASE_URL and _SUPABASE_KEY)   # dev/test ไม่มี Supabase → ข้ามการตรวจความยินยอม


def _validated_crop_ids(crop_ids: list[str] | None) -> list[str]:
    try:
        return crops_mod.validate_ids(crop_ids)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def _check_boundary(lat: float, lng: float, polygon: list | None,
                          crop_ids: list[str] | None, plot_type: str | None,
                          timeout_s: float | None = None) -> dict:
    """
    ตรวจคุณภาพขอบเขต (docs/v2/boundary-quality-warnings.md) — ช้าเกิน/พัง = ไม่ถามผู้ใช้ (checked=False)
    คำเตือนที่ไม่ต้องใช้ดาวเทียม (ปักจุดเดียว / ขอบเขตเล็กมาก) ยังขึ้นได้แม้ GEE ไม่ตอบ
    timeout_s: ค่าเริ่มต้นจาก config (ผู้ใช้รออยู่) · งานเบื้องหลังให้เวลานานกว่าได้
    """
    profile: dict = {}
    try:
        profile = await asyncio.wait_for(
            run_in_threadpool(risk_v2.get_boundary_land_cover, lat, lng, polygon),
            timeout=timeout_s or boundary.rules()["check_timeout_s"])
    except Exception as e:
        logger.warning(f"boundary check skipped ({type(e).__name__}): {e}")
    checked = profile.get("status") == "ok"
    is_point = not (polygon and len(polygon) >= 3)
    area_rai = profile.get("area_rai")
    if area_rai is None and not is_point:
        area_rai = boundary.polygon_area_rai(polygon)
    warnings = boundary.evaluate(profile.get("land_cover") if checked else None, is_point=is_point,
                                 area_rai=area_rai, crop_ids=crop_ids, plot_type=plot_type)
    return {"checked": checked, "warnings": warnings, "land_cover": profile.get("land_cover"),
            "area_rai": area_rai, "method": profile.get("method")}


async def _store_boundary_check(plot_id: int, lat: float, lng: float, polygon: list | None,
                                crop_ids: list[str] | None, plot_type: str | None) -> dict | None:
    """เก็บผลตรวจขอบเขตใน plots.boundary_check (ใช้ทำแถบบนการ์ดแปลง) — ไม่เก็บถ้าตรวจไม่สำเร็จ"""
    try:
        result = await _check_boundary(lat, lng, polygon, crop_ids, plot_type,
                                       timeout_s=BOUNDARY_BACKGROUND_TIMEOUT_S)
        if not result["checked"] and not result["warnings"]:
            return None
        stored = boundary.summarize_for_storage(result, result["warnings"])
        stored["checked_at"] = datetime.now(timezone.utc).isoformat()
        await update_plot(plot_id, {"boundary_check": stored})
        return stored
    except Exception as e:   # งานเบื้องหลัง — ห้ามทำให้คำขอหลักหรือ worker พัง
        logger.error(f"store boundary check failed plot={plot_id}: {type(e).__name__}: {e}")
        return None


@app.get("/crops")
async def list_crops():
    """รายการพืช + ประเภทสวนสำหรับหน้าเลือกในแอป (จาก config/crops.json ไม่มีข้อมูลส่วนบุคคล)"""
    return {"crops": [{"id": c["id"], "name_th": c["name_th"], "group": c["group"]}
                      for c in crops_mod.all_crops()],
            "plot_types": [{"id": k, "name_th": v} for k, v in crops_mod.PLOT_TYPE_LABELS_TH.items()]}


@app.post("/plots/boundary-check")
async def plots_boundary_check(req: BoundaryCheckRequest, me: str = Depends(_api_user)):
    """เรียกตอนกดบันทึกแปลง — คืนคำเตือน (ถ้ามี) ผู้ใช้เลือก "บันทึกตามนี้" ได้เสมอ"""
    crop_ids = _validated_crop_ids(req.crop_ids)
    result = await _check_boundary(req.lat, req.lng, req.polygon, crop_ids, req.plot_type)
    return {"checked": result["checked"], "warnings": result["warnings"], "area_rai": result["area_rai"]}


@app.post("/analyze")
async def analyze(req: AnalysisRequest, background: BackgroundTasks,
                  user_id: str = Depends(_analyze_user)):
    """
    วิเคราะห์แปลงของผู้ใช้ที่ยืนยันตัวตนแล้ว (user จาก LINE token เท่านั้น)

    Phase 1b: ไม่ push การ์ดผลเข้าแชท LINE แล้ว — ผู้ใช้กดเองในแอปและเห็นผลบนหน้าจอ
    อยู่แล้ว push นี้กินโควตา LINE OA 1 ข้อความทุกครั้งที่กดตรวจ (แพลน Free มีแค่ 300/เดือน)
    ผู้ใช้ยังดูการ์ดในแชทได้จากปุ่ม "ตรวจสอบ" ใน Rich Menu (ส่งแบบ reply ไม่นับโควตา)
    """
    req.user_id = user_id
    await _require_consent(user_id)
    crop_ids = _validated_crop_ids(req.crop_ids)
    logger.info(f"Analyzing plot: lat={req.lat}, lng={req.lng}, user={req.user_id}")
    existing_plot = await _require_plot_access(req.plot_id, user_id, access.EDIT, "analyze_plot") if req.plot_id else None
    try:
        data     = await _run_gee_analysis(req.lat, req.lng, polygon=req.polygon)
        message  = format_message(data, req.lat, req.lng)

        # บันทึก user (upsert) + แปลง ก่อน — ต้องรู้ plot_id ก่อนถึงจะฝากให้
        # build_plot_grid_image_url() เก็บรูปย่อของแปลงนี้ถาวรได้ (ดูเหตุผลเต็มที่
        # plot_image_service.py) สลับลำดับมาก่อนขั้นตอนสร้างรูปด้านล่าง
        await upsert_user(req.user_id, req.display_name)
        if existing_plot:
            plot_id = existing_plot["id"]
            plot_type = existing_plot.get("plot_type")
        else:
            acc = await _access_for(user_id)
            label = (req.owner_label or "").strip() or None
            plot_id = await save_plot(req.user_id, req.lat, req.lng,
                                      req.plot_name, req.area_rai,
                                      polygon=req.polygon, plot_type=req.plot_type,
                                      extra={"created_by": user_id,
                                             **({"owner_label": label} if label and acc["role"] != "farmer" else {})})
            plot_type = req.plot_type
            if plot_id and crop_ids:
                await set_plot_crops(plot_id, crop_ids)
            if plot_id:
                background.add_task(_store_boundary_check, plot_id, req.lat, req.lng,
                                    req.polygon, crop_ids, plot_type)
                background.add_task(_assign_area, plot_id, req.lat, req.lng)
                background.add_task(log_event, user_id, "plot_create", plot_id, {"role": acc["role"]})

        # แปลงที่วาดขอบเขตไว้ (polygon) — สร้างรูปแผนที่ + รูปย่อของแปลง (รูปย่อใช้ในหน้า
        # "แปลงของฉัน") ล้มเหลวได้โดยไม่กระทบคำขอหลัก ส่ง image_url/problem_points
        # กลับไปใน response ให้หน้าแอปใช้ได้ (ไม่ push เข้า LINE แล้ว — ดู docstring)
        image_url = None
        problem_points = []
        if req.polygon and len(req.polygon) >= 3:
            img_result = await build_plot_grid_image_url(req.polygon, req.plot_name, plot_id=plot_id,
                                                         plot_type=plot_type)
            image_url = img_result["url"]
            problem_points = img_result["problem_points"]

        await save_analysis(req.user_id, data, message, plot_id=plot_id)
        background.add_task(log_event, user_id, "analyze", plot_id,
                            {"has_polygon": bool(req.polygon and len(req.polygon) >= 3)})
        return {"status": "ok", "plot_id": plot_id, "plot_type": plot_type, "data": data,
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
    plot_type: Literal["flat", "raised_bed", "vegetable_bed"] | None = None   # 1e: ประโยคสรุปของสวนยกร่อง


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
        # 1e: ประโยคสรุป + คำอธิบายสี (เทมเพลตจากข้อมูลจริง) — พังได้โดยไม่กระทบตารางจุด
        try:
            summary = await run_in_threadpool(plot_summary.summarize, points, req.polygon,
                                              req.plot_type, actual_spacing_m)
        except Exception as e:
            logger.warning(f"grid summary failed: {type(e).__name__}: {e}")
            summary = {"summary_th": None, "lines": [], "color_note_th": plot_summary.COLOR_NOTE_TH}
        return {"status": "ok", "count": len(points), "spacing_m": actual_spacing_m, "points": points,
                "summary_th": summary["summary_th"], "summary_lines": summary["lines"],
                "color_note_th": summary["color_note_th"]}
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


@app.get("/user/{user_id}/notify-settings")
async def user_notify_settings(user_id: str, me: str = Depends(_api_user)):
    """การตั้งค่าแจ้งเตือนปัจจุบัน (Phase 3a) — เตือนฝน / ความถี่ / สรุปรายสัปดาห์"""
    _require_self(user_id, me)
    u = await get_user(me) or {}
    return {"rain_alerts": bool(u.get("notify_weekly", True)),
            "rain_alert_freq": u.get("rain_alert_freq") or "normal",
            "weekly_digest": bool(u.get("notify_daily_digest", False)),
            "freq_options": [{"id": "normal", "name_th": "ทุกครั้งที่ฝนหนัก (ไม่ซ้ำระดับเดิมใน 7 วัน)"},
                             {"id": "weekly_max", "name_th": "สูงสุดสัปดาห์ละครั้ง"}]}


@app.patch("/user/{user_id}/rain-alert-freq")
async def user_rain_alert_freq(user_id: str, mode: Literal["normal", "weekly_max"], me: str = Depends(_api_user)):
    _require_self(user_id, me)
    if not await set_rain_alert_freq(me, mode):
        raise HTTPException(status_code=500, detail="อัพเดตการตั้งค่าไม่สำเร็จ")
    return {"user_id": me, "rain_alert_freq": mode}


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
    await _require_plot_access(plot_id, me)
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
    await _require_plot_access(plot_id, me)
    days = min(max(days, 3), 90)
    points = await get_persistent_wet_points(plot_id, days=days)
    return {"plot_id": plot_id, "days": days, "count": len(points), "points": points}


@app.get("/plots/{user_id}/{plot_id}/ndvi-timeseries")
async def ndvi_timeseries(user_id: str, plot_id: int, months: int = 24,
                          me: str = Depends(_api_user)):
    """NDVI รายเดือนจาก GEE ย้อนหลัง n เดือน (max 36)"""
    _require_self(user_id, me)
    months = min(max(months, 1), 36)
    plot = await _require_plot_access(plot_id, me)
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


PLOT_MAX_AREA_RAI = 1000   # ตรงกับ LIFF (applyPlotSizeGuard) — ใหญ่กว่านี้คือวาดผิดชัดเจน


def _closed_ring(polygon: list[list[float]]) -> list[list[float]]:
    ring = [list(p[:2]) for p in polygon]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise HTTPException(status_code=422, detail="ขอบเขตต้องมีอย่างน้อย 3 จุด")
    return ring


@app.patch("/plots/{user_id}/{plot_id}")
async def edit_plot(user_id: str, plot_id: int, req: PlotUpdateRequest,
                    background: BackgroundTasks, me: str = Depends(_api_user)):
    """
    แก้แปลงเดิม (ชื่อ / ประเภทสวน / ชนิดพืช / ขอบเขต) — ประวัติวิเคราะห์ยังอยู่กับ plot_id เดิม
    แก้ขอบเขต: เก็บขอบเขตเดิมไว้ใน plot_boundary_history แล้วตรวจคุณภาพขอบเขตใหม่เบื้องหลัง
    """
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "edit_plot")
    fields: dict = {}
    if req.name is not None:
        fields["name"] = req.name.strip()
    if "plot_type" in req.model_fields_set:
        fields["plot_type"] = req.plot_type          # ส่ง null มา = ล้างค่า (ยังไม่ระบุ)
    crop_ids = _validated_crop_ids(req.crop_ids) if req.crop_ids is not None else None
    if req.polygon is not None:
        ring = _closed_ring(req.polygon)
        area = boundary.polygon_area_rai(ring)
        if area is not None and area > PLOT_MAX_AREA_RAI:
            raise HTTPException(status_code=422, detail=f"ขอบเขตใหญ่ผิดปกติ ~{area:.0f} ไร่ กรุณาวาดใหม่ให้ตรงกับสวนจริง")
        await archive_plot_boundary(plot)
        verts = ring[:-1]
        fields.update({"polygon": ring,
                       "lat": sum(p[1] for p in verts) / len(verts),
                       "lng": sum(p[0] for p in verts) / len(verts)})
        if req.area_rai is not None:
            fields["area_rai"] = req.area_rai
    elif req.area_rai is not None:
        fields["area_rai"] = req.area_rai

    if fields:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not await update_plot(plot_id, fields):
            raise HTTPException(status_code=502, detail="บันทึกการแก้ไขไม่สำเร็จ กรุณาลองใหม่")
    if crop_ids is not None and not await set_plot_crops(plot_id, crop_ids):
        raise HTTPException(status_code=502, detail="บันทึกชนิดพืชไม่สำเร็จ กรุณาลองใหม่")

    new_polygon = fields.get("polygon", plot.get("polygon"))
    new_type = fields.get("plot_type", plot.get("plot_type"))
    if req.polygon is not None or crop_ids is not None or "plot_type" in fields:
        final_crops = crop_ids if crop_ids is not None else await get_plot_crop_ids(plot_id)
        background.add_task(_store_boundary_check, plot_id, fields.get("lat", plot["lat"]),
                            fields.get("lng", plot["lng"]), new_polygon, final_crops, new_type)
    if req.polygon is not None:
        background.add_task(_assign_area, plot_id, fields["lat"], fields["lng"])
        # รูปย่อในหน้า "แปลงของฉัน" ให้ตรงกับขอบเขตใหม่ (ทำเบื้องหลัง ใช้ GEE นาน)
        background.add_task(build_plot_grid_image_url, new_polygon, fields.get("name", plot.get("name", "")),
                            plot_id=plot_id, plot_type=new_type)
    return {"status": "ok", "plot_id": plot_id, "updated": sorted(set(fields) - {"updated_at"})
            + (["crop_ids"] if crop_ids is not None else [])}


@app.post("/plots/{user_id}/{plot_id}/merge")
async def merge_plot(user_id: str, plot_id: int, req: PlotMergeRequest, me: str = Depends(_api_user)):
    """
    ย้ายประวัติจากแปลงซ้ำ (source_plot_id — เช่นแปลงที่เคยลบ-วาดใหม่) มารวมที่แปลงนี้
    แปลงต้นทางถูกปิดใช้งาน ไม่ลบ (ย้อนกลับได้ — supabase/rollback_v14.sql)
    """
    _require_self(user_id, me)
    if req.source_plot_id == plot_id:
        raise HTTPException(status_code=422, detail="เลือกแปลงเดียวกันไม่ได้")
    await _require_own_plot(plot_id, me)
    await _require_own_plot(req.source_plot_id, me)
    if not await merge_plot_history(me, req.source_plot_id, plot_id):
        raise HTTPException(status_code=502, detail="ย้ายประวัติไม่สำเร็จ กรุณาลองใหม่")
    return {"status": "merged", "plot_id": plot_id, "source_plot_id": req.source_plot_id}


# ─────────────────────────────────────────────────────────
# คำแนะนำ "แก้อย่างไร" (Phase 1d) — มาจากตาราง recommendation_rules ที่คนเขียน/ตรวจเท่านั้น
# ─────────────────────────────────────────────────────────

@app.get("/recommendations/plot/{user_id}/{plot_id}")
async def plot_recommendations(user_id: str, plot_id: int, background: BackgroundTasks,
                               me: str = Depends(_api_user)):
    """คำแนะนำของแปลง: ปัญหาระดับแปลงที่พบตอนนี้ + คำแนะนำสำหรับแตะจุดบนแผนที่ (ทุกข้อบอกสถานะร่าง/ตรวจแล้ว)"""
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="view_recommendations")
    crop_ids, signals = await asyncio.gather(get_plot_crop_ids(plot_id), get_latest_plot_signals(plot_id))
    rules = await recommendations.get_rules()
    out = recommendations.for_plot(rules, crop_ids, plot.get("plot_type"), signals)
    out["status_label_th"] = risk_v2.anomaly_label_th((signals or {}).get("risk_v2"))
    background.add_task(log_event, me, "view_recommendation", plot_id, {"plot_problems": len(out["plot"])})
    return out


REPORT_TIMEOUT_S = 90   # ดึงตารางจุด + ภาพดาวเทียม (GEE) + วาด PDF


@app.post("/report/plot/{user_id}/{plot_id}")
async def plot_report(user_id: str, plot_id: int, background: BackgroundTasks,
                      me: str = Depends(_analyze_user)):
    """
    รายงานแปลงสำหรับแชร์ (1f): PDF (ลิงก์ใช้ได้ 7 วัน) + การ์ดสำหรับ liff.shareTargetPicker
    จำกัดคำขอแบบเดียวกับ /analyze เพราะเรียก GEE หนัก
    """
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="create_report")
    crop_ids, signals = await asyncio.gather(get_plot_crop_ids(plot_id), get_latest_plot_signals(plot_id))
    recs = recommendations.for_plot(await recommendations.get_rules(), crop_ids, plot.get("plot_type"), signals)
    recs["status_label_th"] = risk_v2.anomaly_label_th((signals or {}).get("risk_v2"))
    crops_line = " · ".join(x for x in (", ".join(crops_mod.names_th(crop_ids)),
                                        crops_mod.PLOT_TYPE_LABELS_TH.get(plot.get("plot_type") or "", "")) if x)
    try:
        out = await asyncio.wait_for(
            report_service.build_plot_report(plot, crops_line, recs, f"https://liff.line.me/{liff_id()}"),
            timeout=REPORT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="สร้างรายงานใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง")
    if not out["pdf_url"] and not out["image_url"]:
        raise HTTPException(status_code=502, detail="สร้างรายงานไม่สำเร็จ กรุณาลองใหม่ภายหลัง")
    background.add_task(log_event, me, "create_report", plot_id, {"pdf": bool(out["pdf_url"])})
    return out


class RuleBody(BaseModel):
    crop:       str = Field("*", max_length=40)
    problem:    str = Field(..., max_length=40)
    severity:   str = Field(..., max_length=10)
    title_th:   str = Field(..., max_length=200)
    reason_th:  str = Field("", max_length=500)
    actions_th: list[str] = Field(..., max_length=10)
    source:     str = Field("", max_length=300)
    removed:    str = Field("", max_length=300)
    editor:     str = Field(..., min_length=2, max_length=80, description="ชื่อผู้แก้ (บันทึกในประวัติ)")


class RuleStatusBody(BaseModel):
    status: Literal["draft", "reviewed", "retired"]
    editor: str = Field(..., min_length=2, max_length=80, description="ชื่อผู้ตรวจ/ผู้เปลี่ยนสถานะ")


def _clean_rule(body: RuleBody) -> dict:
    try:
        return recommendations.validate_rule(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/admin/recommendations", dependencies=[Depends(verify_admin)])
async def admin_list_rules():
    rows = await list_recommendation_rules()
    return {"source": "db" if rows is not None else "seed",
            "rules": rows if rows is not None else list(recommendations.seed_rules()),
            "problems": recommendations.PROBLEMS_TH, "severities": recommendations.SEVERITY_TH,
            "crops": [{"id": c["id"], "name_th": c["name_th"], "group": c["group"]} for c in crops_mod.all_crops()]}


@app.post("/admin/recommendations", dependencies=[Depends(verify_admin)])
async def admin_create_rule(body: RuleBody):
    row = await save_recommendation_rule(None, {**_clean_rule(body), "status": "draft"}, "create", body.editor.strip())
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ (ยังไม่ได้รัน migrate_v15?)")
    recommendations.invalidate()
    return row


@app.put("/admin/recommendations/{rule_id}", dependencies=[Depends(verify_admin)])
async def admin_edit_rule(rule_id: int, body: RuleBody):
    """แก้ข้อความ → กลับเป็น "ร่าง" เสมอ (ต้องตรวจใหม่) — กันข้อความที่ยังไม่ตรวจขึ้นป้าย "ตรวจแล้ว" """
    fields = {**_clean_rule(body), "status": "draft", "reviewed_by": None, "reviewed_at": None}
    row = await save_recommendation_rule(rule_id, fields, "edit", body.editor.strip())
    if not row:
        raise HTTPException(status_code=404, detail="ไม่พบกฎนี้")
    recommendations.invalidate()
    return row


@app.post("/admin/recommendations/{rule_id}/status", dependencies=[Depends(verify_admin)])
async def admin_rule_status(rule_id: int, body: RuleStatusBody):
    editor = body.editor.strip()
    if body.status == "reviewed":
        fields = {"status": "reviewed", "reviewed_by": editor, "reviewed_at": datetime.now(timezone.utc).isoformat()}
        action = "approve"
    else:
        fields = {"status": body.status, "reviewed_by": None, "reviewed_at": None}
        action = "retire" if body.status == "retired" else "restore"
    row = await save_recommendation_rule(rule_id, fields, action, editor)
    if not row:
        raise HTTPException(status_code=404, detail="ไม่พบกฎนี้")
    recommendations.invalidate()
    return row


@app.get("/admin/recommendations/{rule_id}/history", dependencies=[Depends(verify_admin)])
async def admin_rule_history(rule_id: int):
    return {"rule_id": rule_id, "history": await get_recommendation_rule_history(rule_id)}


@app.get("/admin/recommendations/export.csv", dependencies=[Depends(verify_admin)])
async def admin_rules_export():
    """ส่งให้เกษตรจังหวัดตรวจ — เปิดใน Excel ได้ (UTF-8 BOM)"""
    rows = await list_recommendation_rules()
    rules = rows if rows is not None else list(recommendations.seed_rules())
    names = {c["id"]: c["name_th"] for c in crops_mod.all_crops()}
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["รหัส", "พืช", "ปัญหา", "ความรุนแรง", "หัวข้อ", "เหตุผล", "วิธีแก้", "สถานะ", "ตรวจโดย",
                "ที่มา", "ตัดออกจากข้อความเดิม", "ความเห็นผู้ตรวจ"])
    for r in rules:
        crop = r["crop"]
        crop_th = "ทุกพืช" if crop == "*" else ("กลุ่มผัก" if crop == "group:vegetable" else names.get(crop, crop))
        w.writerow([r.get("id"), crop_th, recommendations.PROBLEMS_TH.get(r["problem"], r["problem"]),
                    recommendations.SEVERITY_TH.get(r["severity"], r["severity"]), r["title_th"],
                    r.get("reason_th", ""), "\n".join(f"- {a}" for a in r.get("actions_th") or []),
                    {"draft": "ร่าง", "reviewed": "ตรวจแล้ว", "retired": "เลิกใช้"}.get(r.get("status"), r.get("status")),
                    r.get("reviewed_by") or "", r.get("source", ""), r.get("removed", ""), ""])
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="recommendations-for-review.csv"'})


# ─────────────────────────────────────────────────────────
# Phase 2a — บทบาท / พื้นที่ / รหัสเชิญ / audit / สถิติการใช้งาน
# ─────────────────────────────────────────────────────────

async def _assign_area(plot_id: int, lat: float, lng: float) -> dict | None:
    """อำเภอของแปลง (งานเบื้องหลัง) — พังได้โดยไม่กระทบผู้ใช้"""
    try:
        found = await asyncio.wait_for(run_in_threadpool(areas.lookup_point, lat, lng), timeout=30)
        if found.get("area_name"):
            await update_plot(plot_id, found)
        return found
    except Exception as e:
        logger.warning(f"assign area failed plot={plot_id}: {type(e).__name__}")
        return None


def _area_list(codes: list[str]) -> list[dict]:
    return [{"code": c, "name_th": areas.name_th(c) or c} for c in codes]


@app.get("/me")
async def me_info(me: str = Depends(_api_user)):
    """บทบาทและพื้นที่ของผู้ใช้ (ใช้แสดงแท็บเจ้าหน้าที่ในแอป)"""
    acc = await _access_for(me)
    return {"user_id": me, "role": acc["role"], "role_th": access.ROLE_TH.get(acc["role"], acc["role"]),
            "areas": _area_list(acc["areas"]), "share_with_officers": acc.get("share_with_officers", False),
            "privacy": privacy.consent_payload(await get_user_privacy(me)) if SUPABASE_CONFIGURED
            else {**privacy.consent_payload(None), "needs_consent": False}}


# ── PDPA (Phase 2b) ──
async def _require_consent(user_id: str) -> None:
    """
    สร้าง/วิเคราะห์แปลงได้เมื่อยอมรับนโยบายฉบับปัจจุบันแล้ว และไม่ได้ขอลบข้อมูลไว้
    dev/test ที่ไม่มี Supabase ข้ามการตรวจ (ไม่มีที่เก็บความยินยอม)
    """
    if not SUPABASE_CONFIGURED:
        return
    u = await get_user_privacy(user_id)
    if u and u.get("deletion_requested_at"):
        raise HTTPException(status_code=409, detail="คุณขอลบข้อมูลไว้ — ยกเลิกคำขอในเมนู \"ข้อมูลของฉัน\" ก่อนจึงใช้งานต่อได้")
    if privacy.consent_payload(u)["needs_consent"]:
        raise HTTPException(status_code=428, detail="กรุณาอ่านและยอมรับนโยบายความเป็นส่วนตัวก่อนใช้งาน")


class ConsentBody(BaseModel):
    version:             str = Field(..., max_length=20)
    share_with_officers: bool = False
    photos_research:     bool = False


@app.get("/me/privacy")
async def me_privacy(me: str = Depends(_api_user)):
    return privacy.consent_payload(await get_user_privacy(me))


@app.post("/me/consent")
async def me_consent(body: ConsentBody, me: str = Depends(_api_user)):
    """ยอมรับนโยบาย + เลือกข้อที่ไม่บังคับ (เปลี่ยนภายหลังได้) — บันทึกประวัติทุกครั้ง"""
    if body.version != privacy.PRIVACY_VERSION:
        raise HTTPException(status_code=409, detail="นโยบายมีฉบับใหม่ กรุณาเปิดแอปใหม่แล้วอ่านอีกครั้ง")
    before = await get_user_privacy(me)
    if not before:
        await upsert_user(me, "")
    action = "update" if before and before.get("consent_version") == body.version else "accept"
    if not await save_consent(me, body.version, body.share_with_officers, body.photos_research, action):
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    access.invalidate(me)
    await log_audit(me, "farmer", f"consent_{action}", "user", me, me,
                    {"share_with_officers": body.share_with_officers, "photos_research": body.photos_research})
    return privacy.consent_payload(await get_user_privacy(me))


@app.get("/me/data")
async def me_data(me: str = Depends(_api_user)):
    """ดาวน์โหลดข้อมูลทั้งหมดของฉัน (JSON) รวมรายการว่าใครเคยเปิดดูแปลงของฉัน"""
    data = await export_user_data(me)
    await log_audit(me, "farmer", "export_data", "user", me, me, None)
    return Response(content=json.dumps(data, ensure_ascii=False, indent=1, default=str),
                    media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="iamroot-my-data.json"'})


@app.get("/me/access-log")
async def me_access_log(me: str = Depends(_api_user)):
    """ใครเปิดดู/แก้แปลงของฉัน 90 วันล่าสุด (บอกบทบาท ไม่บอกตัวบุคคลของเจ้าหน้าที่)"""
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    rows = [r for r in await list_audit_logs(since, me, limit=200) if r.get("actor_user_id") != me]
    return {"entries": [{"role_th": access.ROLE_TH.get(r["actor_role"], r["actor_role"]), "action": r["action"],
                         "plot_id": r.get("target_id") if r.get("target_type") == "plot" else None,
                         "at": r["created_at"]} for r in rows]}


@app.post("/me/deletion-request")
async def me_deletion_request(me: str = Depends(_api_user)):
    """ขอลบข้อมูล: ซ่อนแปลงและหยุดแจ้งเตือนทันที ยกเลิกได้ภายใน 30 วัน หลังจากนั้นผู้ดูแลลบถาวร"""
    if not await set_deletion_request(me, True, privacy.PRIVACY_VERSION):
        raise HTTPException(status_code=502, detail="บันทึกคำขอไม่สำเร็จ กรุณาลองใหม่")
    access.invalidate(me)
    await log_audit(me, "farmer", "deletion_request", "user", me, me, None)
    return {"status": "requested", "grace_days": privacy.DELETION_GRACE_DAYS}


@app.post("/me/deletion-request/cancel")
async def me_deletion_cancel(me: str = Depends(_api_user)):
    u = await get_user_privacy(me)
    if not u or not u.get("deletion_requested_at"):
        raise HTTPException(status_code=404, detail="ไม่มีคำขอลบข้อมูล")
    if not await set_deletion_request(me, False, privacy.PRIVACY_VERSION):
        raise HTTPException(status_code=502, detail="ยกเลิกไม่สำเร็จ กรุณาลองใหม่")
    access.invalidate(me)
    await log_audit(me, "farmer", "deletion_cancel", "user", me, me, None)
    return {"status": "cancelled"}


class PurgeBody(BaseModel):
    confirm_user_id: str = Field(..., max_length=100)


@app.post("/admin/users/{user_id}/purge", dependencies=[Depends(verify_admin)])
async def admin_purge_user(user_id: str, body: PurgeBody):
    """
    ลบข้อมูลผู้ใช้ถาวร — ทำได้เฉพาะผู้ใช้ที่ขอลบไว้แล้ว และต้องพิมพ์ userId ยืนยันซ้ำ (กันกดผิดคน)
    ⚠️ ย้อนกลับไม่ได้ — ผู้ดูแลกดเองเท่านั้น
    """
    if body.confirm_user_id != user_id:
        raise HTTPException(status_code=422, detail="userId ยืนยันไม่ตรงกัน")
    u = await get_user_privacy(user_id)
    if not u or not u.get("deletion_requested_at"):
        raise HTTPException(status_code=409, detail="ผู้ใช้นี้ไม่ได้ขอลบข้อมูล")
    paths = await photo_paths_for_user(user_id)
    counts = await purge_user(user_id)
    counts["photo_files"] = len(paths) if await photo_service.delete_objects(paths) else 0
    access.invalidate(user_id)
    await log_audit(None, "admin", "purge_user", "user", None, None, {"counts": counts})
    return {"status": "purged", "deleted_rows": counts}


# ── รูปถ่ายภาคสนาม (Phase 2c) ──
_photo_user = user_rate_limit("photo", 20)


class PhotoBody(BaseModel):
    client_id:    str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    image_base64: str = Field(..., min_length=100, max_length=11_500_000)   # ≈ 8 MB หลังถอด base64
    lat:          float | None = Field(None, ge=-90, le=90)
    lng:          float | None = Field(None, ge=-180, le=180)
    gps_source:   Literal["exif", "device", "map_point"] | None = None
    taken_at:     datetime | None = None
    tags:         list[str] = Field(default_factory=list, max_length=photo_service.MAX_TAGS)
    note:         str = Field("", max_length=300)
    tree_id:      int | None = Field(None, ge=1)       # รูปของต้นไม้ต้นไหน (6.3)


def _photo_out(row: dict, urls: dict[str, str], me: str, plot_owner: str | None) -> dict:
    return {k: row.get(k) for k in ("id", "plot_id", "tree_id", "lat", "lng", "gps_source", "taken_at", "tags", "note",
                                    "width", "height", "created_at")} | {
        "url": urls.get(row["storage_path"]),
        "tags_th": [photo_service.TAGS_TH.get(t, t) for t in row.get("tags") or []],
        "uploader_role_th": access.ROLE_TH.get(row.get("uploader_role"), ""),
        "can_delete": row.get("uploaded_by") == me or plot_owner == me,
    }


@app.get("/photo-tags")
async def photo_tags():
    return {"tags": [{"id": k, "name_th": v} for k, v in photo_service.TAGS_TH.items()]}


@app.post("/plots/{user_id}/{plot_id}/photos")
async def upload_photo(user_id: str, plot_id: int, body: PhotoBody, background: BackgroundTasks,
                       me: str = Depends(_photo_user)):
    """
    แนบรูปกับแปลง (เจ้าของ / เจ้าหน้าที่ในพื้นที่ / ผู้ดูแล) — ส่งซ้ำด้วย client_id เดิมได้ (ไม่สร้างซ้ำ)
    ใช้ตอนสัญญาณกลับมาแล้วแอปส่งคิวที่ค้างอยู่
    """
    _require_self(user_id, me)
    await _require_consent(me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "photo_upload")
    existing = await get_photo_by_client_id(body.client_id)
    if existing:
        if existing["plot_id"] != plot_id or existing["uploaded_by"] != me:
            raise HTTPException(status_code=409, detail="รหัสรูปซ้ำ")
        urls = await photo_service.signed_urls([existing["storage_path"]])
        return _photo_out(existing, urls, me, plot.get("user_id")) | {"duplicate": True}
    try:
        tags = photo_service.validate_tags(body.tags)
        raw = base64.b64decode(body.image_base64.split(",", 1)[-1], validate=True)
        data, w, h = await run_in_threadpool(photo_service.clean_image, raw)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(status_code=422, detail=str(e) if isinstance(e, ValueError) else "ข้อมูลรูปไม่ถูกต้อง")
    if body.tree_id is not None:
        tree = await get_tree(body.tree_id)
        if not tree or tree["plot_id"] != plot_id:
            raise HTTPException(status_code=422, detail="ไม่พบต้นไม้นี้ในแปลง")
    path = f"{plot_id}/{body.client_id}.jpg"
    if not await photo_service.upload(path, data):
        raise HTTPException(status_code=502, detail="อัปโหลดรูปไม่สำเร็จ จะลองส่งใหม่อัตโนมัติ")
    acc = await _access_for(me)
    row = await insert_photo({
        "client_id": body.client_id, "plot_id": plot_id, "uploaded_by": me, "uploader_role": acc["role"],
        "lat": body.lat, "lng": body.lng, "gps_source": body.gps_source if body.lat is not None else None,
        "taken_at": body.taken_at.isoformat() if body.taken_at else None, "tags": tags,
        "note": body.note.strip(), "storage_path": path, "width": w, "height": h, "bytes": len(data),
        **({"tree_id": body.tree_id} if body.tree_id is not None else {})})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกรูปไม่สำเร็จ จะลองส่งใหม่อัตโนมัติ")
    background.add_task(log_event, me, "photo_upload", plot_id, {"tags": len(tags), "gps": body.gps_source or "none"})
    urls = await photo_service.signed_urls([path])
    return _photo_out(row, urls, me, plot.get("user_id"))


@app.get("/plots/{user_id}/{plot_id}/photos")
async def plot_photos(user_id: str, plot_id: int, me: str = Depends(_api_user)):
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="view_photos")
    rows = await list_photos(plot_id)
    urls = await photo_service.signed_urls([r["storage_path"] for r in rows])
    return {"plot_id": plot_id, "count": len(rows),
            "photos": [_photo_out(r, urls, me, plot.get("user_id")) for r in rows]}


@app.delete("/plots/{user_id}/{plot_id}/photos/{photo_id}")
async def remove_photo(user_id: str, plot_id: int, photo_id: int, me: str = Depends(_api_user)):
    """ลบรูป — คนที่อัปโหลด หรือเจ้าของแปลง (ผู้ใช้ลบข้อมูลของตัวเอง)"""
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me)
    row = await get_photo(photo_id)
    if not row or row["plot_id"] != plot_id:
        raise HTTPException(status_code=404, detail="ไม่พบรูปนี้")
    if me not in (row["uploaded_by"], plot.get("user_id")):
        raise HTTPException(status_code=403, detail="ลบได้เฉพาะคนที่แนบรูป หรือเจ้าของแปลง")
    await photo_service.delete_objects([row["storage_path"]])
    if not await delete_photo_row(photo_id):
        raise HTTPException(status_code=502, detail="ลบไม่สำเร็จ กรุณาลองใหม่")
    await log_audit(me, (await _access_for(me))["role"], "delete_photo", "photo", photo_id, plot.get("user_id"), None)
    return {"status": "deleted", "photo_id": photo_id}


# ── ต้นไม้รายต้น (spec 6.3 ตัวเลือก — migrate_v22) ──
class TreeBody(BaseModel):
    client_id:  str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    lat:        float = Field(..., ge=-90, le=90)
    lng:        float = Field(..., ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0)
    label:      str = Field("", max_length=40)
    crop_id:    str | None = Field(None, max_length=40)


class TreePatchBody(BaseModel):
    label:   str | None = Field(None, min_length=1, max_length=40)
    crop_id: str | None = Field(None, max_length=40)


class TreeLogBody(BaseModel):
    client_id:   str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind:        Literal["symptom", "fertilize", "spray", "note"]
    happened_on: date
    tags:        list[str] = Field(default_factory=list, max_length=photo_service.MAX_TAGS)
    product:     str = Field("", max_length=80)
    note:        str = Field("", max_length=300)


def _valid_crop(crop_id: str | None) -> str | None:
    return _validated_crop_ids([crop_id])[0] if crop_id else None


async def _tree_in_plot(tree_id: int, plot_id: int) -> dict:
    tree = await get_tree(tree_id)
    if not tree or tree["plot_id"] != plot_id:
        raise HTTPException(status_code=404, detail="ไม่พบต้นไม้นี้")
    return tree


@app.get("/plots/{user_id}/{plot_id}/trees")
async def plot_trees(user_id: str, plot_id: int, me: str = Depends(_api_user)):
    """หมุดต้นไม้ของแปลง + บันทึกล่าสุด/จำนวนบันทึก/จำนวนรูป ต่อต้น"""
    _require_self(user_id, me)
    await _require_plot_access(plot_id, me, audit_action="view_trees")
    pins = await list_trees(plot_id)
    logs, photos = await asyncio.gather(list_tree_logs([p["id"] for p in pins]), list_photos(plot_id))
    return {"plot_id": plot_id, "max_trees": trees.MAX_PINS_PER_PLOT,
            "next_label": trees.next_label([p["label"] for p in pins]),
            "trees": trees.tree_list(pins, logs, [p.get("tree_id") for p in photos])}


@app.post("/plots/{user_id}/{plot_id}/trees")
async def add_tree(user_id: str, plot_id: int, body: TreeBody, background: BackgroundTasks, me: str = Depends(_api_user)):
    """ปักหมุดต้นไม้ (ยืนที่ต้นแล้วใช้ GPS) — ส่งซ้ำด้วย client_id เดิมได้"""
    _require_self(user_id, me)
    await _require_consent(me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "tree_pin")
    existing = await get_tree_by_client_id(body.client_id)
    if existing:
        if existing["plot_id"] != plot_id or existing["created_by"] != me:
            raise HTTPException(status_code=409, detail="รหัสต้นไม้ซ้ำ")
        return {k: v for k, v in existing.items() if k != "created_by"} | {"duplicate": True}
    if not trees.within_plot(plot, body.lat, body.lng):
        raise HTTPException(status_code=422, detail="ตำแหน่งอยู่นอกแปลงนี้ — ไปยืนที่ต้นไม้ในแปลงแล้วลองใหม่")
    pins = await list_trees(plot_id)
    if len(pins) >= trees.MAX_PINS_PER_PLOT:
        raise HTTPException(status_code=422, detail=f"ปักได้ไม่เกิน {trees.MAX_PINS_PER_PLOT} ต้นต่อแปลง")
    acc = await _access_for(me)
    row = await insert_tree({"client_id": body.client_id, "plot_id": plot_id,
                             "label": body.label.strip() or trees.next_label([p["label"] for p in pins]),
                             "lat": body.lat, "lng": body.lng, "accuracy_m": body.accuracy_m,
                             "crop_id": _valid_crop(body.crop_id), "created_by": me, "created_role": acc["role"]})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    background.add_task(log_event, me, "tree_pin", plot_id, {"role": acc["role"]})
    return {k: v for k, v in row.items() if k != "created_by"}


@app.patch("/plots/{user_id}/{plot_id}/trees/{tree_id}")
async def edit_tree(user_id: str, plot_id: int, tree_id: int, body: TreePatchBody, me: str = Depends(_api_user)):
    _require_self(user_id, me)
    await _require_consent(me)
    await _require_plot_access(plot_id, me, access.EDIT, "tree_edit")
    await _tree_in_plot(tree_id, plot_id)
    fields = {k: v for k, v in (("label", body.label.strip() if body.label else None),
                                ("crop_id", _valid_crop(body.crop_id))) if v is not None}
    if fields and not await update_tree(tree_id, fields):
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    return {"id": tree_id, **fields}


@app.delete("/plots/{user_id}/{plot_id}/trees/{tree_id}")
async def remove_tree(user_id: str, plot_id: int, tree_id: int, me: str = Depends(_api_user)):
    """เอาหมุดออก (ซ่อน — ประวัติบันทึกยังอยู่ในข้อมูล) · เจ้าของแปลง หรือคนที่ปัก"""
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "tree_remove")
    tree = await _tree_in_plot(tree_id, plot_id)
    if me not in (tree["created_by"], plot.get("user_id")):
        raise HTTPException(status_code=403, detail="เอาออกได้เฉพาะเจ้าของแปลง หรือคนที่ปักหมุด")
    if not await update_tree(tree_id, {"removed_at": datetime.now(timezone.utc).isoformat()}):
        raise HTTPException(status_code=502, detail="ไม่สำเร็จ กรุณาลองใหม่")
    return {"status": "removed", "id": tree_id}


@app.get("/plots/{user_id}/{plot_id}/trees/{tree_id}")
async def tree_detail(user_id: str, plot_id: int, tree_id: int, me: str = Depends(_api_user)):
    """ประวัติของต้น: บันทึก + รูป (ล่าสุดก่อน)"""
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="view_tree")
    tree = await _tree_in_plot(tree_id, plot_id)
    logs, photos = await asyncio.gather(list_tree_logs([tree_id]), list_photos(plot_id))
    photos = [p for p in photos if p.get("tree_id") == tree_id]
    urls = await photo_service.signed_urls([p["storage_path"] for p in photos])
    owner = plot.get("user_id")
    public_tree = {k: v for k, v in tree.items() if k != "created_by"} | {"by_me": tree["created_by"] == me}
    return {"tree": public_tree, "logs": [trees.log_out(r, me, owner) for r in logs],
            "photos": [_photo_out(p, urls, me, owner) for p in photos],
            "kinds": [{"id": k, "name_th": v} for k, v in trees.LOG_KINDS_TH.items()]}


@app.post("/plots/{user_id}/{plot_id}/trees/{tree_id}/logs")
async def add_tree_log(user_id: str, plot_id: int, tree_id: int, body: TreeLogBody, background: BackgroundTasks,
                       me: str = Depends(_api_user)):
    """บันทึกอาการ / ใส่ปุ๋ย / พ่นยา / บันทึกอื่น ของต้นนี้ — ส่งซ้ำด้วย client_id เดิมได้"""
    _require_self(user_id, me)
    await _require_consent(me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "tree_log")
    await _tree_in_plot(tree_id, plot_id)
    existing = await get_tree_log_by_client_id(body.client_id)
    if existing:
        if existing["tree_id"] != tree_id or existing["by_user"] != me:
            raise HTTPException(status_code=409, detail="รหัสบันทึกซ้ำ")
        return trees.log_out(existing, me, plot.get("user_id")) | {"duplicate": True}
    try:
        tags = trees.validate_log(body.kind, body.tags, body.happened_on, datetime.now(timezone.utc).date())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    acc = await _access_for(me)
    row = await insert_tree_log({"client_id": body.client_id, "tree_id": tree_id, "kind": body.kind,
                                 "happened_on": body.happened_on.isoformat(), "tags": tags,
                                 "product": body.product.strip(), "note": body.note.strip(),
                                 "by_user": me, "role": acc["role"]})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    background.add_task(log_event, me, "tree_log", plot_id, {"kind": body.kind})
    return trees.log_out(row, me, plot.get("user_id"))


@app.delete("/plots/{user_id}/{plot_id}/trees/{tree_id}/logs/{log_id}")
async def remove_tree_log(user_id: str, plot_id: int, tree_id: int, log_id: int, me: str = Depends(_api_user)):
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "tree_log_delete")
    await _tree_in_plot(tree_id, plot_id)
    row = await get_tree_log(log_id)
    if not row or row["tree_id"] != tree_id:
        raise HTTPException(status_code=404, detail="ไม่พบบันทึกนี้")
    if me not in (row["by_user"], plot.get("user_id")):
        raise HTTPException(status_code=403, detail="ลบได้เฉพาะคนที่บันทึก หรือเจ้าของแปลง")
    if not await delete_tree_log(log_id):
        raise HTTPException(status_code=502, detail="ลบไม่สำเร็จ กรุณาลองใหม่")
    return {"status": "deleted", "id": log_id}


# ── ยืนยันภาคสนาม (Phase 2d) ──
class ValidationBody(BaseModel):
    scope:           Literal["point", "plot"]
    problem:         str = Field(..., max_length=40)
    severity:        Literal["low", "medium", "high"] | None = None
    answer:          Literal["true", "false", "unsure"]
    lat:             float | None = Field(None, ge=-90, le=90)
    lng:             float | None = Field(None, ge=-180, le=180)
    note:            str = Field("", max_length=300)
    photo_client_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    context:         dict | None = None


def _clean_context(ctx: dict | None) -> dict | None:
    """สิ่งที่ระบบแสดงตอนนั้น — เก็บเฉพาะค่าสั้น ๆ (กัน payload ใหญ่/ข้อมูลไม่จำเป็น)"""
    if not ctx:
        return None
    return {str(k)[:30]: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:60])
            for k, v in list(ctx.items())[:12]}


@app.post("/plots/{user_id}/{plot_id}/validations")
async def add_validation(user_id: str, plot_id: int, body: ValidationBody, background: BackgroundTasks,
                         me: str = Depends(_api_user)):
    """ตอบว่าสิ่งที่ระบบบอกตรงกับในแปลงจริงไหม (เจ้าของ / เจ้าหน้าที่ในพื้นที่ / ผู้ดูแล)"""
    _require_self(user_id, me)
    if body.problem not in recommendations.PROBLEMS_TH:
        raise HTTPException(status_code=422, detail="ประเภทปัญหาไม่ถูกต้อง")
    if body.scope == "point" and (body.lat is None or body.lng is None):
        raise HTTPException(status_code=422, detail="การยืนยันรายจุดต้องมีพิกัดจุด")
    await _require_consent(me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "field_validation")
    acc = await _access_for(me)
    row = await insert_validation({
        "plot_id": plot_id, "user_id": me, "role": acc["role"], "scope": body.scope, "problem": body.problem,
        "severity": body.severity, "answer": body.answer, "lat": body.lat, "lng": body.lng,
        "note": body.note.strip(), "photo_client_id": body.photo_client_id, "context": _clean_context(body.context)})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    background.add_task(log_event, me, "field_validation", plot_id,
                        {"problem": body.problem, "answer": body.answer, "role": acc["role"]})
    return {"id": row["id"], "answer_th": validation.ANSWER_TH[body.answer],
            "message_th": "ขอบคุณครับ — คำตอบนี้ช่วยให้ระบบแม่นขึ้น", "plot_owner_is_me": plot.get("user_id") == me}


@app.get("/plots/{user_id}/{plot_id}/validations")
async def plot_validations(user_id: str, plot_id: int, me: str = Depends(_api_user)):
    _require_self(user_id, me)
    await _require_plot_access(plot_id, me, audit_action="view_validations")
    rows = await list_validations(plot_id)
    return {"plot_id": plot_id, "summary": validation.summarize(rows),
            "items": [{k: r.get(k) for k in ("scope", "problem", "severity", "answer", "note", "created_at")}
                      | {"role_th": access.ROLE_TH.get(r.get("role"), ""), "by_me": r.get("user_id") == me}
                      for r in rows[:50]]}


@app.get("/admin/validations", dependencies=[Depends(verify_admin)])
async def admin_validations(days: int = 365):
    since = (datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), 3650))).isoformat()
    return validation.summarize(await list_validations(since_iso=since))


@app.get("/admin/validations/export.csv", dependencies=[Depends(verify_admin)])
async def admin_validations_export(days: int = 3650):
    """ข้อมูลยืนยันภาคสนามทั้งหมด (มีพิกัด — ผู้ดูแลเท่านั้น) สำหรับวัดผลและเป็น label Phase 4 · ไม่มี LINE userId"""
    since = (datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), 3650))).isoformat()
    rows = await list_validations(since_iso=since)
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["id", "plot_id", "role", "scope", "problem", "severity", "answer", "lat", "lng", "note",
                "has_photo", "context", "created_at"])
    for r in rows:
        w.writerow([r["id"], r["plot_id"], r["role"], r["scope"], r["problem"], r.get("severity") or "",
                    r["answer"], r.get("lat") or "", r.get("lng") or "", r.get("note") or "",
                    "yes" if r.get("photo_client_id") else "", json.dumps(r.get("context") or {}, ensure_ascii=False),
                    r["created_at"]])
    await log_audit(None, "admin", "export_validations", "validations", None, None, {"rows": len(rows)})
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="field-validations.csv"'})


# ── ติดตามระยะยาว: เทียบหน้าฝน/หน้าแล้ง (Phase 3c) ──
SEASONS_TIMEOUT_S = 60
SEASONS_CACHE_TTL_S = 12 * 3600
_seasons_cache: dict[tuple, tuple[float, dict]] = {}


@app.get("/plots/{user_id}/{plot_id}/seasons")
async def plot_seasons(user_id: str, plot_id: int, background: BackgroundTasks, months: int = 24,
                       me: str = Depends(_grid_user)):
    """ความชื้นรายเดือนจากเรดาร์ + เทียบหน้าฝน/หน้าแล้งกับปีก่อนของแปลงเดียวกัน (GEE ~10–30 วินาที, แคช 12 ชม.)"""
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="view_seasons")
    months = min(max(months, 12), 36)
    key = (plot_id, months, json.dumps(plot.get("polygon")))
    hit = _seasons_cache.get(key)
    if hit and time.monotonic() - hit[0] < SEASONS_CACHE_TTL_S:
        return hit[1]
    today = datetime.now(timezone.utc)
    start = today - timedelta(days=int(months * 30.5))
    try:
        series = await asyncio.wait_for(
            run_in_threadpool(risk_v2.s1_series, plot["lat"], plot["lng"], plot.get("polygon"), start, today),
            timeout=SEASONS_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ดึงข้อมูลย้อนหลังใช้เวลานานเกินไป กรุณาลองใหม่")
    except Exception as e:
        logger.error(f"seasons failed plot={plot_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="ดึงข้อมูลดาวเทียมย้อนหลังไม่สำเร็จ กรุณาลองใหม่ภายหลัง")
    out = seasons.summarize(series, today) | {"plot_id": plot_id, "months_requested": months}
    _seasons_cache[key] = (time.monotonic(), out)
    background.add_task(log_event, me, "view_seasons", plot_id, None)
    return out


# ── จำแนกชนิดพืช: ทาย + ยืนยัน (Phase 4a — crop_classifier) ──
class CropFeedbackBody(BaseModel):
    answer:        Literal["yes", "no", "other", "unsure"]
    crop_ids:      list[str] = Field(default_factory=list, max_length=crops_mod.MAX_CROPS_PER_PLOT)
    prediction_id: int | None = Field(None, ge=1)


@app.get("/plots/{user_id}/{plot_id}/crop-guess")
async def plot_crop_guess(user_id: str, plot_id: int, me: str = Depends(_grid_user)):
    """
    ระบบทายว่าแปลงนี้น่าจะปลูกอะไร (top-3 + ความมั่นใจ) — ยังไม่เปิดใช้ = status "collecting" (แอปถามผู้ใช้แทน)
    ห้ามตัดสินเอง: ผลทายไม่เปลี่ยนชนิดพืชของแปลง จนกว่าผู้ใช้กดยืนยัน
    """
    _require_self(user_id, me)
    plot = await _require_plot_access(plot_id, me, audit_action="view_crop_guess")
    out = await crop_service.guess(plot)
    crop_ids = await get_plot_crop_ids(plot_id)
    is_owner = plot.get("user_id") == me
    helps = bool((await get_user_privacy(me) or {}).get("consent_photos_research")) if is_owner else None
    return out | {"plot_id": plot_id, "current_crop_ids": crop_ids, "current_names_th": crops_mod.names_th(crop_ids),
                  "is_owner": is_owner, "owner_helps_training": helps}


@app.post("/plots/{user_id}/{plot_id}/crop-feedback")
async def plot_crop_feedback(user_id: str, plot_id: int, body: CropFeedbackBody, background: BackgroundTasks,
                             me: str = Depends(_api_user)):
    """ใช่ / ไม่ใช่ / เลือกพืชอื่น / ไม่แน่ใจ — เป็น label ของรอบเทรนถัดไป · ใช่/เลือกพืชอื่น = บันทึกชนิดพืชของแปลงด้วย"""
    _require_self(user_id, me)
    await _require_consent(me)
    plot = await _require_plot_access(plot_id, me, access.EDIT, "crop_feedback")
    ids = _validated_crop_ids(body.crop_ids)
    if body.answer in ("yes", "other") and not ids:
        raise HTTPException(status_code=422, detail="เลือกพืชอย่างน้อย 1 ชนิด")
    acc = await _access_for(me)
    role = acc["role"] if acc["role"] in ("officer", "admin") else "farmer"
    if body.answer in ("yes", "other") and sorted(ids) != sorted(await get_plot_crop_ids(plot_id)):
        if not await set_plot_crops(plot_id, ids, source="user" if role == "farmer" else "officer"):
            raise HTTPException(status_code=502, detail="บันทึกชนิดพืชไม่สำเร็จ กรุณาลองใหม่")
    row = await crop_store.insert_feedback({"plot_id": plot_id, "prediction_id": body.prediction_id,
                                            "answer": body.answer, "crop_ids": ids, "by_user": me, "role": role})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    background.add_task(log_event, me, "crop_feedback", plot_id, {"answer": body.answer, "role": role})
    return {"id": row["id"], "crop_ids": ids, "names_th": crops_mod.names_th(ids),
            "message_th": "ขอบคุณครับ — คำตอบนี้ช่วยสอนระบบให้ทายชนิดพืชได้แม่นขึ้น"}


class CropSurveyBody(BaseModel):
    client_id:        str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    lat:              float = Field(..., ge=5.5, le=20.6)        # ประเทศไทย
    lng:              float = Field(..., ge=97.3, le=105.7)
    accuracy_m:       float | None = Field(None, ge=0)
    crop_ids:         list[str] = Field(..., min_length=1, max_length=crops_mod.MAX_CROPS_PER_PLOT)
    owner_permission: bool


SURVEY_MAX_ACCURACY_M = 30
SURVEY_CONSENT_BASIS = "เจ้าหน้าที่สำรวจในแปลง — เจ้าของแปลงอนุญาต (เจ้าหน้าที่ยืนยันในแอป)"


async def _require_officer(me: str) -> dict:
    acc = await _access_for(me)
    if acc["role"] not in ("officer", "admin"):
        raise HTTPException(status_code=403, detail="ใช้ได้เฉพาะเจ้าหน้าที่")
    return acc


@app.post("/officer/crop-survey")
async def officer_crop_survey(body: CropSurveyBody, background: BackgroundTasks, me: str = Depends(_api_user)):
    """
    เจ้าหน้าที่ยืนในแปลง บันทึกจุด GPS + พืชที่เห็น → แปลงอ้างอิงสำหรับเทรน (ไม่แสดงในแอป ไม่ผูกกับบัญชีเกษตรกร)
    ส่งซ้ำด้วย client_id เดิมได้ (คิวออฟไลน์) · ต้องยืนยันว่าเจ้าของแปลงอนุญาต
    """
    await _require_consent(me)
    await _require_officer(me)
    if not body.owner_permission:
        raise HTTPException(status_code=422, detail="ต้องได้รับอนุญาตจากเจ้าของแปลงก่อนบันทึก")
    if body.accuracy_m is not None and body.accuracy_m > SURVEY_MAX_ACCURACY_M:
        raise HTTPException(status_code=422, detail=f"ตำแหน่ง GPS ยังไม่แม่น (±{round(body.accuracy_m)} ม.) "
                                                    f"รอสักครู่แล้วลองใหม่ ต้องไม่เกิน ±{SURVEY_MAX_ACCURACY_M} ม.")
    ids = _validated_crop_ids(body.crop_ids)
    try:
        area = await asyncio.wait_for(run_in_threadpool(areas.lookup_point, body.lat, body.lng), timeout=10)
    except asyncio.TimeoutError:
        area = {"area_code": None, "area_name": None}
    row = await crop_store.upsert_survey({
        "source": crop_labels.SURVEY_SOURCE, "source_ref": body.client_id, "crop_ids": ids, "lat": body.lat,
        "lng": body.lng, "polygon": None, "area_code": area.get("area_code"),
        "consent_basis": SURVEY_CONSENT_BASIS, "imported_by": me})
    if not row:
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ กรุณาลองใหม่")
    background.add_task(log_event, me, "crop_survey", None, {"crops": len(ids), "district": area.get("area_code")})
    return {"id": row["id"], "client_id": body.client_id, "names_th": crops_mod.names_th(ids),
            "area_name": area.get("area_name") or areas.name_th(area.get("area_code")),
            "message_th": "บันทึกแล้ว — ขอบคุณครับ จุดนี้ใช้สอนระบบทายชนิดพืช"}


@app.get("/officer/crop-survey")
async def officer_crop_survey_list(me: str = Depends(_api_user)):
    """จุดสำรวจของตัวเอง (ล่าสุด 50 จุด + จำนวนทั้งหมด)"""
    await _require_officer(me)
    rows, total = await asyncio.gather(crop_store.list_surveys(me), crop_store.count_surveys(me))
    return {"total": total, "items": [{"id": r["id"], "names_th": crops_mod.names_th(r["crop_ids"]),
                                       "area_name": areas.name_th(r.get("area_code")),
                                       "lat": r["lat"], "lng": r["lng"], "at": r["imported_at"]} for r in rows]}


@app.delete("/officer/crop-survey/{survey_id}")
async def officer_crop_survey_delete(survey_id: int, me: str = Depends(_api_user)):
    await _require_officer(me)
    if not await crop_store.delete_survey(survey_id, me):
        raise HTTPException(status_code=404, detail="ไม่พบจุดสำรวจนี้")
    return {"deleted": survey_id}


@app.get("/admin/crop-model", dependencies=[Depends(verify_admin)])
async def admin_crop_model():
    """สถานะระบบจำแนกพืช: เปิดทายหรือยัง, โมเดลแต่ละเวอร์ชัน, จำนวน label (ไม่มีพิกัด/ผู้ใช้)"""
    _, ds = await crop_service.build_dataset()
    models, active, counts = await asyncio.gather(crop_store.list_models(), crop_store.get_active_model(),
                                                  crop_store.label_counts())
    return {"guess_enabled_env": crop_service.guess_enabled(), "active_version": (active or {}).get("version"),
            "models": models, "dataset": ds, "labels": counts,
            "gate": {"min_plots": crop_evaluate.GATE_MIN_PLOTS, "min_macro_f1": crop_evaluate.GATE_MIN_MACRO_F1,
                     "min_groups": crop_evaluate.GATE_MIN_GROUPS, "min_plots_per_class": crop_labels.MIN_PLOTS_PER_CLASS}}


@app.post("/admin/crop-model/features", dependencies=[Depends(verify_admin)])
async def admin_crop_features(limit: int = 5):
    """สกัด feature ชุดฝึกทีละไม่กี่แปลง (GEE ~10 วินาที/แปลง) — เรียกซ้ำจน remaining = 0"""
    return await crop_service.batch_features(min(max(limit, 1), 20))


@app.post("/admin/crop-model/train", dependencies=[Depends(verify_admin)])
async def admin_crop_train():
    """ประเมินแบบแบ่งเชิงพื้นที่ + เทรน + เก็บเป็นเวอร์ชันใหม่ (ไม่เปิดใช้เอง)"""
    out = await crop_service.train("admin")
    await log_audit(None, "admin", "crop_model_train", "crop_model", None, None,
                    {"status": out["status"], "version": out.get("version"), "n": out["dataset"]["n"]})
    return out


@app.post("/admin/crop-model/activate", dependencies=[Depends(verify_admin)])
async def admin_crop_activate(version: str | None = None):
    """เปิดใช้โมเดลที่ผ่านเกณฑ์ (ไม่ส่ง version = ปิดการทาย) · ผู้ใช้เห็นผลทายเมื่อ env CROP_GUESS_ENABLED=1 ด้วย"""
    ok, msg = await crop_service.activate(version or None)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    await log_audit(None, "admin", "crop_model_activate", "crop_model", None, None, {"version": version})
    return {"active_version": version, "message_th": msg}


@app.get("/admin/crop-model/report.md", dependencies=[Depends(verify_admin)])
async def admin_crop_report(version: str):
    m = await crop_store.get_model(version)
    if not m:
        raise HTTPException(status_code=404, detail="ไม่พบโมเดลนี้")
    names = {c["id"]: c["name_th"] for c in crops_mod.all_crops()} | {"mixed": "สวนผสม", "other": "อื่น ๆ"} \
        | {d["code"]: d["name_th"] for d in areas.districts()}
    return Response(content=crop_evaluate.report_markdown(version, m["metrics"], m["dataset"], names),
                    media_type="text/markdown; charset=utf-8")


class ReferenceImportBody(BaseModel):
    source:        str = Field(..., min_length=3, max_length=60, pattern=r"^[A-Za-z0-9_\-]+$")
    consent_basis: str = Field(..., min_length=5, max_length=200)
    csv:           str = Field(..., max_length=2_000_000)


@app.post("/admin/crop-model/references", dependencies=[Depends(verify_admin)])
async def admin_crop_references(body: ReferenceImportBody):
    """นำเข้าแปลงอ้างอิง (เช่น ทะเบียนเกษตรกร) จาก CSV — ใช้เทรนเท่านั้น · ต้องระบุที่มาทางกฎหมายของข้อมูล"""
    rows, errors = crop_references.parse_csv(body.csv, body.source, body.consent_basis.strip(), "admin")
    n = await crop_store.upsert_reference_labels(rows)
    await log_audit(None, "admin", "import_reference_labels", "reference_labels", None, None,
                    {"source": body.source, "rows": n, "errors": len(errors)})
    return {"imported": n, "errors": errors[:50], "error_count": len(errors)}


# ── แดชบอร์ดจังหวัด + สถิติการใช้งาน (Phase 2e) ──
async def _status_labels(plot_ids: list[int]) -> dict[int, str | None]:
    """ป้ายเทียบปกติของแปลงเอง (ชื้น/แห้งกว่าปกติ) ล่าสุดต่อแปลง — ยิงพร้อมกันไม่เกิน 8"""
    sem = asyncio.Semaphore(8)

    async def one(pid: int):
        async with sem:
            s = await get_latest_plot_signals(pid)
        return pid, risk_v2.anomaly_label_th((s or {}).get("risk_v2"))
    return dict(await asyncio.gather(*[one(p) for p in plot_ids]))


async def _province_data() -> dict:
    plots = await get_active_plots()
    labels = await _status_labels([p["id"] for p in plots])
    return province.district_summary(plots, labels)


@app.get("/admin/province", dependencies=[Depends(verify_admin)])
async def admin_province():
    """ภาพรวมรายอำเภอ (ตัวเลขรวม ไม่มีชื่อ/พิกัดแปลง)"""
    data = await _province_data()
    await log_audit(None, "admin", "view_province", "province", "70", None, None)
    return data | {"suppress_below": province.SUPPRESS_BELOW, "source": areas.config().get("source")}


@app.get("/admin/province/export.csv", dependencies=[Depends(verify_admin)])
async def admin_province_export():
    """ไฟล์ส่งหน่วยงาน (เปิดใน Excel ได้) — อำเภอที่มีเกษตรกรน้อยกว่า 3 คนซ่อนตัวเลขเป็น "<3" """
    data = await _province_data()
    rows = province.suppressed(data["districts"])
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow([f"IAM ROOT — สรุปแปลงรายอำเภอ จ.ราชบุรี (ข้อมูล ณ {data['generated_at'][:10]})"])
    w.writerow([f"อำเภอที่มีเกษตรกรน้อยกว่า {province.SUPPRESS_BELOW} คน แสดงเป็น \"<{province.SUPPRESS_BELOW}\" เพื่อคุ้มครองข้อมูลส่วนบุคคล"
                " · ขอบเขตอำเภอ: geoBoundaries"])
    w.writerow(["รหัสอำเภอ", "อำเภอ", "เกษตรกร (คน)", "แปลง", "พื้นที่รวม (ไร่)", "แปลงที่วาดขอบเขต",
                "พืช (จำนวนแปลง)", "ประเภทสวน", "ความชื้นเทียบปกติของแปลง"])
    for r in rows:
        fmt = lambda d: " · ".join(f"{k} {v}" for k, v in d.items())   # noqa: E731
        w.writerow([r["code"] or "", r["name_th"], r["farmers"], r["plots"], r["area_rai"], r["with_boundary"],
                    fmt(r["crops"]), fmt(r["plot_types"]), fmt(r["status"])])
    await log_audit(None, "admin", "export_province", "province", "70", None, {"rows": len(rows)})
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="iamroot-ratchaburi-districts.csv"'})


@app.get("/admin/usage", dependencies=[Depends(verify_admin)])
async def admin_usage(weeks: int = 12):
    """สถิติการใช้งานรายสัปดาห์ (สำหรับรายงาน B2G) — นับจาก events เริ่ม 25 ต.ค. 2569"""
    weeks = min(max(weeks, 1), 52)
    since = (datetime.now(timezone.utc) - timedelta(weeks=weeks, days=7)).isoformat()
    events, users = await asyncio.gather(get_events_since(since), get_users_created())
    plots = await get_active_plots()
    return {"weeks": province.weekly_usage(events, users, weeks),
            "totals": {"users": len(users), "active_plots": len(plots),
                       "events_counted_since": "2026-10-25"}}


EVENT_NAMES = {"app_open", "view_map", "view_seasons", "view_recommendation", "open_pdf", "share_line", "photo_upload",
               "field_validation", "open_officer_tab"}


class EventBody(BaseModel):
    name:    str = Field(..., max_length=40)
    plot_id: int | None = Field(None, ge=1)
    props:   dict | None = None


@app.post("/events", status_code=204)
async def post_event(body: EventBody, background: BackgroundTasks, me: str = Depends(_api_user)):
    """สถิติการใช้งานจากแอป (B2G) — รับเฉพาะชื่อในรายการ · props เก็บได้ไม่เกิน 10 ค่าแบบสั้น"""
    if body.name not in EVENT_NAMES:
        raise HTTPException(status_code=422, detail="ไม่รู้จักเหตุการณ์นี้")
    props = {str(k)[:30]: (v if isinstance(v, (int, float, bool)) else str(v)[:60])
             for k, v in list((body.props or {}).items())[:10]}
    background.add_task(log_event, me, body.name, body.plot_id, props or None)
    return Response(status_code=204)


class RedeemBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=20)


@app.post("/invites/redeem")
async def redeem_invite(body: RedeemBody, request: Request, me: str = Depends(_api_user)):
    """ใช้รหัสเชิญ → ได้บทบาท/พื้นที่ตามรหัส (เพิ่มสิทธิ์ได้อย่างเดียว ไม่ลดสิทธิ์)"""
    # กันไล่เดารหัส: 10 ครั้ง/นาที/IP นอกเหนือจากเพดานต่อผู้ใช้
    if ip_bucket_exceeded(request, "invite", 10):
        raise HTTPException(status_code=429, detail="ลองบ่อยเกินไป กรุณารอสักครู่")
    code = body.code.strip().upper()
    inv = await get_invite(code)
    now = datetime.now(timezone.utc)
    if (not inv or datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00")) <= now
            or inv["used_count"] >= inv["max_uses"]):
        raise HTTPException(status_code=404, detail="รหัสเชิญไม่ถูกต้องหรือหมดอายุแล้ว")
    acc = await _access_for(me)
    new_areas = sorted(set(acc["areas"]) | set(inv.get("area_codes") or []))
    role = inv["role"] if access.can_upgrade(acc["role"], inv["role"]) else acc["role"]
    await upsert_user(me, "")
    if not await record_invite_use(code, me, inv["used_count"]):
        raise HTTPException(status_code=409, detail="ใช้รหัสนี้ไปแล้ว")
    if not await set_user_role(me, role, new_areas):
        raise HTTPException(status_code=502, detail="บันทึกสิทธิ์ไม่สำเร็จ กรุณาลองใหม่")
    access.invalidate(me)
    await log_audit(me, role, "redeem_invite", "invite", code, me, {"role": role, "areas": new_areas})
    return {"role": role, "role_th": access.ROLE_TH[role], "areas": _area_list(new_areas)}


@app.get("/officer/plots")
async def officer_plots(me: str = Depends(_api_user)):
    """
    แปลงที่เจ้าหน้าที่เห็นได้: ที่ตัวเองวาด + ในอำเภอที่รับผิดชอบ เฉพาะเจ้าของที่ยินยอมแบ่งปัน
    (ผู้ดูแลเห็นทุกแปลงที่ใช้งานอยู่) · ไม่ส่ง LINE userId ของเจ้าของออกไป
    """
    acc = await _access_for(me)
    if acc["role"] == "farmer":
        raise HTTPException(status_code=403, detail="สำหรับเจ้าหน้าที่เท่านั้น")
    if acc["role"] == "admin":
        rows = await get_active_plots()
        visible = rows
    else:
        rows = await get_plots_for_officer(me, acc["areas"])
        shares = await get_share_flags([p["user_id"] for p in rows if p.get("created_by") != me])
        visible = [p for p in rows
                   if access.plot_access_reason(acc, me, p, shares.get(p["user_id"], False)) is not None]
    risk = await get_latest_risk_by_plots([p["id"] for p in visible])
    out = []
    for p in visible:
        info = risk.get(p["id"]) or {}
        out.append({k: p.get(k) for k in ("id", "name", "lat", "lng", "polygon", "plot_type", "crop_ids",
                                          "area_code", "area_name", "owner_label", "thumbnail_url",
                                          "boundary_check", "created_at")}
                   | {"is_mine": p.get("user_id") == me, "created_by_me": p.get("created_by") == me,
                      "latest_analyzed_at": info.get("analyzed_at")})
    await log_audit(me, acc["role"], "list_area_plots", "area", ",".join(acc["areas"]) or "-", None,
                    {"count": len(out)})
    return {"role": acc["role"], "areas": _area_list(acc["areas"]), "count": len(out), "plots": out}


# ── ผู้ดูแล (X-Admin-Key) ──
class UserRoleBody(BaseModel):
    role:  Literal["farmer", "officer", "admin"]
    areas: list[str] = Field(default_factory=list, max_length=50)


class InviteBody(BaseModel):
    role:         Literal["officer", "admin"] = "officer"
    area_codes:   list[str] = Field(default_factory=list, max_length=50)
    expires_days: int = Field(14, ge=1, le=90)
    max_uses:     int = Field(1, ge=1, le=100)
    note:         str = Field("", max_length=200)


_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ไม่มี 0/O/1/I กันอ่านผิด


def _new_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


@app.get("/admin/areas", dependencies=[Depends(verify_admin)])
async def admin_areas():
    return {"districts": areas.districts(), "source": areas.config().get("source")}


@app.get("/admin/users", dependencies=[Depends(verify_admin)])
async def admin_users():
    users = await list_users_admin()
    await log_audit(None, "admin", "list_users", "users", None, None, {"count": len(users)})
    return {"users": users, "roles": access.ROLE_TH}


@app.patch("/admin/users/{user_id}", dependencies=[Depends(verify_admin)])
async def admin_set_user(user_id: str, body: UserRoleBody):
    try:
        codes = areas.validate_codes(body.areas)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if body.role != "officer":
        codes = codes if body.role == "admin" else []
    if not await set_user_role(user_id, body.role, codes):
        raise HTTPException(status_code=502, detail="บันทึกไม่สำเร็จ (ยังไม่ได้รัน migrate_v16?)")
    access.invalidate(user_id)
    await log_audit(None, "admin", "set_role", "user", user_id, user_id, {"role": body.role, "areas": codes})
    return {"user_id": user_id, "role": body.role, "areas": _area_list(codes)}


@app.post("/admin/invites", dependencies=[Depends(verify_admin)])
async def admin_create_invite(body: InviteBody):
    try:
        codes = areas.validate_codes(body.area_codes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if body.role == "officer" and not codes:
        raise HTTPException(status_code=422, detail="รหัสเชิญเจ้าหน้าที่ต้องระบุอำเภออย่างน้อย 1 อำเภอ")
    code = _new_invite_code()
    row = {"code": code, "role": body.role, "area_codes": codes, "note": body.note.strip(), "created_by": "admin",
           "expires_at": (datetime.now(timezone.utc) + timedelta(days=body.expires_days)).isoformat(),
           "max_uses": body.max_uses}
    if not await create_invite(row):
        raise HTTPException(status_code=502, detail="สร้างรหัสไม่สำเร็จ (ยังไม่ได้รัน migrate_v16?)")
    await log_audit(None, "admin", "create_invite", "invite", code, None, {"role": body.role, "areas": codes})
    return {**row, "link": f"https://liff.line.me/{liff_id()}?invite={code}", "areas": _area_list(codes)}


@app.get("/admin/invites", dependencies=[Depends(verify_admin)])
async def admin_list_invites():
    return {"invites": await list_invites()}


@app.post("/admin/invites/{code}/expire", dependencies=[Depends(verify_admin)])
async def admin_expire_invite(code: str):
    if not await expire_invite(code):
        raise HTTPException(status_code=502, detail="ยกเลิกรหัสไม่สำเร็จ")
    await log_audit(None, "admin", "expire_invite", "invite", code, None, None)
    return {"code": code, "status": "expired"}


@app.get("/admin/audit-logs", dependencies=[Depends(verify_admin)])
async def admin_audit_logs(days: int = 7, target_owner: str | None = None):
    since = (datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), 365))).isoformat()
    return {"logs": await list_audit_logs(since, target_owner)}


@app.post("/admin/areas/backfill", dependencies=[Depends(verify_admin)])
async def admin_areas_backfill(only_missing: bool = True):
    """ใส่อำเภอให้แปลงที่มีอยู่แล้ว — คืนเฉพาะรหัสแปลง/รหัสอำเภอ"""
    out = []
    for p in await get_active_plots():
        if only_missing and p.get("area_name"):
            continue
        found = await _assign_area(p["id"], p["lat"], p["lng"])
        out.append({"plot_id": p["id"], "area_code": (found or {}).get("area_code"),
                    "in_config": bool((found or {}).get("area_code"))})
    return {"count": len(out), "plots": out}


@app.post("/admin/boundary-check/backfill", dependencies=[Depends(verify_admin)])
async def admin_boundary_backfill(only_missing: bool = True):
    """ตรวจคุณภาพขอบเขตย้อนหลังให้แปลงที่มีอยู่แล้ว (ทำแถบบนการ์ดแปลงเดิม) — คืนเฉพาะรหัสแปลง/รหัสคำเตือน"""
    out = []
    for p in await get_active_plots():
        if only_missing and p.get("boundary_check"):
            continue
        stored = await _store_boundary_check(p["id"], p["lat"], p["lng"], p.get("polygon"),
                                             p.get("crop_ids"), p.get("plot_type"))
        out.append({"plot_id": p["id"], "stored": stored is not None,
                    "checked": bool(stored and stored.get("land_cover") is not None),
                    "warnings": [w["code"] for w in (stored or {}).get("warnings", [])]})
    return {"count": len(out), "plots": out}


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
