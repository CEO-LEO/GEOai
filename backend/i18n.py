"""
i18n.py — Multi-language support (Thai / English)
ใช้สำหรับ admin dashboard และ API responses
Frontend เกษตรกร (LINE / LIFF) ยังเป็นภาษาไทยเท่าเดิม
"""

from typing import Literal

Lang = Literal["th", "en"]

_STRINGS: dict[str, dict[Lang, str]] = {
    # ── Risk levels ──
    "risk.high":          {"th": "เสี่ยงสูง",       "en": "High Risk"},
    "risk.medium":        {"th": "เฝ้าระวัง",       "en": "Medium Risk"},
    "risk.ok":            {"th": "ปกติ",            "en": "Normal"},

    # ── Dashboard stats ──
    "stats.total":        {"th": "วิเคราะห์ทั้งหมด",  "en": "Total Analyses"},
    "stats.high_risk":    {"th": "เสี่ยงสูง",         "en": "High Risk"},
    "stats.medium_risk":  {"th": "เฝ้าระวัง",         "en": "Medium Risk"},
    "stats.ok":           {"th": "ปกติ",              "en": "Normal"},
    "stats.unique_users": {"th": "เกษตรกร",          "en": "Farmers"},
    "stats.avg_ndvi":     {"th": "NDVI เฉลี่ย",       "en": "Avg NDVI"},

    # ── Admin API messages ──
    "admin.no_data":      {"th": "ไม่มีข้อมูล",       "en": "No data available"},
    "admin.forbidden":    {"th": "คีย์ไม่ถูกต้อง",    "en": "Invalid admin API key"},
    "admin.not_found":    {"th": "ไม่พบข้อมูล",       "en": "Not found"},

    # ── Escalation alerts ──
    "escalation.title":   {"th": "⚠️ แจ้งเตือนฉุกเฉิน — ความเสี่ยงต่อเนื่อง",
                           "en": "⚠️ Escalation Alert — Persistent Risk"},
    "escalation.body":    {"th": "แปลงของคุณมีความเสี่ยงสูงติดต่อกัน {weeks} สัปดาห์",
                           "en": "Your plot has been at high risk for {weeks} consecutive weeks"},

    # ── Time labels ──
    "time.week":          {"th": "สัปดาห์",           "en": "week(s)"},
    "time.day":           {"th": "วัน",              "en": "day(s)"},

    # ── v2: Land displacement ──
    "displacement.high":    {"th": "⚠️ พื้นดินเปลี่ยนแปลงมาก",   "en": "⚠️ High Land Change"},
    "displacement.medium":  {"th": "🔶 พื้นดินเปลี่ยนปานกลาง",    "en": "🔶 Moderate Land Change"},
    "displacement.low":     {"th": "✅ พื้นดินเสถียร",             "en": "✅ Stable Land"},
    "displacement.stability": {"th": "ค่าเสถียรภาพ",               "en": "Stability Score"},

    # ── v2: Fertilizer ──
    "fertilizer.title":       {"th": "🧪 คำแนะนำปุ๋ย",            "en": "🧪 Fertilizer Advice"},
    "fertilizer.maintenance": {"th": "บำรุงปกติ",                  "en": "Maintenance"},
    "fertilizer.recovery":    {"th": "ฟื้นฟู",                     "en": "Recovery"},
    "fertilizer.intensive":   {"th": "เข้มข้น",                    "en": "Intensive"},
    "fertilizer.critical":    {"th": "วิกฤต — ตรวจดินก่อน",        "en": "Critical — Soil Test Required"},
    "fertilizer.per_tree":    {"th": "กก./ต้น/ปี",                 "en": "kg/tree/year"},

    # ── v2: Yield estimation ──
    "yield.title":     {"th": "📊 ประเมินผลผลิต",     "en": "📊 Yield Estimate"},
    "yield.high":      {"th": "ดี",                   "en": "High"},
    "yield.medium":    {"th": "ปานกลาง",              "en": "Medium"},
    "yield.low":       {"th": "ต่ำ",                  "en": "Low"},
    "yield.very_low":  {"th": "ต่ำมาก",               "en": "Very Low"},
    "yield.kg_per_rai": {"th": "กก./ไร่/ปี",           "en": "kg/rai/year"},

    # ── v2: Land impact ──
    "impact.title":    {"th": "🌍 ผลกระทบจากพื้นดินเปลี่ยน",  "en": "🌍 Land Change Impact"},
    "impact.high":     {"th": "ผลกระทบสูง",                   "en": "High Impact"},
    "impact.medium":   {"th": "ผลกระทบปานกลาง",               "en": "Medium Impact"},
    "impact.low":      {"th": "ผลกระทบน้อย",                  "en": "Low Impact"},
}


def t(key: str, lang: Lang = "th", **kwargs) -> str:
    """Translate key to the target language. Supports {placeholder} formatting."""
    entry = _STRINGS.get(key, {})
    text = entry.get(lang, entry.get("th", key))
    if kwargs:
        text = text.format(**kwargs)
    return text


def risk_label(level: str, lang: Lang = "th") -> str:
    """Map risk level string to display label."""
    return t(f"risk.{level}", lang)
