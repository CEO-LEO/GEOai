"""
scheduler.py — ระบบแจ้งเตือนอัตโนมัติ
  Job 1 (ทุกวันจันทร์ 07:00): สแกนแปลงทุกราย — แจ้งเตือนเมื่อเสี่ยงสูง
  Job 2 (ทุกวัน 06:00): ตรวจพยากรณ์ฝน 7 วัน + ผนวกดิน — แจ้งเตือนก่อนฝนหนัก/รากเน่า
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import (get_all_reports, save_analysis, get_user_plots,
                      get_notifiable_users, get_recent_analyses,
                      get_latest_plot_analysis)
from gee_analysis import analyze_durian_plot
from rule_engine import format_message
from flex_messages import build_weekly_alert_flex, build_escalation_flex
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
    return (
        data["ndvi_change"] < -0.10
        or (data["elevation_diff"] < -1.5 and data["soil_moisture_vv"] > -10)
        or data.get("displacement", {}).get("change_level") == "high"
        or data.get("swab", {}).get("severity") == "high"  # v3: SWAB root-zone risk (นายายอาม)
    )


ESCALATION_WEEKS = 2  # แจ้งเตือนฉุกเฉินถ้าเสี่ยงสูงติดต่อกัน ≥ n สัปดาห์


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
async def weekly_scan_job():
    """วิเคราะห์ทุกแปลงของทุกราย — ส่งเตือนเฉพาะกรณีเสี่ยง"""
    logger.info("🕐 Weekly scan started")

    # ดึง unique users จาก reports
    all_reports = await get_all_reports(limit=1000)
    seen_users: set[str] = set()
    for r in all_reports:
        seen_users.add(r["user_id"])

    notifiable = await get_notifiable_users()
    total_plots = 0
    alerted = 0
    escalated = 0

    for user_id in seen_users:
        try:
            plots = await get_user_plots(user_id)
            if not plots:
                continue

            for plot in plots:
                total_plots += 1
                lat = float(plot["lat"])
                lng = float(plot["lng"])
                plot_id = plot.get("id")

                data    = analyze_durian_plot(lat, lng)
                message = format_message(data, lat, lng)
                await save_analysis(user_id, data, message,
                                    plot_id=plot_id)

                if _is_high_risk(data) and user_id in notifiable:
                    # ── Check escalation: ติดต่อกัน ≥ ESCALATION_WEEKS ──
                    if plot_id:
                        recent = await get_recent_analyses(
                            plot_id, weeks=ESCALATION_WEEKS + 1
                        )
                        consec = _count_consecutive_high_risk(recent)
                        if consec >= ESCALATION_WEEKS:
                            flex = build_escalation_flex(
                                data, lat, lng, message, consec
                            )
                            await send_line_message(user_id, message, flex=flex)
                            escalated += 1
                            logger.warning(
                                f"🚨 Escalation alert: {user_id} plot {plot_id} "
                                f"— high risk {consec} consecutive weeks"
                            )
                            continue

                    flex = build_weekly_alert_flex(data, lat, lng, message)
                    await send_line_message(user_id, message, flex=flex)
                    alerted += 1

        except Exception as e:
            logger.error(f"Weekly scan failed for {user_id}: {e}")

    logger.info(
        f"✅ Weekly scan done — {len(seen_users)} users, "
        f"{total_plots} plots scanned, {alerted} alerts sent, "
        f"{escalated} escalations"
    )


# ─────────────────────────────────────────────────
# Scheduler factory
# ─────────────────────────────────────────────────
def create_scheduler() -> AsyncIOScheduler:
    """สร้าง scheduler พร้อม jobs — เรียกจาก lifespan ใน main.py"""
    scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")

    # Job 1: ทุกวันจันทร์ 07:00 — สแกนความเสี่ยงดาวเทียม
    scheduler.add_job(
        weekly_scan_job,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="weekly_scan",
        replace_existing=True,
        max_instances=1,
    )

    # Job 2: ทุกวัน 06:00 — แจ้งเตือนพยากรณ์ฝน + รากเน่า (รากเน่าใช้เวลา 2-5 วัน)
    scheduler.add_job(
        rain_alert_job,
        trigger=CronTrigger(hour=6, minute=0),
        id="rain_alert",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler


# ─────────────────────────────────────────────────
# Rain alert job (Job 2)
# ─────────────────────────────────────────────────
async def rain_alert_job():
    """ตรวจพยากรณ์ฝน 7 วัน + ผนวกข้อมูลดิน → แจ้งเตือนรวม"""
    logger.info("🌧️ Combined weather+soil alert scan started")

    all_reports = await get_all_reports(limit=1000)

    seen_users: set[str] = set()
    for r in all_reports:
        seen_users.add(r["user_id"])

    alerted = 0
    combined_alerts = 0
    notifiable = await get_notifiable_users()

    for user_id in seen_users:
        if user_id not in notifiable:
            continue
        try:
            plots = await get_user_plots(user_id)
            if not plots:
                continue

            for plot in plots:
                lat     = float(plot["lat"])
                lng     = float(plot["lng"])
                name    = plot.get("name", "แปลงของคุณ")
                plot_id = plot.get("id")

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
                        flex = build_combined_alert_flex(
                            forecast, soil_risk, alert,
                            lat, lng, plot_name=name)
                        msg = build_combined_alert_message(
                            forecast, soil_risk, alert,
                            lat, lng, plot_name=name)
                        await send_line_message(user_id, msg, flex=flex)
                        combined_alerts += 1
                        logger.info(
                            f"{alert['alert_title']} → {user_id} plot '{name}' "
                            f"rain={forecast['total_rain_mm']}mm "
                            f"soil_score={soil_risk['soil_risk_score']} "
                            f"level={alert['alert_level']}"
                        )
                else:
                    # ── Fallback: ไม่มีข้อมูลดิน → ใช้ rain-only alert ──
                    if forecast["is_heavy_rain"]:
                        flex = build_rain_alert_flex(
                            forecast, lat, lng, plot_name=name)
                        msg = build_rain_alert_message(
                            forecast, lat, lng, plot_name=name)
                        await send_line_message(user_id, msg, flex=flex)
                        alerted += 1
                        logger.info(
                            f"🌧️ Rain-only alert → {user_id} plot '{name}' "
                            f"— {forecast['total_rain_mm']}mm"
                        )

        except Exception as e:
            logger.error(f"Rain alert failed for {user_id}: {e}")

    logger.info(
        f"✅ Weather+soil alert scan done — "
        f"{combined_alerts} combined + {alerted} rain-only alerts sent"
    )
