"""
scheduler.py — ระบบแจ้งเตือนอัตโนมัติ
  Job 1: สแกนแปลงทุกราย — แจ้งเตือนเมื่อเสี่ยงสูง + สรุปประจำวัน
  Job 2: ตรวจพยากรณ์ฝน 7 วัน + ผนวกดิน — แจ้งเตือนก่อนฝนหนัก/รากเน่า

ทั้งสองงานถูกทริกเกอร์จากภายนอก (GitHub Actions → /admin/trigger/daily-scan,
/admin/trigger/rain-alert) ไม่ใช่ cron ในโปรเซสนี้ — ดูเหตุผลเต็มที่ create_scheduler()
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.concurrency import run_in_threadpool

from database import (get_all_reports, save_analysis, get_user_plots,
                      get_notifiable_users, get_recent_analyses,
                      get_latest_plot_analysis, save_grid_snapshot,
                      get_digest_users, get_users_by_hour,
                      delete_old_grid_snapshots, get_swab_level_days_ago,
                      get_scheduler_last_run, set_scheduler_last_run,
                      PRESET_NOTIFY_HOURS, get_plot_analysis_since)
from gee_analysis import analyze_durian_plot, get_moisture_grid
from rule_engine import format_message, compute_risk_level
from flex_messages import (build_weekly_alert_flex, build_escalation_flex,
                           build_daily_digest_message, build_result_flex,
                           format_swab_trend)
from plot_image_service import build_plot_grid_image_url, delete_old_plot_images
from line_sender import send_line_message
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


ESCALATION_DAYS = 2  # แจ้งเตือนฉุกเฉินถ้าเสี่ยงสูงติดต่อกัน ≥ n วัน (scan รายวัน)
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

    # ดึง unique users จาก reports
    all_reports = await get_all_reports(limit=1000)
    seen_users: set[str] = set()
    for r in all_reports:
        seen_users.add(r["user_id"])

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
                    })

                    if not should_notify:
                        continue  # ยังไม่ถึงเวลาแจ้งเตือนของ user คนนี้ในรอบนี้

                    if _is_high_risk(data) and user_id in notifiable:
                        # ── รูปแผนที่ + จุดที่พบปัญหา (ถ้ามีขอบเขตแปลง) ── ผู้ใช้ขอ
                        # "ทุกคนได้รับภาพในการแจ้งตอนเช้าทุกคน" — เดิมมีแค่สรุปประจำวัน
                        # (opt-in, มีแค่ 1 ใน 8 คนเปิด) เท่านั้นที่แนบรูป escalation/
                        # weekly-alert (ที่คนส่วนใหญ่ได้รับจริง) ไม่เคยแนบเลย คำนวณครั้ง
                        # เดียวตรงนี้ใช้ร่วมกันทั้งสองแบบ (exclusive กันผ่าน continue
                        # ด้านล่างอยู่แล้ว) แปลงที่ไม่มีขอบเขต (จุดเดียว) ยังไม่มีตาราง
                        # ให้ทำรูปได้ — เป็นข้อจำกัดเดียวกับสรุปประจำวันเป๊ะ ไม่ใช่บั๊กใหม่
                        alert_img_url = None
                        alert_problem_points: list[dict] = []
                        if polygon and len(polygon) >= 3:
                            img_result = await build_plot_grid_image_url(
                                polygon, plot.get("name") or "แปลงของคุณ", plot_id=plot_id)
                            alert_img_url = img_result["url"]
                            alert_problem_points = img_result["problem_points"]

                        # ── Check escalation: ติดต่อกัน ≥ ESCALATION_DAYS วัน ──
                        if plot_id:
                            recent = await get_recent_analyses(
                                plot_id, days=ESCALATION_DAYS + 5
                            )
                            consec = _count_consecutive_high_risk(recent)
                            if consec >= ESCALATION_DAYS:
                                flex = build_escalation_flex(
                                    data, lat, lng, message, consec,
                                    plot_name=plot.get("name") or "",
                                    problem_points=alert_problem_points,
                                )
                                await send_line_message(user_id, message, flex=flex,
                                                        image_url=alert_img_url)
                                counters["escalated"] += 1
                                logger.warning(
                                    f"🚨 Escalation alert: {user_id} plot {plot_id} "
                                    f"— high risk {consec} consecutive days"
                                )
                                continue

                        flex = build_weekly_alert_flex(data, lat, lng, message,
                                                       plot_name=plot.get("name") or "",
                                                       problem_points=alert_problem_points)
                        await send_line_message(user_id, message, flex=flex, image_url=alert_img_url)
                        counters["alerted"] += 1

                # ── สรุปแปลงประจำวัน — ส่งการ์ดเต็มรูปแบบ (เหมือนกดตรวจสอบแปลงเอง)
                # พร้อมรูปแผนที่ความชื้น ทีละแปลง หลังวนครบทุกแปลงของ user นี้ ──
                # v2 (ตามที่ผู้ใช้ขอ): เดิมส่งการ์ดสรุปย่อบรรทัดเดียวต่อแปลง อ่านไม่เห็น
                # รายละเอียด — เปลี่ยนมาส่งข้อความสั้นๆ นำก่อน 1 ข้อความ (ภาพรวมเร็วๆ)
                # แล้วตามด้วยการ์ดเต็ม + รูปของทุกแปลง ทีละใบเหมือนผลตรวจสอบปกติทุกประการ
                # (ใช้ build_result_flex ตัวเดียวกับ /analyze ตรงๆ ไม่ทำการ์ดแยกใหม่ —
                # กันสองจุดสร้างการ์ดหน้าตาต่างกันแล้วหลุดซิงก์กันในอนาคต)
                if should_notify and user_id in digest_users and plot_summaries:
                    intro = build_daily_digest_message(plot_summaries)
                    await send_line_message(user_id, intro)
                    for p in plot_summaries:
                        img_url = None
                        problem_points = []
                        if p["polygon"] and len(p["polygon"]) >= 3:
                            img_result = await build_plot_grid_image_url(p["polygon"], p["name"],
                                                                         plot_id=p.get("plot_id"))
                            img_url = img_result["url"]
                            problem_points = img_result["problem_points"]

                        # ── แนวโน้มเทียบสัปดาห์ก่อน (เฉพาะสรุปประจำวัน ตามที่ผู้ใช้ขอ) ──
                        # ของแถม ไม่ควรทำให้การ์ดหลักพังถ้า query พลาด (เช่นแปลงใหม่)
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
                        await send_line_message(user_id, p["message"], flex=plot_flex, image_url=img_url)
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
async def rain_alert_job(hour: int | None = None):
    """
    ตรวจพยากรณ์ฝน 7 วัน + ผนวกข้อมูลดิน → แจ้งเตือนรวม

    hour: เหมือน daily_scan_job() — กรองเฉพาะผู้ใช้ที่ตั้ง notify_hour ตรงกับค่านี้
    """
    logger.info(f"🌧️ Combined weather+soil alert scan started (hour={hour})")

    all_reports = await get_all_reports(limit=1000)

    seen_users: set[str] = set()
    for r in all_reports:
        seen_users.add(r["user_id"])

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

                    if soil_data:
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

                        if alert["should_notify"]:
                            # ผู้ใช้ขอ "ทุกคนได้รับภาพในการแจ้งตอนเช้าทุกคน" — แนบรูป
                            # แผนที่เหมือนกับที่ทำใน daily_scan_job (เฉพาะแปลงที่มี
                            # ขอบเขต ถึงจะมีตารางจุดให้ทำรูปได้)
                            rain_img_url = None
                            if polygon and len(polygon) >= 3:
                                img_result = await build_plot_grid_image_url(
                                    polygon, name, plot_id=plot_id)
                                rain_img_url = img_result["url"]

                            flex = build_combined_alert_flex(
                                forecast, soil_risk, alert,
                                lat, lng, plot_name=name)
                            msg = build_combined_alert_message(
                                forecast, soil_risk, alert,
                                lat, lng, plot_name=name)
                            await send_line_message(user_id, msg, flex=flex, image_url=rain_img_url)
                            counters["combined_alerts"] += 1
                            logger.info(
                                f"{alert['alert_title']} → {user_id} plot '{name}' "
                                f"rain={forecast['total_rain_mm']}mm "
                                f"soil_score={soil_risk['soil_risk_score']} "
                                f"level={alert['alert_level']}"
                            )
                    else:
                        # ── Fallback: ไม่มีข้อมูลดิน → ใช้ rain-only alert ──
                        if forecast["is_heavy_rain"]:
                            rain_img_url = None
                            if polygon and len(polygon) >= 3:
                                img_result = await build_plot_grid_image_url(
                                    polygon, name, plot_id=plot_id)
                                rain_img_url = img_result["url"]

                            flex = build_rain_alert_flex(
                                forecast, lat, lng, plot_name=name)
                            msg = build_rain_alert_message(
                                forecast, lat, lng, plot_name=name)
                            await send_line_message(user_id, msg, flex=flex, image_url=rain_img_url)
                            counters["alerted"] += 1
                            logger.info(
                                f"🌧️ Rain-only alert → {user_id} plot '{name}' "
                                f"— {forecast['total_rain_mm']}mm"
                            )

            except Exception as e:
                logger.error(f"Rain alert failed for {user_id}: {e}")

    await asyncio.gather(*(_process_user(u) for u in seen_users))

    logger.info(
        f"✅ Weather+soil alert scan done — "
        f"{counters['combined_alerts']} combined + {counters['alerted']} rain-only alerts sent"
    )
