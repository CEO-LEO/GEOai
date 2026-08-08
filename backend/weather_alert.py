"""
weather_alert.py — ระบบแจ้งเตือนล่วงหน้าก่อนฤดูฝน
ดึงข้อมูล Open-Meteo (ฟรี ไม่ต้องมี API key) สำหรับพยากรณ์ฝน 7 วัน
ผนวกกับข้อมูลดิน (displacement, elevation, moisture) จากผลวิเคราะห์ดาวเทียม
ถ้าพบ "ดินเป็นแอ่ง/ดินทรุด" + "ฝนกำลังจะตกหนัก" → แจ้งเตือนน้ำขัง+งดปุ๋ย

ถูกเรียกจาก scheduler.py ทุกวันจันทร์
"""

import asyncio
import httpx
import logging
from typing import TypedDict

from fastapi.concurrency import run_in_threadpool
import gee_analysis

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WeatherNext (Google DeepMind AI forecast ผ่าน Earth Engine) ต้องขอสิทธิ์
# เข้าถึง dataset ก่อน — ระหว่างรออนุมัติ (หรือถ้า reject) ระบบจะ fallback ไป
# Open-Meteo อัตโนมัติ ไม่ต้องแก้โค้ด/deploy ใหม่เมื่อได้รับอนุมัติแล้ว
WEATHERNEXT_TIMEOUT_S = 25

# ─── Thresholds (ปรับสำหรับ อ.นายายอาม จ.จันทบุรี — ฝนสูงตลอดปี) ──────────────
# ฝนสะสม 7 วัน (มม.) ที่ถือว่า "หนัก" — ลดจาก 80 เหลือ 60 (เพราะดินชื้นอยู่แล้ว)
RAIN_THRESHOLD_MM   = 60.0
# ฝนสูงสุดต่อวัน (มม.) ที่ถือว่าหนักมาก
RAIN_DAILY_HEAVY_MM = 35.0
# ฝนปานกลาง (มม./7วัน) — ยังเสี่ยงถ้าดินแย่ (ลดจาก 50 → 40)
RAIN_MODERATE_MM    = 40.0
# ดินอยู่ต่ำกว่ารอบข้าง (ม.) → เป็นแอ่ง
BASIN_ELEV_DIFF     = -1.0
# Surface stability ต่ำ → ดินทรุด
STABILITY_LOW       = 0.5
STABILITY_CRITICAL  = 0.3
# VV backscatter change สูง → สัญญาณดินทรุด/เปลี่ยน
VV_CHANGE_WARN      = 1.5
VV_CHANGE_CRITICAL  = 3.0
# SWAB thresholds สำหรับ อ.นายายอาม (รากตื้น 30-50 ซม.)
SWAB_WARN_THRESHOLD = 0.10   # SWAB index > 0.10 = น้ำสูงเกินพอ (เตือนไวขึ้น)


class WeatherForecast(TypedDict):
    total_rain_mm:   float
    max_daily_mm:    float
    rainy_days:      int
    is_heavy_rain:   bool
    source:          str   # "weathernext" | "open-meteo" — ใช้ debug/แสดงผลว่าข้อมูลมาจากไหน


async def _get_7day_rain_weathernext(lat: float, lng: float) -> WeatherForecast:
    """
    พยากรณ์ฝน 7 วันจาก Google DeepMind WeatherNext (ผ่าน Earth Engine)
    ไม่มี retry — ถ้าล้มเหลว (ยังไม่ได้รับสิทธิ์เข้าถึง dataset, GEE ไม่พร้อม ฯลฯ)
    ให้ raise แล้วปล่อยให้ get_7day_rain() fallback ไป Open-Meteo ทันที
    """
    if not gee_analysis._gee_ready:
        raise RuntimeError("GEE not ready")
    result = await asyncio.wait_for(
        run_in_threadpool(gee_analysis.get_weathernext_rain_forecast, lat, lng, 7),
        timeout=WEATHERNEXT_TIMEOUT_S,
    )
    return WeatherForecast(
        total_rain_mm = result["total_rain_mm"],
        max_daily_mm  = result["max_daily_mm"],
        rainy_days    = result["rainy_days"],
        is_heavy_rain = result["is_heavy_rain"],
        source        = "weathernext",
    )


async def _get_7day_rain_openmeteo(lat: float, lng: float) -> WeatherForecast:
    """ดึงพยากรณ์ฝน 7 วันจาก Open-Meteo (ฟรี ไม่ต้องมี API key)"""
    params = {
        "latitude":         lat,
        "longitude":        lng,
        "daily":            "precipitation_sum",
        "timezone":         "Asia/Bangkok",
        "forecast_days":    7,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    daily_rain: list[float] = data.get("daily", {}).get("precipitation_sum", [])
    daily_rain = [v or 0.0 for v in daily_rain]

    total      = sum(daily_rain)
    max_daily  = max(daily_rain) if daily_rain else 0.0
    rainy_days = sum(1 for v in daily_rain if v > 5.0)

    return WeatherForecast(
        total_rain_mm  = round(total, 1),
        max_daily_mm   = round(max_daily, 1),
        rainy_days     = rainy_days,
        is_heavy_rain  = total >= RAIN_THRESHOLD_MM or max_daily >= 40.0,
        source         = "open-meteo",
    )


async def get_7day_rain(lat: float, lng: float) -> WeatherForecast:
    """
    ดึงพยากรณ์ฝน 7 วัน — ลอง WeatherNext (Google DeepMind AI) ก่อน
    ถ้าใช้ไม่ได้ (ยังไม่ได้รับสิทธิ์เข้าถึง/error ใดๆ) fallback ไป Open-Meteo
    ทันทีแบบไม่มี retry เพราะ WeatherNext มีความละเอียดพื้นที่หยาบ (~27กม./จุด)
    Open-Meteo ยังเป็นแหล่งหลักที่เชื่อถือได้และละเอียดกว่าสำหรับพยากรณ์รายจุด
    """
    try:
        return await _get_7day_rain_weathernext(lat, lng)
    except Exception as e:
        logger.info(f"WeatherNext unavailable ({e}) — falling back to Open-Meteo")
        return await _get_7day_rain_openmeteo(lat, lng)


def build_rain_alert_message(forecast: WeatherForecast,
                              lat: float, lng: float,
                              plot_name: str = "แปลงของคุณ") -> str:
    """สร้างข้อความแจ้งเตือนฝนล่วงหน้า"""
    level = "🔴 หนักมาก" if forecast["max_daily_mm"] >= 40 else "🟠 หนัก"
    lines = [
        f"🌧️ *แจ้งเตือนฝน 7 วันข้างหน้า — GEOai (อ.นายายอาม)*",
        f"📍 {plot_name} ({lat:.4f}, {lng:.4f})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌧️ ฝนสะสม 7 วัน:    {forecast['total_rain_mm']} มม.",
        f"⛈️  ฝนสูงสุดต่อวัน:   {forecast['max_daily_mm']} มม. ({level})",
        f"📅 จำนวนวันที่ฝนตก: {forecast['rainy_days']} วัน",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "⚠️ *ข้อแนะนำด่วนสำหรับทุเรียน อ.นายายอาม:*",
        "  • ตรวจร่องระบายน้ำให้เปิดโล่งก่อนฝนมา (รากทุเรียนตื้น 30-50 ซม.)",
        "  • ห้ามใส่ปุ๋ยหรือฉีดยาก่อน 24 ชม. ที่ฝนจะตก",
        "  • หากแปลงต่ำกว่ารอบข้าง ให้สูบน้ำออกไว้ล่วงหน้า",
        "  • พื้นที่ลาดชัน: น้ำไหลสะสมลงหุบโคนต้นได้เร็ว ตรวจร่องก่อน-หลังฝน",
        "",
        "🛡️ ข้อมูลอากาศ: Open-Meteo.com | GEOai v3.0 — อ.นายายอาม",
    ]
    return "\n".join(lines)


def build_rain_alert_flex(forecast: WeatherForecast,
                           lat: float, lng: float,
                           plot_name: str = "แปลงของคุณ") -> dict:
    """Flex Message สำหรับแจ้งเตือนฝน"""
    level_color  = "#C62828" if forecast["max_daily_mm"] >= 40 else "#E65100"
    level_icon   = "⛈️" if forecast["max_daily_mm"] >= 40 else "🌧️"
    level_label  = "ฝนหนักมาก" if forecast["max_daily_mm"] >= 40 else "ฝนหนัก"

    bar_pct = min(100, int(forecast["total_rain_mm"] / 150 * 100))

    return {
        "type": "flex",
        "altText": f"{level_icon} แจ้งเตือนฝน 7 วัน — {plot_name}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": level_color,
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"{level_icon} แจ้งเตือนฝน 7 วัน",
                                "color": "#FFFFFF",
                                "weight": "bold",
                                "size": "md",
                                "flex": 1
                            },
                            {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [{"type": "text", "text": level_label,
                                              "color": "#FFFFFF", "size": "xs",
                                              "weight": "bold"}],
                                "backgroundColor": "#00000033",
                                "paddingAll": "5px",
                                "cornerRadius": "10px"
                            }
                        ]
                    },
                    {"type": "text", "text": f"📍 {plot_name} | ({lat:.4f}, {lng:.4f})",
                     "color": "#FFFFFFCC", "size": "xs", "margin": "sm"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "spacing": "md",
                "contents": [
                    {
                        "type": "box", "layout": "horizontal", "spacing": "md",
                        "contents": [
                            _rain_stat("🌧️ ฝนสะสม", f"{forecast['total_rain_mm']} มม.", level_color),
                            _rain_stat("⛈️ สูงสุด/วัน", f"{forecast['max_daily_mm']} มม.", level_color),
                            _rain_stat("📅 วันฝนตก", f"{forecast['rainy_days']} วัน", "#555555"),
                        ]
                    },
                    {
                        "type": "box", "layout": "vertical",
                        "backgroundColor": "#EEEEEE", "height": "8px",
                        "cornerRadius": "4px",
                        "contents": [{
                            "type": "box", "layout": "vertical",
                            "backgroundColor": level_color, "height": "8px",
                            "cornerRadius": "4px", "width": f"{bar_pct}%",
                            "contents": []
                        }]
                    },
                    {"type": "separator"},
                    {
                        "type": "text", "text": "📋 ข้อแนะนำสำหรับทุเรียน",
                        "weight": "bold", "size": "sm", "color": "#333"
                    },
                    *[{"type": "box", "layout": "horizontal", "spacing": "sm",
                       "contents": [
                           {"type": "text", "text": "•", "size": "sm",
                            "color": level_color, "flex": 0},
                           {"type": "text", "text": tip, "size": "sm",
                            "wrap": True, "color": "#444", "margin": "sm"}
                       ]} for tip in [
                        "ตรวจร่องระบายน้ำให้เปิดโล่งก่อนฝนมา",
                        "งดใส่ปุ๋ยหรือฉีดยา 24 ชม. ก่อนฝนตก",
                        "แปลงต่ำ: สูบน้ำออกไว้ล่วงหน้า",
                    ]]
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "paddingAll": "12px", "backgroundColor": "#F5F5F5",
                "contents": [
                    {
                        "type": "button",
                        "action": {"type": "postback",
                                   "label": "🔍 ตรวจสอบแปลงนี้ด้วย",
                                   "data": "action=check"},
                        "style": "primary", "color": "#1a7a3c", "height": "sm"
                    },
                    {"type": "text",
                     "text": "🌤️ ข้อมูล: Open-Meteo.com | GEOai v1.0",
                     "size": "xxs", "color": "#AAAAAA", "align": "center",
                     "margin": "sm"}
                ]
            }
        }
    }


def _rain_stat(label: str, value: str, color: str) -> dict:
    return {
        "type": "box", "layout": "vertical", "flex": 1,
        "backgroundColor": "#F5F5F5", "cornerRadius": "8px", "paddingAll": "8px",
        "contents": [
            {"type": "text", "text": label, "size": "xxs", "color": "#888"},
            {"type": "text", "text": value, "size": "sm",
             "color": color, "weight": "bold", "margin": "sm", "wrap": True}
        ]
    }


# ═══════════════════════════════════════════════════════
# Part 2 — Combined Soil + Weather Risk Assessment
# ═══════════════════════════════════════════════════════

class SoilRiskProfile(TypedDict):
    is_basin:          bool   # อยู่ต่ำกว่ารอบข้าง (แอ่ง)
    is_subsiding:      bool   # ดินทรุด (displacement สูง)
    is_unstable:       bool   # surface stability ต่ำ
    is_waterlogged:    bool   # ดินชื้นเกินไปอยู่แล้ว
    risk_factors:      list[str]  # รายการปัจจัยเสี่ยง (ภาษาไทย)
    soil_risk_score:   int    # 0-100


def assess_soil_waterlog_risk(analysis: dict) -> SoilRiskProfile:
    """
    ประเมินความเสี่ยงน้ำขังจากข้อมูลดาวเทียม (ผลวิเคราะห์ล่าสุดของแปลง)
    analysis: row จาก get_latest_plot_analysis() หรือ analyze_durian_plot()
    """
    elev_diff   = float(analysis.get("elevation_diff") or 0)
    stability   = float(analysis.get("surface_stability") or 1.0)
    vv_change   = float(analysis.get("displacement_vv_change") or 0)
    moisture_vv = float(analysis.get("soil_moisture_vv") or -15)
    disp_level  = analysis.get("displacement_level") or "low"
    impact_sev  = analysis.get("land_impact_severity") or "low"

    score = 0
    factors: list[str] = []

    # ── 1. แอ่ง: ที่ต่ำกว่ารอบข้าง ──
    is_basin = elev_diff < BASIN_ELEV_DIFF
    if is_basin:
        score += 30
        factors.append(f"แปลงอยู่ต่ำกว่ารอบข้าง {abs(elev_diff):.1f} ม. (เป็นแอ่ง)")

    # ── 2. ดินทรุด: VV เปลี่ยนมาก หรือ displacement สูง ──
    is_subsiding = abs(vv_change) >= VV_CHANGE_WARN or disp_level in ("high", "medium")
    if abs(vv_change) >= VV_CHANGE_CRITICAL:
        score += 30
        factors.append(f"ดินทรุดตัวรุนแรง (SAR เปลี่ยน {abs(vv_change):.1f} dB)")
    elif is_subsiding:
        score += 15
        factors.append(f"ดินมีสัญญาณทรุดตัว (SAR เปลี่ยน {abs(vv_change):.1f} dB)")

    # ── 3. พื้นไม่เสถียร ──
    is_unstable = stability < STABILITY_LOW
    if stability < STABILITY_CRITICAL:
        score += 25
        factors.append(f"พื้นดินไม่เสถียรมาก (stability {stability:.2f})")
    elif is_unstable:
        score += 10
        factors.append(f"พื้นดินเสถียรต่ำ (stability {stability:.2f})")

    # ── 4. ดินชื้นเกินอยู่แล้ว ──
    is_waterlogged = moisture_vv > -10
    if is_waterlogged:
        score += 15
        factors.append(f"ดินชื้นเกินปกติ (VV {moisture_vv:.1f} dB)")

    # ── 5. SWAB: สมดุลน้ำ-อากาศในดิน (การากตื้น อ.นายายอาม) ──
    swab       = analysis.get("swab") or {}
    swab_idx   = float(swab.get("swab_index") or 0.0)
    water_pct  = float(swab.get("soil_water_pct") or 45.0)
    air_pct    = float(swab.get("soil_air_pct") or 30.0)
    swab_status = swab.get("status", "optimal")
    if swab_idx >= SWAB_WARN_THRESHOLD:
        swab_bonus = min(20, int(swab_idx * 40))
        score += swab_bonus
        factors.append(
            f"น้ำในดินสูง {water_pct:.0f}% / อากาศ {air_pct:.0f}% — "
            f"รากฝอยเสี่ยงขาดออกซีเจน")

    score = min(100, score)

    return SoilRiskProfile(
        is_basin=is_basin,
        is_subsiding=is_subsiding,
        is_unstable=is_unstable,
        is_waterlogged=is_waterlogged,
        risk_factors=factors,
        soil_risk_score=score,
    )


class CombinedAlert(TypedDict):
    alert_level:       str    # "critical" | "warning" | "watch" | "none"
    alert_title:       str    # หัวข้อแจ้งเตือน (ไทย)
    should_notify:     bool   # ควรส่งเตือนหรือไม่
    waterlog_risk:     bool   # เสี่ยงน้ำขัง
    stop_fertilizer:   bool   # ควรงดใส่ปุ๋ย
    advisories:        list[str]  # คำแนะนำสั้น ๆ
    combined_score:    int    # 0-100


def evaluate_combined_risk(
    forecast: WeatherForecast,
    soil: SoilRiskProfile,
) -> CombinedAlert:
    """
    ผนวก forecast กับ soil risk → สรุปคำเตือน
    Logic หลัก:
      - ฝนหนัก + ดินแอ่ง/ทรุด → CRITICAL (น้ำขัง + งดปุ๋ย)
      - ฝนหนัก + ดินปกติ       → WARNING  (ระวังน้ำ)
      - ฝนปานกลาง + ดินแย่     → WARNING  (ดินไม่พร้อมรับน้ำ)
      - ฝนน้อย + ดินแย่         → WATCH    (เฝ้าระวัง)
      - อื่น ๆ                  → NONE
    """
    advisories: list[str] = []
    score = 0
    waterlog = False
    stop_fert = False

    heavy_rain    = forecast["is_heavy_rain"]
    moderate_rain = forecast["total_rain_mm"] >= RAIN_MODERATE_MM
    soil_bad      = soil["soil_risk_score"] >= 30
    soil_critical = soil["soil_risk_score"] >= 50
    # SWAB pre-condition: รากตื้น อ.นายายอาม — ถ้าดินชื้นสูงอยู่แล้ว → เร่งระดับเตือน
    swab_already_wet = any(
        "รากฝอยเสี่ยง" in f or "ดินชื้นเกิน" in f
        for f in soil.get("risk_factors", [])
    )

    # ── CRITICAL: SWAB น้ำขัง + ฝนใดๆ (อ.นายายอาม: รากตื้น → เสี่ยงรากเน่าทันที) ──
    if swab_already_wet and moderate_rain:
        score = min(100, 60 + soil["soil_risk_score"] // 2)
        waterlog = True
        stop_fert = True
        advisories.append("🚨 ดินชื้นสูงอยู่แล้ว + ฝนจะตกเพิ่ม — รากฝอย 30-50 ซม. เสี่ยงขาดออกซีเจน")
        advisories.append("⛔ งดให้น้ำและใส่ปุ๋ยทันที")
        advisories.append("💧 ขุดร่องระบายน้ำลึก ≥ 50 ซม. รอบโคนต้น")
        advisories.append("🔍 ตรวจสอบรากฝอย หากน้ำตาลเข้ม/เน่า → ฉีด Metalaxyl ทันที")
        return CombinedAlert(
            alert_level="critical",
            alert_title="🔴 วิกฤต: SWAB ชื้นสูง + ฝนมา — รากตื้นเสี่ยงรากเน่า",
            should_notify=True,
            waterlog_risk=waterlog,
            stop_fertilizer=stop_fert,
            advisories=advisories,
            combined_score=score,
        )

    # ── CRITICAL: ฝนหนัก + ดินเสี่ยง ──
    if heavy_rain and soil_bad:
        score = min(100, 50 + soil["soil_risk_score"] // 2
                    + int(forecast["total_rain_mm"] / 5))
        waterlog = True
        stop_fert = True
        advisories.append("🚨 ระวังน้ำขังรากเน่า — ดินเป็นแอ่ง/ทรุด + ฝนหนัก")
        advisories.append("⛔ งดใส่ปุ๋ยชั่วคราว จนกว่าดินจะระบายน้ำได้")
        if soil["is_basin"]:
            advisories.append("💧 ขุดร่องระบายน้ำเพิ่มทันที แปลงอยู่ต่ำกว่ารอบข้าง")
        if soil["is_subsiding"]:
            advisories.append("⛰️ ดินทรุดทำให้น้ำไหลมาขังเพิ่ม ควรถมดินรอบโคนต้น")
        if soil["is_waterlogged"]:
            advisories.append("💦 ดินชื้นเกินอยู่แล้ว สูบน้ำออกก่อนฝนมา")
        advisories.append("🔍 ตรวจรากทุเรียน หากรากน้ำตาลเข้ม→อาจเริ่มเน่า")

        return CombinedAlert(
            alert_level="critical",
            alert_title="🔴 วิกฤต: น้ำขัง + ดินทรุด + ฝนหนัก",
            should_notify=True,
            waterlog_risk=waterlog,
            stop_fertilizer=stop_fert,
            advisories=advisories,
            combined_score=score,
        )

    # ── WARNING: ฝนหนักอย่างเดียว ──
    if heavy_rain:
        score = 40 + int(forecast["total_rain_mm"] / 10)
        stop_fert = True
        advisories.append("🌧️ ฝนหนักจะตก 7 วันข้างหน้า — งดใส่ปุ๋ยชั่วคราว")
        advisories.append("🔧 เปิดร่องระบายน้ำให้โล่ง")
        advisories.append("🍃 เก็บผลสุกก่อนฝนมาหากทำได้")
        return CombinedAlert(
            alert_level="warning",
            alert_title="🟠 เตือน: ฝนหนักกำลังจะมา",
            should_notify=True,
            waterlog_risk=False,
            stop_fertilizer=stop_fert,
            advisories=advisories,
            combined_score=min(100, score),
        )

    # ── WARNING: ฝนปานกลาง + ดินแย่ ──
    if moderate_rain and soil_bad:
        score = 35 + soil["soil_risk_score"] // 3
        waterlog = soil_critical
        stop_fert = soil_critical
        advisories.append("⚠️ ฝนปานกลาง แต่ดินแปลงนี้ไม่พร้อมรับน้ำ")
        if soil["is_basin"]:
            advisories.append("💧 แปลงเป็นแอ่ง ฝนไม่มากก็อาจขังได้")
        if stop_fert:
            advisories.append("⛔ งดใส่ปุ๋ยจนดินแห้งกว่านี้")
        advisories.append("🔧 ตรวจสอบร่องระบายน้ำ")
        return CombinedAlert(
            alert_level="warning",
            alert_title="🟠 เตือน: ดินเสี่ยง + ฝนจะตก",
            should_notify=True,
            waterlog_risk=waterlog,
            stop_fertilizer=stop_fert,
            advisories=advisories,
            combined_score=min(100, score),
        )

    # ── WATCH: ฝนน้อย แต่ดินแย่มาก ──
    if soil_critical:
        score = 20 + soil["soil_risk_score"] // 4
        advisories.append("👁️ ดินมีปัญหา แม้ฝนไม่มาก ให้เฝ้าระวังน้ำขัง")
        for f in soil["risk_factors"]:
            advisories.append(f"  • {f}")
        return CombinedAlert(
            alert_level="watch",
            alert_title="🟡 เฝ้าระวัง: ดินเสี่ยงสูง",
            should_notify=True,
            waterlog_risk=False,
            stop_fertilizer=False,
            advisories=advisories,
            combined_score=min(100, score),
        )

    # ── NONE ──
    return CombinedAlert(
        alert_level="none",
        alert_title="🟢 ปกติ",
        should_notify=False,
        waterlog_risk=False,
        stop_fertilizer=False,
        advisories=[],
        combined_score=0,
    )


# ═══════════════════════════════════════════════════════
# Combined Alert Messages (Text + Flex)
# ═══════════════════════════════════════════════════════

def build_combined_alert_message(
    forecast: WeatherForecast,
    soil: SoilRiskProfile,
    alert: CombinedAlert,
    lat: float, lng: float,
    plot_name: str = "แปลงของคุณ",
) -> str:
    """ข้อความแจ้งเตือนผนวกอากาศ + ดิน"""
    lines = [
        f"{alert['alert_title']}",
        f"📍 {plot_name} ({lat:.4f}, {lng:.4f})",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🌧️ พยากรณ์ฝน 7 วัน:",
        f"  ฝนรวม {forecast['total_rain_mm']} มม. | "
        f"สูงสุด {forecast['max_daily_mm']} มม./วัน | "
        f"ฝนตก {forecast['rainy_days']} วัน",
        "",
    ]

    if soil["risk_factors"]:
        lines.append("🛰️ สภาพดิน (จากดาวเทียม):")
        for f in soil["risk_factors"]:
            lines.append(f"  • {f}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📋 คำแนะนำ:")
    for adv in alert["advisories"]:
        lines.append(f"  {adv}")

    if alert["stop_fertilizer"]:
        lines.append("")
        lines.append("⛔ สรุป: งดใส่ปุ๋ยชั่วคราว จนกว่าดินจะแห้งและระบายน้ำได้ดี")

    lines.append("")
    lines.append("🛰️ GEOai v2.0 — ข้อมูลอากาศ: Open-Meteo | ดิน: Sentinel-1/2")

    return "\n".join(lines)


_LEVEL_CONFIG = {
    "critical": {"color": "#C62828", "icon": "🔴", "label": "วิกฤต"},
    "warning":  {"color": "#E65100", "icon": "🟠", "label": "เตือน"},
    "watch":    {"color": "#F9A825", "icon": "🟡", "label": "เฝ้าระวัง"},
}


def build_combined_alert_flex(
    forecast: WeatherForecast,
    soil: SoilRiskProfile,
    alert: CombinedAlert,
    lat: float, lng: float,
    plot_name: str = "แปลงของคุณ",
) -> dict:
    """Flex Message สำหรับแจ้งเตือนรวม อากาศ + ดิน"""
    cfg = _LEVEL_CONFIG.get(alert["alert_level"], _LEVEL_CONFIG["warning"])
    color = cfg["color"]

    # ── Header ──
    header = {
        "type": "box", "layout": "vertical",
        "backgroundColor": color, "paddingAll": "14px",
        "contents": [
            {
                "type": "box", "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": alert["alert_title"],
                     "color": "#FFFFFF", "weight": "bold", "size": "md",
                     "flex": 1, "wrap": True},
                    {"type": "box", "layout": "vertical",
                     "contents": [{"type": "text", "text": cfg["label"],
                                   "color": "#FFFFFF", "size": "xs",
                                   "weight": "bold"}],
                     "backgroundColor": "#00000033",
                     "paddingAll": "5px", "cornerRadius": "10px"}
                ]
            },
            {"type": "text",
             "text": f"📍 {plot_name} | ({lat:.4f}, {lng:.4f})",
             "color": "#FFFFFFCC", "size": "xs", "margin": "sm"}
        ]
    }

    # ── Body: stats ──
    rain_stats = {
        "type": "box", "layout": "horizontal", "spacing": "md",
        "contents": [
            _rain_stat("🌧️ ฝนสะสม", f"{forecast['total_rain_mm']} มม.", color),
            _rain_stat("⛈️ สูงสุด/วัน", f"{forecast['max_daily_mm']} มม.", color),
            _rain_stat("📅 วันฝนตก", f"{forecast['rainy_days']} วัน", "#555"),
        ]
    }

    # ── Body: soil risk ──
    soil_items: list[dict] = []
    if soil["risk_factors"]:
        soil_items.append({"type": "separator"})
        soil_items.append({
            "type": "text", "text": "🛰️ สภาพดิน (ดาวเทียม)",
            "weight": "bold", "size": "sm", "color": "#333"
        })
        for factor in soil["risk_factors"][:4]:
            soil_items.append({
                "type": "box", "layout": "horizontal", "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "•", "size": "sm",
                     "color": color, "flex": 0},
                    {"type": "text", "text": factor, "size": "sm",
                     "wrap": True, "color": "#444", "margin": "sm"}
                ]
            })

    # ── Body: advisories ──
    adv_items: list[dict] = [
        {"type": "separator"},
        {"type": "text", "text": "📋 คำแนะนำสำหรับทุเรียน",
         "weight": "bold", "size": "sm", "color": "#333"},
    ]
    for adv in alert["advisories"][:5]:
        clean = adv.lstrip()
        adv_items.append({
            "type": "box", "layout": "horizontal", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "•", "size": "sm",
                 "color": color, "flex": 0},
                {"type": "text", "text": clean, "size": "sm",
                 "wrap": True, "color": "#444", "margin": "sm"}
            ]
        })

    # ── Fertilizer banner (ถ้าต้องงดปุ๋ย) ──
    fert_banner: list[dict] = []
    if alert["stop_fertilizer"]:
        fert_banner.append({"type": "separator"})
        fert_banner.append({
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#FFF3E0", "cornerRadius": "8px",
            "paddingAll": "10px", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "⛔", "size": "lg", "flex": 0},
                {"type": "text",
                 "text": "งดใส่ปุ๋ยชั่วคราว\nจนกว่าดินจะแห้งและระบายน้ำได้",
                 "size": "sm", "wrap": True, "color": "#BF360C",
                 "weight": "bold", "margin": "sm"}
            ]
        })

    # ── Waterlog banner ──
    water_banner: list[dict] = []
    if alert["waterlog_risk"]:
        water_banner.append({
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#E3F2FD", "cornerRadius": "8px",
            "paddingAll": "10px", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "🚨", "size": "lg", "flex": 0},
                {"type": "text",
                 "text": "เสี่ยงน้ำขังรากเน่า\nสูบน้ำออก+ขุดร่องระบายด่วน",
                 "size": "sm", "wrap": True, "color": "#0D47A1",
                 "weight": "bold", "margin": "sm"}
            ]
        })

    body = {
        "type": "box", "layout": "vertical",
        "paddingAll": "16px", "spacing": "md",
        "contents": [
            rain_stats,
            *soil_items,
            *adv_items,
            *fert_banner,
            *water_banner,
        ]
    }

    # ── Footer ──
    footer = {
        "type": "box", "layout": "vertical",
        "paddingAll": "12px", "backgroundColor": "#F5F5F5",
        "contents": [
            {"type": "button",
             "action": {"type": "postback",
                        "label": "🔍 ตรวจสอบแปลงนี้ด้วย",
                        "data": "action=check"},
             "style": "primary", "color": "#1a7a3c", "height": "sm"},
            {"type": "text",
             "text": "🛰️ GEOai v2.0 | Open-Meteo + Sentinel-1/2",
             "size": "xxs", "color": "#AAAAAA", "align": "center",
             "margin": "sm"}
        ]
    }

    return {
        "type": "flex",
        "altText": f"{cfg['icon']} {alert['alert_title']} — {plot_name}",
        "contents": {
            "type": "bubble",
            "header": header,
            "body": body,
            "footer": footer,
        }
    }
