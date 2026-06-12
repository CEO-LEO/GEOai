import os
import json
import csv
import logging
import tempfile
import io
import secrets
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

import ee
from fastapi import FastAPI, HTTPException, Depends, Security
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
except Exception:
    _get_model_meta = None
from rule_engine import format_message
from flex_messages import build_result_flex
from line_sender import send_line_message, get_failed_queue, get_retry_stats
from database import (save_analysis, get_all_reports, save_plot,
                      get_user_plots, get_plot_history, set_notify,
                      delete_plot, find_nearby_plot, seed_demo_data,
                      upsert_user)
from webhook import router as webhook_router
from scheduler import create_scheduler
from middleware import RateLimitMiddleware
from cache import cache_stats
from i18n import t, Lang
import log_buffer

logging.basicConfig(level=logging.INFO)
log_buffer.install()
logger = logging.getLogger(__name__)

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
    logger.info("Scheduler started — weekly scan every Monday 07:00")

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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "GEOai", "cache": cache_stats()}


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/")


@app.get("/liff/config.js")
async def liff_config():
    """Inject API_URL and LIFF_ID from env vars into frontend JS"""
    liff_url = os.environ.get("LIFF_URL", "")
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


@app.post("/analyze")
async def analyze(req: AnalysisRequest):
    logger.info(f"Analyzing plot: lat={req.lat}, lng={req.lng}, user={req.user_id}")
    try:
        data     = analyze_durian_plot(req.lat, req.lng, polygon=req.polygon)
        message  = format_message(data, req.lat, req.lng)
        flex     = build_result_flex(data, req.lat, req.lng, message)

        # บันทึก user (upsert) + แปลง + ผลวิเคราะห์
        await upsert_user(req.user_id, req.display_name)
        plot_id = await save_plot(req.user_id, req.lat, req.lng,
                                  req.plot_name, req.area_rai,
                                  polygon=req.polygon)
        await send_line_message(req.user_id, message, flex=flex)
        await save_analysis(req.user_id, data, message, plot_id=plot_id)
        return {"status": "ok", "plot_id": plot_id, "data": data}
    except ee.EEException as e:
        logger.error(f"GEE error: {e}")
        raise HTTPException(status_code=502, detail="ไม่สามารถดึงข้อมูลดาวเทียมได้ในขณะนี้")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน")


@app.get("/analyze/preview")
async def analyze_preview(lat: float, lng: float):
    """
    GET endpoint สำหรับทดสอบวิเคราะห์เร็ว (ไม่บันทึก DB / ไม่ส่ง LINE)
    ใช้: /analyze/preview?lat=12.61&lng=102.10
    """
    try:
        data    = analyze_durian_plot(lat, lng)
        message = format_message(data, lat, lng)
        return {"status": "ok", "data": data, "message": message}
    except Exception as e:
        logger.error(f"Preview error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

    def _level(r):
        if r.get("ndvi_change", 0) < -0.20 or \
           (r.get("elevation_diff", 0) < -1.5 and r.get("soil_moisture_vv", 0) > -10) or \
           r.get("displacement_level") == "high":
            return "high"
        if r.get("ndvi_change", 0) < -0.10 or r.get("elevation_diff", 0) < -1.5 or \
           r.get("displacement_level") == "medium":
            return "medium"
        return "ok"

    levels       = [_level(r) for r in reports]
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
    """เปิด/ปิดการแจ้งเตือนรายสัปดาห์"""
    ok = await set_notify(user_id, enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="อัพเดตการตั้งค่าไม่สำเร็จ")
    return {"user_id": user_id, "notify_weekly": enabled}


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
    return {"plot_id": plot_id, "count": len(rows), "history": rows}


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


# ── Static file mounts (MUST be after all API routes) ────
app.mount("/liff", StaticFiles(directory=str(_BASE_DIR / "liff"), html=True), name="liff")
app.mount("/dashboard", StaticFiles(directory=str(_BASE_DIR / "dashboard"), html=True), name="dashboard")
