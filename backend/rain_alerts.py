"""
rain_alerts.py — เตือนฝนแบบบอกจุดเสี่ยงของแปลง (Phase 3b) — ฟังก์ชันล้วน ทดสอบได้

ตัวอย่างใน spec 6.1: "พรุ่งนี้ฝนหนัก แปลง A มีจุดเสี่ยงน้ำขัง 2 จุด แตะดูวิธีเตรียมรับมือ"

กฎ (docs/v2/line-quota-estimate.md):
  - เตือนเมื่อ **มีวันฝน ≥ HEAVY_DAY_MM ภายใน SOON_DAYS วันข้างหน้า** (~2–5 ครั้ง/ปี/พื้นที่ในราชบุรี)
    แทนกฎเดิม "ฝน 7 วัน ≥ 60 มม. + ดินเสี่ยงจากสูตร SWAB เดิม" ที่ติดเกือบทุกแปลงเกือบทุกวันในหน้าฝน
  - จุดเสี่ยง = จุดที่ชื้นกว่าส่วนอื่นของแปลงจากตารางจุดล่าสุด (เกณฑ์เดียวกับแผนที่ — plot_summary)
  - สวนยกร่อง: ไม่นับจุดชื้น (น้ำในร่อง) — บอกเฉพาะฝน
  - คำแนะนำมาจากตาราง recommendation_rules (ปัญหา heavy_rain_coming) พร้อมป้ายร่าง/ตรวจแล้ว
ค่าปริมาณฝนเป็นเกณฑ์อุตุนิยมวิทยาที่ระบบใช้อยู่เดิม (weather_alert.RAIN_DAILY_HEAVY_MM) ไม่ใช่ค่าทางการเกษตรใหม่
"""

from datetime import datetime, timedelta, timezone

import crops
import plot_summary
from weather_alert import RAIN_DAILY_HEAVY_MM

HEAVY_DAY_MM = RAIN_DAILY_HEAVY_MM      # 35 มม./วัน
VERY_HEAVY_DAY_MM = 90.0                # เกณฑ์กรมอุตุนิยมวิทยา: ฝนหนัก 35.1–90 มม./24 ชม., หนักมาก > 90 มม.
SOON_DAYS = 3
DAY_WORDS_TH = ["วันนี้", "พรุ่งนี้", "มะรืนนี้"]
ATTRIBUTION = "ข้อมูลพยากรณ์: Open-Meteo.com (CC BY 4.0)"


def heavy_rain_soon(forecast: dict) -> dict | None:
    """วันแรกที่ฝน ≥ HEAVY_DAY_MM ภายใน SOON_DAYS วัน → {"day_offset", "mm", "level"} หรือ None"""
    daily = forecast.get("daily_mm")
    if daily:
        for i, mm in enumerate(daily[:SOON_DAYS]):
            if (mm or 0) >= HEAVY_DAY_MM:
                peak = max(v or 0 for v in daily[:SOON_DAYS])
                return {"day_offset": i, "mm": round(mm, 1), "peak_mm": round(peak, 1),
                        "level": "critical" if peak >= VERY_HEAVY_DAY_MM else "warning"}
        return None
    # แหล่งที่ไม่มีรายวัน (WeatherNext) — รู้แค่ค่าสูงสุดใน 7 วัน ไม่รู้วัน
    peak = forecast.get("max_daily_mm") or 0
    if peak >= HEAVY_DAY_MM:
        return {"day_offset": None, "mm": round(peak, 1), "peak_mm": round(peak, 1),
                "level": "critical" if peak >= VERY_HEAVY_DAY_MM else "warning"}
    return None


def risk_points(snapshot: list[dict], polygon: list | None, plot_type: str | None) -> dict:
    """จุดเสี่ยงน้ำขัง = จุดที่ชื้นกว่าส่วนอื่นของแปลง (ตารางจุดล่าสุด) — สวนยกร่องไม่นับ"""
    if plot_type == "raised_bed" or not snapshot:
        return {"wet": 0, "where": "", "has_data": bool(snapshot), "raised_bed": plot_type == "raised_bed"}
    s = plot_summary.summarize(snapshot, polygon, plot_type)
    where = ""
    for line in s["lines"]:
        if "ชื้นกว่าส่วนอื่น" in line and "ส่วนใหญ่อยู่" in line:
            where = line.split("จุด", 2)[-1].strip()
    return {"wet": s["counts"].get("wet", 0), "where": where, "has_data": True, "raised_bed": False}


def headline(plot_name: str, soon: dict, risk: dict) -> str:
    when = DAY_WORDS_TH[soon["day_offset"]] if soon.get("day_offset") is not None else "ภายใน 7 วัน"
    heavy = "ฝนหนักมาก" if soon["level"] == "critical" else "ฝนหนัก"
    base = f"{when}{heavy} (~{soon['mm']:.0f} มม.) — {plot_name}"
    if risk["raised_bed"]:
        return base
    if risk["wet"]:
        return f"{base}: มีจุดที่ชื้นกว่าส่วนอื่น {risk['wet']} จุด เสี่ยงน้ำขังก่อน"
    return base


def crop_note(crop_ids: list[str] | None) -> str | None:
    """พืชที่ระบุว่าอ่อนไหวต่อน้ำขัง (config/crops.json) — ไม่เดาให้พืชที่ยังไม่มีค่า"""
    known = crops.by_id()
    sensitive = [known[c]["name_th"] for c in crop_ids or [] if known.get(c, {}).get("waterlogging_sensitivity") == "high"]
    return f"{', '.join(sensitive)} อ่อนไหวต่อน้ำขัง ควรเตรียมทางระบายน้ำก่อนฝนมา" if sensitive else None


def build_flex(plot: dict, soon: dict, risk: dict, rec: dict | None, note: str | None, liff_url: str) -> dict:
    title = headline(plot.get("name") or "แปลงของคุณ", soon, risk)
    color = "#C62828" if soon["level"] == "critical" else "#E65100"
    body = [
        {"type": "text", "text": "🌧️ เตือนฝน — IAM ROOT", "size": "xs", "color": "#78909C"},
        {"type": "text", "text": title, "weight": "bold", "size": "md", "wrap": True, "color": color},
    ]
    if risk.get("where"):
        body.append({"type": "text", "text": f"📍 จุดชื้น{risk['where']}", "size": "sm", "wrap": True, "color": "#333333"})
    if risk.get("raised_bed"):
        body.append({"type": "text", "text": "สวนยกร่อง: ไม่นับจุดชื้นจากภาพดาวเทียม (น้ำในร่อง)", "size": "xs",
                     "wrap": True, "color": "#78909C"})
    if note:
        body.append({"type": "text", "text": f"🌱 {note}", "size": "sm", "wrap": True, "color": "#333333"})
    if rec:
        body.append({"type": "text", "text": "ควรทำก่อนฝนมา", "weight": "bold", "size": "sm", "margin": "md"})
        body += [{"type": "text", "text": f"• {a}", "size": "sm", "wrap": True, "color": "#333333"}
                 for a in rec["actions"][:4]]
        body.append({"type": "text", "text": rec["status_th"], "size": "xxs", "color": "#8D4B00", "wrap": True})
    body.append({"type": "text", "text": ATTRIBUTION, "size": "xxs", "color": "#90A4AE", "margin": "md"})
    return {
        "type": "flex", "altText": f"🌧️ {title}"[:400],
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#15803d", "height": "sm",
                 "action": {"type": "uri", "label": "ดูจุดเสี่ยงบนแผนที่", "uri": liff_url}}]},
        },
    }


def build_text(plot: dict, soon: dict, risk: dict, rec: dict | None, note: str | None) -> str:
    lines = [f"🌧️ {headline(plot.get('name') or 'แปลงของคุณ', soon, risk)}"]
    if risk.get("where"):
        lines.append(f"📍 จุดชื้น{risk['where']}")
    if note:
        lines.append(f"🌱 {note}")
    if rec:
        lines.append("ควรทำก่อนฝนมา:")
        lines += [f"• {a}" for a in rec["actions"][:4]]
        lines.append(f"({rec['status_th']})")
    lines.append(ATTRIBUTION)
    return "\n".join(lines)


def weekly_cap_reached(last_sent_iso: str | None, now: datetime | None = None) -> bool:
    """ผู้ใช้ที่เลือก "สูงสุดสัปดาห์ละครั้ง" ได้เตือนฝนไปแล้วใน 7 วัน"""
    if not last_sent_iso:
        return False
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(last_sent_iso.replace("Z", "+00:00")) < timedelta(days=7)
