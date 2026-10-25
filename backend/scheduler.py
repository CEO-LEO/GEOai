"""
scheduler.py — ระบบแจ้งเตือนอัตโนมัติ
  Job 1: สแกนแปลงทุกราย — แจ้งเตือนเมื่อความเสี่ยง "เพิ่มขึ้น" เป็นสูง + สรุปรายสัปดาห์
  Job 2: ตรวจพยากรณ์ฝน 7 วัน + ผนวกดิน — แจ้งเตือนก่อนฝนหนัก/รากเน่า

ทั้งสองงานถูกทริกเกอร์จากภายนอก (GitHub Actions → /admin/trigger/daily-scan,
/admin/trigger/rain-alert) ไม่ใช่ cron ในโปรเซสนี้ — ดูเหตุผลเต็มที่ create_scheduler()
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.concurrency import run_in_threadpool

from database import (get_all_user_ids, save_analysis, get_user_plots,
                      get_notifiable_users,
                      get_latest_plot_analysis, save_grid_snapshot,
                      get_digest_users, get_users_by_hour,
                      delete_old_grid_snapshots, get_swab_level_days_ago,
                      get_scheduler_last_run, set_scheduler_last_run,
                      PRESET_NOTIFY_HOURS, get_plot_analysis_since,
                      get_previous_risk_level, get_last_sent_message)
from gee_analysis import analyze_durian_plot, get_moisture_grid
from rule_engine import format_message, compute_risk_level
from flex_messages import (build_weekly_alert_flex,
                           build_daily_digest_message, build_result_flex,
                           format_swab_trend)
from plot_image_service import build_plot_grid_image_url, delete_old_plot_images
from line_sender import send_line_message
import risk_v2
import risk_display
from weather_alert import (
    get_7day_rain,
    build_rain_alert_flex,
    build_rain_alert_message,
    assess_soil_waterlog_risk,
    evaluate_combined_risk,
    build_combined_alert_message,
    build_combined_alert_flex,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────
# Risk thresholds (ส่งเตือนเฉพาะกรณีเสี่ยง)
# ─────────────────────────────────────────────────
def _is_high_risk(data: dict) -> bool:
    """
    เดิมเขียน threshold เองที่นี่ (NDVI < -0.10) ซึ่งจริงๆ เป็นเกณฑ์ "น่ากังวล/ปานกลาง"
    ของทั้งระบบ ไม่ใช่เกณฑ์ "สูง" (-0.20) ทำให้แจ้งเตือนไวเกินไปเทียบกับที่ dashboard/LIFF
    แสดง (ดู formula-audit) — ใช้ overall_risk_level ที่ analyze_durian_plot() คำนวณ
    มาให้แล้วแทน (ถ้าไม่มี เช่นแถวประวัติเก่าจาก get_recent_analyses ที่ query แค่บาง
    column สำหรับเช็ค escalation ก็ fallback ไปคำนวณจากเท่าที่มีด้วยฟังก์ชันเดียวกัน)
    """
    overall = data.get("overall_risk_level")
    if overall is None:
        overall = compute_risk_level(data)
    return overall == "high"


ESCALATION_DAYS = 2  # (ไม่ได้ใช้ส่งแล้วตั้งแต่ Phase 1b — คงไว้ให้ _count_consecutive_high_risk/test)

# Phase 1b — ลดข้อความ LINE (OA แพลน Free 300 ข้อความ/เดือน หมดตั้งแต่ราววันที่ 7)
DIGEST_WEEKDAY = 0             # สรุปแปลงส่งสัปดาห์ละครั้ง วันจันทร์ (0=จันทร์ ตามเวลาไทย)
DIGEST_MAX_IMAGES = 3          # 1 push ได้ 5 message: ข้อความนำ + carousel + รูปไม่เกิน 3
RISK_REMINDER_AFTER_DAYS = 7   # ยังสูงต่อเนื่องเกิน n วันหลังเตือน → เตือนซ้ำ "ครั้งเดียว"
RAIN_ALERT_MAX_IMAGES = 3      # เตือนฝนรวมหลายแปลง: carousel 1 + รูปไม่เกิน 3
RAIN_ALERT_DEDUPE_DAYS = 7     # ไม่ส่งเตือนฝนระดับเดิม/ต่ำกว่าซ้ำภายใน n วัน (= ช่วงพยากรณ์ 7 วัน)
_RAIN_LEVEL_RANK = {"watch": 1, "warning": 2, "critical": 3}


SHADOW_TIMEOUT_S = 45


async def _attach_shadow_risk(data: dict, lat: float, lng: float, polygon: list | None) -> dict:
    """
    รันคู่ (shadow) วิธีวัดความเสี่ยงใหม่ — เก็บใน full_data["risk_v2"] และสัปดาห์ละครั้ง
    full_data["plot_profile"] ไม่แสดงผู้ใช้ ไม่ใช้ตัดสินการแจ้งเตือน ล้มเหลวได้โดยไม่กระทบงานหลัก
    คืน dict ใหม่ (ไม่แก้ dict เดิมซึ่งอาจเป็นตัวเดียวกับที่อยู่ใน cache ของ analyze_durian_plot)
    """
    data = dict(data)
    try:
        data["risk_v2"] = await asyncio.wait_for(
            run_in_threadpool(risk_v2.get_wetness_anomaly, lat, lng, polygon), SHADOW_TIMEOUT_S)
    except Exception as e:
        logger.warning(f"risk_v2 shadow failed: {type(e).__name__}")
        data["risk_v2"] = {"method": risk_v2.METHOD, "status": "error", "error": type(e).__name__}
    if _is_digest_day():
        try:
            data["plot_profile"] = await asyncio.wait_for(
                run_in_threadpool(risk_v2.get_plot_profile, lat, lng, polygon), SHADOW_TIMEOUT_S)
        except Exception as e:
            logger.warning(f"plot_profile failed: {type(e).__name__}")
    return data


def _risk_increased_to_high(current_high: bool, previous_level: str | None) -> bool:
    """
    แจ้งเตือนเฉพาะเมื่อความเสี่ยง "เพิ่มขึ้น" เป็นสูง — เดิมส่งทุกวันที่ผลเป็น "สูง"
    (30 วันล่าสุด 82% ของผลเป็นสูง แทบทุกแปลงโดนเตือนทุกวัน คำเตือนเลยไม่มีความหมาย
    และกินโควตา) ไม่มีผลก่อนหน้า (แปลงใหม่) ถือว่าเพิ่มขึ้น
    """
    return current_high and previous_level != "high"


def _is_digest_day(now: datetime | None = None) -> bool:
    now = now or datetime.now(_BANGKOK_TZ)
    return now.astimezone(_BANGKOK_TZ).weekday() == DIGEST_WEEKDAY
GRID_SNAPSHOT_TIMEOUT_S = 45  # กันจุดชื้นสะสม (grid snapshot) ค้างนานเกินไปต่อแปลง

# เดิมวนทีละ user ทีละแปลงล้วนๆ (sequential) — ผู้ใช้หลักสิบคน x หลายแปลง x GEE call
# ที่ใช้เวลาหลายวินาที/ครั้ง รวมกันแล้วงานสแกนรายวันอาจกินเวลานานหลายนาทีจนเสี่ยง
# ทับเวลาที่ trigger รอบถัดไปมาถึง — คุมด้วย Semaphore ให้ประมวลผลหลาย user พร้อมกัน
# ได้สูงสุด GEE_CONCURRENCY คน ณ ขณะหนึ่ง (ยังไม่รู้โควตาจริงของ GEE service account
# ที่โปรเจกต์นี้ใช้ — ผู้ใช้ต้องเช็คเองใน Google Cloud Console — เลยเลือกค่าระมัดระวัง
# ไว้ก่อน ปรับขึ้นได้ภายหลังถ้าเห็นว่าปลอดภัยจริง) แต่ละ user ยังประมวลผลแปลงของ
# ตัวเองทีละแปลงเหมือนเดิม (ไม่ทวีคูณ concurrency อีกชั้น เพราะส่วนใหญ่มี 1-2 แปลง)
GEE_CONCURRENCY = 5


def _count_consecutive_high_risk(analyses: list[dict]) -> int:
    """นับจำนวนผลวิเคราะห์ล่าสุดที่เสี่ยงสูงติดต่อกัน"""
    count = 0
    for a in analyses:  # เรียงจากล่าสุดไปเก่า
        if _is_high_risk(a):
            count += 1
        else:
            break
    return count


# ─────────────────────────────────────────────────
# Main job
# ─────────────────────────────────────────────────
def _bangkok_day_start_utc() -> str:
    """เที่ยงคืนของวันนี้ตามเวลาไทย แปลงเป็น UTC ISO string — ใช้เป็นเส้นแบ่ง
    "คำนวณไปแล้ววันนี้หรือยัง" ใน get_plot_analysis_since()"""
    now_bkk = datetime.now(_BANGKOK_TZ)
    midnight_bkk = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_bkk.astimezone(timezone.utc).isoformat()


async def daily_scan_job(hour: int | None = None):
    """
    วิเคราะห์ทุกแปลงของทุกราย — ส่งเตือนเฉพาะกรณีเสี่ยง + สรุปประจำวัน

    hour: ชั่วโมง (เวลาไทย) ที่ถูกทริกเกอร์มา — ใช้กรองเฉพาะขั้นตอน "ส่ง" (แจ้งเตือน/
    สรุปประจำวัน) เท่านั้น ผู้ใช้ที่ notify_hour ไม่ตรงกับค่านี้จะไม่ได้รับอะไรในรอบนี้
    ถ้าไม่ระบุ (None) — ส่งให้ทุกคนไม่กรอง (ใช้ตอนสั่งรันผ่าน /admin/trigger/daily-scan
    โดยไม่ใส่ ?hour= เช่นตอนทดสอบด้วยมือ)

    v6 (ผู้ใช้ขอ "อยากให้ส่งเร็วกว่านี้ อาจต้องเริ่มทำตั้งแต่ตี 5 แล้วค่อยส่งตอน
    6-7 โมง"): เดิมขั้นตอน "คำนวณ" (เรียก GEE) ก็ถูกกรองด้วย notify_hour เหมือนกัน
    — user ที่ตั้งเวลาไว้ 9 โมง กว่าจะเริ่มยิง GEE ก็ตอน 9 โมงพอดี ผลวิเคราะห์เลยมาถึง
    ช้ากว่าที่ควร (ต้องรอ GEE คำนวณเสร็จก่อนถึงส่งได้จริง) ตอนนี้แยกสองขั้นตอนออกจากกัน:
    "คำนวณ" ทำให้ทุกคนทุกรอบที่ trigger เข้ามา แต่ข้ามแปลงที่คำนวณไปแล้ววันนี้ (ดู
    get_plot_analysis_since) ทำให้รอบแรกสุดของวัน (ปกติคือ 05:00 น.) คำนวณให้ทุกคน
    รวดเดียว รอบถัดๆ ไปของคนอื่นแค่ "อ่าน" ผลที่มีอยู่แล้วมาส่ง ไม่ต้องรอ GEE อีกเลย —
    ส่วน "ส่ง" (escalation/weekly-alert/digest) ยังกรองตาม notify_hour เหมือนเดิม
    ทุกประการ ไม่เปลี่ยนพฤติกรรมฝั่งนี้เลย
    """
    logger.info(f"🕐 Daily scan started (hour={hour})")

    # เก็บกวาด grid_snapshots เก่า (ดู delete_old_grid_snapshots — ตารางนี้โตไม่จำกัด
    # มาตลอดเพราะไม่เคยมีการลบ) ก่อนเริ่มสแกน ไม่ให้ล้มทั้งงานถ้าเก็บกวาดพัง
    try:
        await delete_old_grid_snapshots()
    except Exception as e:
        logger.warning(f"grid_snapshots cleanup failed (non-fatal): {e}")
    try:
        await delete_old_plot_images()
    except Exception as e:
        logger.warning(f"plot_images cleanup failed (non-fatal): {e}")

    # รายชื่อผู้ใช้ทั้งหมดจากตาราง users (แบ่งหน้า) — เดิมหาจาก "รายงานล่าสุด 1,000
    # แถว" พอแปลงเกินราว 1,000 แปลง ผู้ใช้บางคนจะหลุดจากการสแกนถาวรโดยไม่มี error
    seen_users = await get_all_user_ids()

    # หา user ที่ notify_hour ตรงกับรอบนี้ — ใช้กรองแค่ขั้นตอน "ส่ง" ด้านล่าง
    # (ขั้นตอน "คำนวณ" ทำให้ทุกคนเสมอ ไม่กรองด้วยตัวนี้ — ดู docstring ด้านบน)
    hour_users: set[str] | None = None
    if hour is not None:
        hour_users = await get_users_by_hour(hour)  # None = คอลัมน์ยังไม่มี (ยัง migrate ไม่เสร็จ)

    notifiable   = await get_notifiable_users()
    digest_users = await get_digest_users()
    counters = {"computed": 0, "reused": 0, "alerted": 0, "escalated": 0, "digested": 0}
    semaphore = asyncio.Semaphore(GEE_CONCURRENCY)
    day_start_utc = _bangkok_day_start_utc()

    async def _process_user(user_id: str):
        # None หรือ hour_users เป็น None (คอลัมน์ยังไม่มี) = ไม่กรอง ส่งให้ทุกคน
        should_notify = hour is None or hour_users is None or user_id in hour_users

        async with semaphore:
            try:
                plots = await get_user_plots(user_id)
                if not plots:
                    return

                # เก็บผลทุกแปลงของ user นี้ไว้ ถ้าเปิด "สรุปแปลงประจำวัน" จะส่งการ์ดเต็ม
                # (เหมือน /analyze ปกติ) ทีละแปลงหลังวนครบทุกแปลง — ไม่ว่าแปลงไหนจะเสี่ยงหรือไม่
                plot_summaries: list[dict] = []

                for plot in plots:
                    lat = float(plot["lat"])
                    lng = float(plot["lng"])
                    plot_id = plot.get("id")
                    polygon = plot.get("polygon")

                    # ── คำนวณ (ถ้ายังไม่เคยทำวันนี้) หรือใช้ผลที่มีอยู่แล้ว ──
                    cached = await get_plot_analysis_since(plot_id, day_start_utc) if plot_id else None
                    if cached is not None:
                        data = cached
                        counters["reused"] += 1
                    else:
                        # เรียกผ่าน threadpool กัน GEE call (blocking) ค้าง event loop
                        # ทั้งตัวระหว่างสแกน — เหมือน fix เดิมที่ทำกับ /analyze
                        data = await run_in_threadpool(analyze_durian_plot, lat, lng)
                        data = await _attach_shadow_risk(data, lat, lng, polygon)
                        await save_analysis(user_id, data, format_message(data, lat, lng),
                                            plot_id=plot_id)
                        counters["computed"] += 1

                        # ── จุดชื้นสะสม (สำหรับหาแนวโน้มทางน้ำใต้ผิวดิน) ── เฉพาะแปลง
                        # ที่วาดขอบเขตไว้ (polygon) เท่านั้น ถึงจะมีตาราง grid ให้เก็บ
                        # แยก try/except ต่างหาก ไม่ให้ grid ล้มเหลวกระทบการแจ้งเตือน
                        # เสี่ยงหลักของแปลงนี้ — ทำครั้งเดียวตอนคำนวณสด ไม่ทำซ้ำตอนอ่าน cache
                        if polygon and plot_id and len(polygon) >= 3:
                            try:
                                # allow_widen=True — งานพื้นหลังอัตโนมัติ ไม่ใช่
                                # ผู้ใช้กด interactive เอง (เหตุผลเดียวกับ
                                # plot_image_service.py) กันแปลงใหญ่ผิดปกติทำให้
                                # snapshot หายไปเงียบๆ แทนที่จะได้ค่าหยาบกว่าเดิม
                                points, _ = await asyncio.wait_for(
                                    run_in_threadpool(get_moisture_grid, polygon, 10, True),
                                    timeout=GRID_SNAPSHOT_TIMEOUT_S,
                                )
                                await save_grid_snapshot(plot_id, points)
                            except Exception as e:
                                logger.warning(f"Grid snapshot failed for plot {plot_id}: {e}")

                    message = format_message(data, lat, lng)
                    plot_summaries.append({
                        "name": plot.get("name") or "แปลงของคุณ",
                        "data": data,
                        "lat": lat,
                        "lng": lng,
                        "message": message,
                        "polygon": polygon,
                        "plot_id": plot_id,
                        "plot_type": plot.get("plot_type"),
                    })

                    if not should_notify:
                        continue  # ยังไม่ถึงเวลาแจ้งเตือนของ user คนนี้ในรอบนี้

                    # ── แจ้งเตือนเฉพาะเมื่อความเสี่ยงเพิ่มขึ้นเป็น "สูง" ──
                    # เทียบกับผลก่อนวันนี้ (ไม่ใช่ทุกวันที่เป็นสูง) และกันส่งซ้ำวันเดียวกัน
                    # ถ้ามี line_message_log — เลิกส่ง "escalation" ซ้ำทุกวันแล้ว (Phase 1b)
                    # โหมด "กำลังปรับปรุง": หยุดคำเตือนความเสี่ยงชั่วคราว (ยังใช้สูตรเดิม) — เตือนฝนยังส่ง
                    if (_is_high_risk(data) and user_id in notifiable and plot_id
                            and risk_display.risk_alerts_enabled()):
                        prev_level = await get_previous_risk_level(plot_id, day_start_utc)
                        kind = "risk_alert"
                        if not _risk_increased_to_high(True, prev_level):
                            # ยังสูงต่อเนื่อง — เตือนซ้ำครั้งเดียวถ้าเกิน 7 วันนับจากที่เตือน
                            if not await _needs_risk_reminder(plot_id):
                                continue
                            kind = "risk_reminder"
                        elif await get_last_sent_message(plot_id, "risk_alert", day_start_utc):
                            continue

                        alert_img_url = None
                        alert_problem_points: list[dict] = []
                        if polygon and len(polygon) >= 3:
                            img_result = await build_plot_grid_image_url(
                                polygon, plot.get("name") or "แปลงของคุณ", plot_id=plot_id,
                                plot_type=plot.get("plot_type"))
                            alert_img_url = img_result["url"]
                            alert_problem_points = img_result["problem_points"]

                        flex = build_weekly_alert_flex(data, lat, lng, message,
                                                       plot_name=plot.get("name") or "",
                                                       problem_points=alert_problem_points)
                        await send_line_message(user_id, message, flex=flex, image_url=alert_img_url,
                                                kind=kind,
                                                priority="critical" if kind == "risk_alert" else "normal",
                                                plot_id=plot_id, level="high")
                        counters["alerted"] += 1

                # ── สรุปแปลงรายสัปดาห์ (ผู้ใช้เลือกเปิดเอง) ── Phase 1b: เดิมส่งทุกวัน
                # ข้อความนำ 1 push + การ์ดพร้อมรูปอีก 1 push ต่อแปลง ตอนนี้ส่งสัปดาห์ละครั้ง
                # (วันจันทร์) รวมเป็น push เดียว: ข้อความนำ + carousel การ์ดทุกแปลง +
                # รูปแผนที่ไม่เกิน DIGEST_MAX_IMAGES รูป (1 push = 1 ข้อความในโควตา)
                if (should_notify and user_id in digest_users and plot_summaries
                        and _is_digest_day()):
                    bubbles, images = [], []
                    for p in plot_summaries[:10]:   # carousel รับสูงสุด 10 การ์ด
                        problem_points = []
                        if (p["polygon"] and len(p["polygon"]) >= 3
                                and len(images) < DIGEST_MAX_IMAGES):
                            img_result = await build_plot_grid_image_url(p["polygon"], p["name"],
                                                                         plot_id=p.get("plot_id"),
                                                                         plot_type=p.get("plot_type"))
                            problem_points = img_result["problem_points"]
                            if img_result["url"]:
                                images.append(img_result["url"])

                        swab_trend = None
                        current_level = p["data"].get("swab", {}).get("swab_level")
                        if current_level is not None and p.get("plot_id"):
                            try:
                                prev_level = await get_swab_level_days_ago(p["plot_id"], days_ago=7)
                                swab_trend = format_swab_trend(current_level, prev_level)
                            except Exception as e:
                                logger.warning(f"swab trend lookup failed for plot {p.get('plot_id')}: {e}")

                        plot_flex = build_result_flex(p["data"], p["lat"], p["lng"], p["message"],
                                                      swab_trend=swab_trend, problem_points=problem_points,
                                                      plot_name=p.get("name") or "")
                        bubbles.append(plot_flex["contents"])

                    intro = build_daily_digest_message(plot_summaries)
                    msgs = [{"type": "text", "text": intro},
                            {"type": "flex", "altText": "📋 สรุปแปลงรายสัปดาห์",
                             "contents": bubbles[0] if len(bubbles) == 1
                             else {"type": "carousel", "contents": bubbles}}]
                    msgs += [{"type": "image", "originalContentUrl": u, "previewImageUrl": u}
                             for u in images]
                    await send_line_message(user_id, intro, messages=msgs,
                                            kind="weekly_digest", priority="low")
                    counters["digested"] += 1

            except Exception as e:
                logger.error(f"Daily scan failed for {user_id}: {e}")

    # ประมวลผลทุก user พร้อมกัน (คุมสูงสุด GEE_CONCURRENCY คนพร้อมกันด้วย semaphore
    # ข้างบน) แทนการวน for ทีละคน — งานที่เคยกินเวลา ~N*T (N คน x T วินาที/คน) เหลือ
    # ประมาณ ceil(N/GEE_CONCURRENCY)*T แทน
    await asyncio.gather(*(_process_user(u) for u in seen_users))

    logger.info(
        f"✅ Daily scan done (hour={hour}) — {len(seen_users)} users, "
        f"{counters['computed']} plots freshly computed, {counters['reused']} reused "
        f"from earlier today, {counters['alerted']} alerts sent, "
        f"{counters['escalated']} escalations, {counters['digested']} daily digests"
    )


# ─────────────────────────────────────────────────
# Scheduler factory
# ─────────────────────────────────────────────────
def create_scheduler() -> AsyncIOScheduler:
    """
    สร้าง scheduler — เรียกจาก lifespan ใน main.py

    เดิม (v1) ผูก daily_scan_job/rain_alert_job ไว้ตรงนี้ด้วย CronTrigger 07:00/06:00
    — ใช้งานไม่ได้จริงบน Render free tier: เซิร์ฟเวอร์หลับหลัง idle ~15 นาที และ
    AsyncIOScheduler ใช้ MemoryJobStore สร้างใหม่ทุกครั้งที่โปรเซสเริ่ม (ตื่นจาก
    sleep = โปรเซสใหม่) ทำให้ misfire_grace_time ช่วยอะไรไม่ได้เลย ถ้าเซิร์ฟเวอร์
    หลับอยู่พอดีตอนถึงเวลา งานนั้นก็หายไปเงียบๆ ทั้งวัน (ยืนยันจริงจาก log:
    ไม่มี analyses row ใกล้ 07:00 น. เลยแม้แต่วันเดียว ทั้งที่มี keep-alive ping
    ช่วยแล้วก็ตาม — ดู .github/workflows/keep-alive.yml สำหรับรายละเอียดเต็ม)
    —
    ย้าย 2 งานนี้ไปให้ GitHub Actions ยิง POST ตรงมาที่ /admin/trigger/daily-scan
    และ /admin/trigger/rain-alert แทน (request เข้ามาเองจะปลุกเซิร์ฟเวอร์ก่อน
    รันงานทันที ไม่ต้องพึ่งเวลาปลุกที่แม่นยำ) — ห้ามเพิ่ม add_job ของ 2 งานนี้
    กลับมาที่นี่อีก ไม่งั้นจะรันซ้ำสองครั้งวันที่เซิร์ฟเวอร์บังเอิญตื่นอยู่พอดี
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")
    return scheduler


# ─────────────────────────────────────────────────
# Catch-up สำหรับชั่วโมงที่ GitHub Actions ทิ้งรอบ cron ไปเฉยๆ
# ─────────────────────────────────────────────────
# ยืนยันเกิดจริง 21-22 ส.ค. 2569: รอบ 07:00 น. หายไปทั้งรอบวันที่ 22 (ผู้ใช้ทุกคน
# ตั้ง notify_hour ไว้ 7 โมงหมด เลยไม่มีใครได้รับแจ้งเตือนเลยวันนั้น) วันที่ 21 รันได้
# แค่ 2 จาก 6 รอบที่ตั้งไว้ — GitHub เขียนไว้เองว่า scheduled workflow อาจถูก "ทิ้ง"
# ได้ถ้าโหลดสูง ย้ายนาที cron ออกจากนาที 00 (ช่วงคับคั่งสุด) ไปแล้วที่
# keep-alive.yml แต่กันไว้อีกชั้นตรงนี้ด้วย เผื่อ GitHub ยังทิ้งรอบอยู่ดี — รอบถัดไป
# ที่ทริกเกอร์มาจะ "ตามงาน" ทุกชั่วโมงที่ยังไม่ได้ทำของวันนี้ ไม่ใช่แค่ hour เดียว
# ที่ trigger ส่งมา
_BANGKOK_TZ = timezone(timedelta(hours=7))


async def _catchup_hours(job_name: str, current_hour: int) -> list[int]:
    """
    คืนชั่วโมง (จาก PRESET_NOTIFY_HOURS) ที่ควรรันแต่ยังไม่ได้รันวันนี้ จนถึง
    current_hour เรียงจากเก่าไปใหม่ — ถ้าไม่มีอะไรพลาด (กรณีปกติ ชั่วโมงขยับทีละ 1)
    จะได้ list ที่มีแค่ current_hour ตัวเดียว เหมือนพฤติกรรมเดิมทุกประการ
    """
    today = datetime.now(_BANGKOK_TZ).date().isoformat()
    last = await get_scheduler_last_run(job_name)
    if not last or last.get("last_run_date") != today:
        return [h for h in PRESET_NOTIFY_HOURS if h <= current_hour]
    last_hour = last.get("last_run_hour", 0)
    return [h for h in PRESET_NOTIFY_HOURS if last_hour < h <= current_hour]


async def run_job_with_catchup(job_fn, job_name: str, hour: int | None):
    """
    ห่อ daily_scan_job/rain_alert_job — เรียกให้ครบทุกชั่วโมงที่ "พลาดไป" ของวันนี้
    แทนที่จะรันแค่ hour เดียวที่ trigger ส่งมา (ดูเหตุผลเต็มด้านบน) ใช้แทนการเรียก
    job_fn(hour) ตรงๆ ใน /admin/trigger/* (main.py)

    hour=None (เรียกทดสอบด้วยมือ ไม่ผ่าน cron ภายนอก) ข้ามการเช็ค catch-up ทั้งหมด
    รันครั้งเดียวแบบเดิม (ประมวลผลทุกคนไม่กรองตาม notify_hour)

    ถ้า job_fn ของบางชั่วโมงพัง (exception) ยังนับว่า "พยายามแล้ว" ไม่ retry วนซ้ำ
    ไม่ให้ backlog พอกพูนจนงานหนักขึ้นเรื่อยๆ ถ้ามีปัญหาจริงจังกว่าแค่ cron หาย
    """
    if hour is None:
        await job_fn(hour=None)
        return

    hours = await _catchup_hours(job_name, hour)
    if not hours:
        logger.info(f"{job_name}: hour={hour} ทำไปแล้วของวันนี้ ข้ามรอบนี้")
        return
    if len(hours) > 1:
        logger.warning(f"{job_name}: ตามงานย้อนหลัง {len(hours)} ชั่วโมงที่พลาดไป: {hours}")

    for h in hours:
        try:
            await job_fn(hour=h)
        except Exception as e:
            logger.error(f"{job_name} catch-up run failed for hour={h}: {e}")

    today = datetime.now(_BANGKOK_TZ).date().isoformat()
    await set_scheduler_last_run(job_name, today, hour)


# ─────────────────────────────────────────────────
# Rain alert job (Job 2)
# ─────────────────────────────────────────────────
async def _needs_risk_reminder(plot_id: int) -> bool:
    """
    เตือนซ้ำ "ครั้งเดียว" เมื่อแปลงยังสูงต่อเนื่องเกิน RISK_REMINDER_AFTER_DAYS วันหลังคำเตือน
    ข้อความล่าสุดของแปลง (risk_alert/risk_reminder ใน 60 วัน) ต้องเป็น risk_alert ที่ส่งไปนาน
    พอแล้ว — ถ้าเป็น risk_reminder แปลว่าเตือนซ้ำไปแล้ว ไม่ส่งอีกจนกว่าระดับจะลดแล้วขึ้นใหม่
    (ซึ่งจะเป็น risk_alert ครั้งใหม่) ต้องมี line_message_log (migrate_v13) ถ้าไม่มีจะไม่เตือนซ้ำ
    """
    since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    last = await get_last_sent_message(plot_id, ["risk_alert", "risk_reminder"], since)
    if not last or last.get("kind") != "risk_alert":
        return False
    sent_at = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - sent_at >= timedelta(days=RISK_REMINDER_AFTER_DAYS)


def _rain_priority(level: str) -> str:
    return "critical" if level == "critical" else "normal"


async def _rain_alert_is_new(plot_id: int | None, level: str) -> bool:
    """
    ส่งเตือนฝนเฉพาะเมื่อระดับ "สูงกว่า" ที่เคยส่งให้แปลงนี้ภายใน RAIN_ALERT_DEDUPE_DAYS วัน
    (เดิมส่งทุกวันที่พยากรณ์ยังเข้าเกณฑ์ — ฝนชุดเดียวกันโดนเตือนซ้ำหลายวัน)
    ต้องมีตาราง line_message_log (migrate_v13) ถ้ายังไม่มีจะส่งตามเดิม
    """
    if not plot_id:
        return True
    since = (datetime.now(timezone.utc) - timedelta(days=RAIN_ALERT_DEDUPE_DAYS)).isoformat()
    last = await get_last_sent_message(plot_id, "rain_alert", since)
    if not last:
        return True
    return _RAIN_LEVEL_RANK.get(level, 0) > _RAIN_LEVEL_RANK.get(last.get("level"), 0)


async def _send_grouped_rain_alert(user_id: str, pending: list[dict]) -> None:
    """
    เตือนฝนของทุกแปลงของผู้ใช้ใน push เดียว (1 ข้อความในโควตา) — 1 แปลง: การ์ด + รูป,
    หลายแปลง: carousel การ์ด (สูงสุด 10) + รูปแผนที่ไม่เกิน RAIN_ALERT_MAX_IMAGES
    บันทึก log แยกรายแปลงเพื่อให้การกันส่งซ้ำ 7 วันยังทำงานต่อแปลง
    """
    images: list[str] = []
    for item in pending:
        if len(images) >= RAIN_ALERT_MAX_IMAGES:
            break
        polygon = item.get("polygon")
        if polygon and len(polygon) >= 3:
            img_result = await build_plot_grid_image_url(polygon, item["name"], plot_id=item["plot_id"],
                                                         plot_type=item.get("plot_type"))
            if img_result["url"]:
                images.append(img_result["url"])

    if len(pending) == 1:
        messages = [pending[0]["flex"]]
    else:
        messages = [{
            "type": "flex",
            "altText": f"🌧️ แจ้งเตือนฝน {len(pending)} แปลงของคุณ — ปัดดูทีละแปลง",
            "contents": {"type": "carousel",
                         "contents": [item["flex"]["contents"] for item in pending[:10]]},
        }]
    messages += [{"type": "image", "originalContentUrl": u, "previewImageUrl": u} for u in images]

    priority = "critical" if any(_rain_priority(i["level"]) == "critical" for i in pending) else "normal"
    await send_line_message(user_id, pending[0]["msg"], messages=messages, kind="rain_alert",
                            priority=priority,
                            log_plots=[(i["plot_id"], i["level"]) for i in pending])


async def rain_alert_job(hour: int | None = None):
    """
    ตรวจพยากรณ์ฝน 7 วัน + ผนวกข้อมูลดิน → แจ้งเตือนรวม

    hour: เหมือน daily_scan_job() — กรองเฉพาะผู้ใช้ที่ตั้ง notify_hour ตรงกับค่านี้
    """
    logger.info(f"🌧️ Combined weather+soil alert scan started (hour={hour})")

    # ไม่หลุดเมื่อแปลงเกิน 1,000 (ดู daily_scan_job)
    seen_users: set[str] = set(await get_all_user_ids())

    if hour is not None:
        hour_users = await get_users_by_hour(hour)
        if hour_users is not None:
            seen_users &= hour_users

    counters = {"alerted": 0, "combined_alerts": 0}
    notifiable = await get_notifiable_users()
    # งานนี้ไม่ได้เรียก GEE ตรงๆ (ใช้ weather API + อ่านผลดินที่ analyze ไว้แล้วจาก
    # Supabase) แต่โครงสร้างเดิมเป็น sequential ทีละคนเหมือน daily_scan_job — ใช้
    # semaphore ผูกจำนวนความขนานเดียวกันเพื่อความสม่ำเสมอ กันยิง weather API/Supabase
    # พร้อมกันมากเกินไปตอนผู้ใช้เยอะขึ้นด้วย
    semaphore = asyncio.Semaphore(GEE_CONCURRENCY)

    async def _process_user(user_id: str):
        if user_id not in notifiable:
            return
        async with semaphore:
            try:
                plots = await get_user_plots(user_id)
                if not plots:
                    return

                # Phase 1b: เก็บคำเตือนของทุกแปลงก่อน แล้วส่งรวมเป็น push เดียวต่อคน
                pending: list[dict] = []
                for plot in plots:
                    lat     = float(plot["lat"])
                    lng     = float(plot["lng"])
                    name    = plot.get("name", "แปลงของคุณ")
                    plot_id = plot.get("id")
                    polygon = plot.get("polygon")

                    forecast = await get_7day_rain(lat, lng)

                    # ── ดึงข้อมูลดินล่าสุด (ถ้ามี) ──
                    soil_data = None
                    if plot_id:
                        soil_data = await get_latest_plot_analysis(plot_id)

                    # สวนยกร่อง (1c): ความชื้นดินจากดาวเทียมรวมน้ำในร่อง → ไม่ใช้ประเมินเสี่ยงน้ำขัง
                    # เตือนเฉพาะฝนหนักจากพยากรณ์ (ทางเดียวกับแปลงที่ยังไม่มีข้อมูลดิน)
                    if soil_data and plot.get("plot_type") != "raised_bed":
                        # ── Combined alert: อากาศ + ดิน ──
                        soil_risk = assess_soil_waterlog_risk(soil_data)
                        alert = evaluate_combined_risk(forecast, soil_risk)

                        # Gap 1: combined_score >= 60 → force critical + notify immediately
                        if alert["combined_score"] >= 60 and alert["alert_level"] != "critical":
                            alert = dict(alert)
                            alert["alert_level"] = "critical"
                            alert["should_notify"] = True
                            alert["alert_title"] = "🔴 วิกฤต: คะแนนความเสี่ยงสูง — ต้องดำเนินการทันที"
                            logger.warning(
                                f"🚨 Critical threshold override: {user_id} plot '{name}' "
                                f"combined_score={alert['combined_score']}"
                            )

                        if alert["should_notify"] and await _rain_alert_is_new(plot_id, alert["alert_level"]):
                            pending.append({
                                "flex": build_combined_alert_flex(forecast, soil_risk, alert,
                                                                  lat, lng, plot_name=name),
                                "msg": build_combined_alert_message(forecast, soil_risk, alert,
                                                                    lat, lng, plot_name=name),
                                "plot_id": plot_id, "level": alert["alert_level"],
                                "polygon": polygon, "name": name, "plot_type": plot.get("plot_type"),
                            })
                            counters["combined_alerts"] += 1
                            logger.info(
                                f"{alert['alert_title']} → {user_id} plot '{name}' "
                                f"rain={forecast['total_rain_mm']}mm "
                                f"soil_score={soil_risk['soil_risk_score']} "
                                f"level={alert['alert_level']}"
                            )
                    else:
                        # ── Fallback: ไม่มีข้อมูลดิน → ใช้ rain-only alert ──
                        if forecast["is_heavy_rain"] and await _rain_alert_is_new(plot_id, "warning"):
                            pending.append({
                                "flex": build_rain_alert_flex(forecast, lat, lng, plot_name=name),
                                "msg": build_rain_alert_message(forecast, lat, lng, plot_name=name),
                                "plot_id": plot_id, "level": "warning",
                                "polygon": polygon, "name": name, "plot_type": plot.get("plot_type"),
                            })
                            counters["alerted"] += 1
                            logger.info(
                                f"🌧️ Rain-only alert → {user_id} plot '{name}' "
                                f"— {forecast['total_rain_mm']}mm"
                            )

                if pending:
                    await _send_grouped_rain_alert(user_id, pending)

            except Exception as e:
                logger.error(f"Rain alert failed for {user_id}: {e}")

    await asyncio.gather(*(_process_user(u) for u in seen_users))

    logger.info(
        f"✅ Weather+soil alert scan done — "
        f"{counters['combined_alerts']} combined + {counters['alerted']} rain-only alerts sent"
    )
