"""
flex_messages.py — LINE Flex Message templates
ส่งผลวิเคราะห์เป็น Flex bubble ที่สวยงาม
แทน plain text เดิมสำหรับ push message

v2: เพิ่ม land displacement, fertilizer, yield estimation
"""

from datetime import datetime, timezone, timedelta
from typing import Literal

from rule_engine import compute_risk_level
from gee_analysis import swab_index_to_level

_BANGKOK_TZ = timezone(timedelta(hours=7))


RiskLevel = Literal["high", "medium", "ok"]


def _risk_level(data: dict) -> RiskLevel:
    """
    เดิมคำนวณ threshold ซ้ำเองที่นี่ (คนละชุดกับ dashboard/LIFF/scheduler และไม่รวม
    topsoil_risk_level/land_impact.severity เหมือนกัน) ตอนนี้ใช้ analyze_durian_plot()'s
    overall_risk_level ตรงๆ ถ้ามี (ข้อมูลสด) ไม่งั้น fallback ไปคำนวณด้วยฟังก์ชันกลาง
    เดียวกับที่อื่นทั้งระบบ (ดู formula-audit)
    """
    overall = data.get("overall_risk_level")
    if overall in ("high", "medium", "ok"):
        return overall
    return compute_risk_level(data)


_COLORS: dict[RiskLevel, dict] = {
    "high":   {"header": "#C62828", "badge": "#E53935", "icon": "🔴", "label": "เสี่ยงสูง"},
    "medium": {"header": "#E65100", "badge": "#FB8C00", "icon": "🟠", "label": "เฝ้าระวัง"},
    "ok":     {"header": "#1a7a3c", "badge": "#2E7D32", "icon": "🟢", "label": "ปกติ"},
}


def build_result_flex(data: dict, lat: float, lng: float, plain_text: str,
                      swab_trend: str | None = None,
                      problem_points: list[dict] | None = None,
                      plot_name: str = "") -> dict:
    """
    สร้าง LINE Flex Message bubble พร้อมกราฟ meter และคำแนะนำ
    คืน dict สำหรับใส่ใน messages array ของ LINE API

    plot_name (ถ้ามี): ผู้ใช้ขอเปลี่ยนหัวการ์ดจากคำว่า "GEOai" คงที่ เป็นชื่อแปลง
    ของเขาเอง (มีหลายแปลง อยากรู้ทันทีว่าการ์ดนี้คือแปลงไหนโดยไม่ต้องเปิดดูพิกัด)
    — fallback กลับไปที่ "GEOai" ถ้าไม่ได้ส่งมา (เช่น ผลวิเคราะห์เก่าที่ไม่มีชื่อ
    แปลงติดมาด้วย)

    swab_trend (ถ้ามี): ข้อความเทียบ "ระดับ" ความชื้นกับผลวิเคราะห์ก่อนหน้า (เช่น
    "📈 ดีขึ้น 2 ระดับจากสัปดาห์ก่อน") — คำนวณจากประวัติ ต้องมาจากภายนอก (ฟังก์ชันนี้
    ไม่มีสิทธิ์เข้าถึง DB เอง) ดู build_daily_digest_message/scheduler.py ที่เรียกใช้

    problem_points (ถ้ามี): จุดที่พบปัญหาเด่นสุดในแปลง (ดู
    gee_analysis.select_problem_points) — มีแทนที่จะโชว์บรรทัดคำแนะนำวิธีแก้ทั่วไป
    (ผู้ใช้ขอ) เฉพาะแปลงที่วาดขอบเขตไว้เท่านั้นถึงจะมีข้อมูลนี้ — plot_image_service.py
    เป็นคนคำนวณให้ (ต้องมีตารางจุดความชื้นซึ่งคำนวณตอนทำรูปแผนที่อยู่แล้ว)
    """
    level  = _risk_level(data)
    colors = _COLORS[level]

    ndvi_now    = data["ndvi_now"]
    ndvi_change = data["ndvi_change"]
    moisture    = data["soil_moisture_vv"]
    elev_diff   = data["elevation_diff"]

    # v2 data (backward-compatible)
    displacement = data.get("displacement", {})
    yield_est    = data.get("yield_estimate", {})
    land_impact  = data.get("land_impact", {})
    bsi                   = data.get("bsi", None)
    topsoil_risk          = data.get("topsoil_risk_level", "low")

    # NDVI bar width (0.0–0.9 → 0%–100%)
    ndvi_pct   = max(0, min(100, int(ndvi_now / 0.9 * 100)))
    change_str = f"{'▲' if ndvi_change >= 0 else '▼'} {abs(ndvi_change * 100):.1f}%"
    elev_str   = f"{'สูงกว่า' if elev_diff >= 0 else 'ต่ำกว่า'} {abs(elev_diff):.1f} ม."
    moist_str  = "สูง ⚠️" if moisture > -10 else "ปกติ ✅"

    # Displacement labels
    stability = displacement.get("surface_stability", 1.0)
    stability_pct = int(stability * 100)
    disp_level = displacement.get("change_level", "low")
    disp_str = {"high": "⚠️ ไม่เสถียร", "medium": "🔶 ปานกลาง", "low": "✅ เสถียร"}.get(disp_level, "✅ เสถียร")
    disp_color = {"high": "#C62828", "medium": "#E65100", "low": "#2E7D32"}.get(disp_level, "#2E7D32")

    header_title = f"🌿 {plot_name}" if plot_name else "🌿 GEOai"

    return {
        "type": "flex",
        "altText": (f"{colors['icon']} ผลวิเคราะห์ {plot_name} — {colors['label']}" if plot_name
                    else f"{colors['icon']} ผลวิเคราะห์แปลงทุเรียน — {colors['label']}"),
        "contents": {
            "type": "bubble",
            # "giga" = การ์ดกว้างเกือบเต็มจอมือถือ — ตอนอยู่ใน carousel (หลายแปลง)
            # ค่าเดิม "mega" แคบไป ทำให้เห็นขอบการ์ดถัดไปโผล่มาข้างๆ ดูล้นจอ
            "size": "giga",

            # ── Header ──────────────────────────────────
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": colors["header"],
                "paddingAll": "10px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": header_title,
                                "color": "#FFFFFF",
                                "weight": "bold",
                                "size": "lg",
                                "flex": 1,
                                "wrap": True
                            },
                            {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": f"{colors['icon']} {colors['label']}",
                                        "color": "#FFFFFF",
                                        "size": "sm",
                                        "weight": "bold"
                                    }
                                ],
                                "backgroundColor": "#00000033",
                                "paddingAll": "6px",
                                "cornerRadius": "12px"
                            }
                        ]
                    },
                    {
                        "type": "text",
                        "text": f"📍 {lat:.4f}, {lng:.4f}",
                        "color": "#FFFFFFCC",
                        "size": "xs",
                        "margin": "sm"
                    }
                ]
            },

            # ── Body ────────────────────────────────────
            # v6 (ผู้ใช้ขอ "การ์ดใหญ่เกิน อยากให้เล็กลงสักครึ่งหนึ่ง"): ลด
            # padding/spacing ทั่วทั้งการ์ดลงราวครึ่งหนึ่ง (ฟอนต์เหลือ "xxs" อยู่
            # แล้วหลายจุด ลดต่อไม่ได้ — ตัวที่ลดได้จริงคือช่องว่างรอบๆ)
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "10px",
                "spacing": "sm",
                "contents": [

                    # NDVI section
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": "🌿 ความสมบูรณ์ต้นทุเรียน",
                                        "size": "sm",
                                        "color": "#555555",
                                        "weight": "bold",
                                        "flex": 1
                                    },
                                    {
                                        "type": "text",
                                        "text": f"{ndvi_now:.2f}  {change_str}",
                                        "size": "sm",
                                        "color": colors["badge"],
                                        "weight": "bold",
                                        "align": "end"
                                    }
                                ]
                            },
                            # Progress bar
                            {
                                "type": "box",
                                "layout": "vertical",
                                "backgroundColor": "#EEEEEE",
                                "height": "8px",
                                "cornerRadius": "4px",
                                "margin": "sm",
                                "contents": [
                                    {
                                        "type": "box",
                                        "layout": "vertical",
                                        "backgroundColor": colors["badge"],
                                        "height": "8px",
                                        "cornerRadius": "4px",
                                        "width": f"{ndvi_pct}%",
                                        "contents": []
                                    }
                                ]
                            },
                            # v4 (ผู้ใช้ขอ "อธิบายค่าต่างๆ"): ดูจากความเขียวของใบผ่านดาวเทียม
                            # ไม่ใช้คำว่า NDVI เลย (ศัพท์เทคนิคที่เกษตรกรทั่วไปไม่คุ้น)
                            {
                                "type": "text",
                                "text": "ดูจากความเขียวของใบผ่านภาพดาวเทียม ค่ายิ่งสูงยิ่งสมบูรณ์ "
                                        "(▲/▼ = เทียบกับปีก่อน)",
                                "size": "xxs",
                                "color": "#999999",
                                "wrap": True,
                                "margin": "sm",
                            },
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # Stats grid
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            _stat_box("💧 ความชื้นดิน", moist_str,
                                      "#E53935" if moisture > -10 else "#2E7D32",
                                      caption="น้ำในดินตอนนี้มากไปหรือพอดี"),
                            _stat_box("⛰️ ระดับพื้นที่", elev_str,
                                      "#E53935" if elev_diff < -1.5 else "#555555",
                                      caption="เทียบพื้นที่รอบข้าง ยิ่งต่ำยิ่งเสี่ยงน้ำขัง"),
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # v2: Land displacement section
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            _stat_box("🌍 สภาพพื้นดิน", f"{disp_str} ({stability_pct}%)", disp_color,
                                      caption="ดินทรุด/เคลื่อนตัวหรือไม่ % ยิ่งสูงยิ่งมั่นคง"),
                            _stat_box("📊 ผลผลิตประเมิน",
                                      f"{yield_est.get('estimated_kg_per_rai', '-')} กก./ไร่"
                                      if yield_est else "—",
                                      {"high": "#2E7D32", "medium": "#FB8C00",
                                       "low": "#E65100", "very_low": "#C62828"
                                       }.get(yield_est.get("quality", "medium"), "#555555")
                                      if yield_est else "#555555",
                                      caption="คาดคะเนจากภาพดาวเทียมล่าสุด"),
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # v3: Soil Water-Air Balance
                    *(_build_swab_section(data.get("swab", {}), swab_trend, problem_points)),

                    # v2: Land impact summary
                    *(_build_impact_section(land_impact) if land_impact and land_impact.get("severity") != "low" else []),

                    # v2: Topsoil section
                    *(_build_topsoil_section(bsi, topsoil_risk)),

                    # เอาออกตามที่ผู้ใช้ขอ (2026-08-19): คำแนะนำปุ๋ย (_build_fertilizer_section),
                    # แถว AI คาดการณ์ผลผลิต (_build_ai_yield_section), และบล็อกคำแนะนำท้ายการ์ด
                    # (advice_components) — ฟังก์ชันยังอยู่ในไฟล์เผื่อต้องเอากลับมาใช้ ไม่ได้ลบทิ้ง
                    # แค่ไม่เรียกใช้ในการ์ดนี้แล้ว

                    # Bottom warning block (topsoil high risk)
                    *(_build_topsoil_high_warning() if topsoil_risk == "high" else []),
                ]
            },

            # ── Footer ──────────────────────────────────
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "8px",
                "backgroundColor": "#F5F5F5",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "🔍 ตรวจสอบแปลงใหม่",
                            "data": "action=check"
                        },
                        "style": "primary",
                        "color": "#1a7a3c",
                        "height": "sm"
                    },
                    {
                        "type": "text",
                        "text": "🛡️ Sentinel-1/2 | SRTM | SWAB | GEOai v3.0",
                        "size": "xxs",
                        "color": "#AAAAAA",
                        "align": "center",
                        "margin": "sm"
                    }
                ]
            }
        }
    }


def _stat_box(label: str, value: str, color: str, caption: str = "") -> dict:
    """
    caption (ใหม่ — ผู้ใช้ขอ "อธิบายค่าต่างๆ ให้เกษตรกรทั่วไปเข้าใจ"): บรรทัดเล็กๆ
    ใต้ตัวเลข อธิบายเป็นภาษาพูดว่าค่านี้คืออะไร/อ่านยังไง ไม่ต้องเดาความหมายเอง
    """
    contents = [
        {"type": "text", "text": label,  "size": "xxs", "color": "#888888"},
        {"type": "text", "text": value,  "size": "sm",  "color": color,
         "weight": "bold", "wrap": True, "margin": "sm"}
    ]
    if caption:
        contents.append(
            {"type": "text", "text": caption, "size": "xxs", "color": "#999999",
             "wrap": True, "margin": "xs"}
        )
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "backgroundColor": "#F5F5F5",
        "cornerRadius": "8px",
        "paddingAll": "7px",
        "contents": contents,
    }


def _build_fertilizer_section(fertilizer: dict, colors: dict) -> list[dict]:
    """สร้าง Flex components สำหรับแสดงคำแนะนำปุ๋ย"""
    if not fertilizer or fertilizer.get("level") == "critical":
        return [
            {"type": "box", "layout": "vertical",
             "backgroundColor": "#FFEBEE", "cornerRadius": "8px", "paddingAll": "10px",
             "margin": "sm",
             "contents": [
                 {"type": "text", "text": "🧪 ปุ๋ย: ⚠️ ต้นวิกฤต ควรตรวจดินก่อนใส่ปุ๋ย",
                  "size": "sm", "color": "#C62828", "wrap": True, "weight": "bold"}
             ]},
            {"type": "separator"},
        ]

    fert_color = {
        "maintenance": "#2E7D32",
        "recovery": "#E65100",
        "intensive": "#C62828",
    }.get(fertilizer.get("level", "maintenance"), "#555555")

    formula = fertilizer.get("formula_display", "-")
    n = fertilizer.get("n_kg_per_tree", 0)
    p = fertilizer.get("p_kg_per_tree", 0)
    k = fertilizer.get("k_kg_per_tree", 0)
    ca = fertilizer.get("ca_kg_per_tree", 0)
    mg = fertilizer.get("mg_kg_per_tree", 0)

    nutrient_texts = [f"N {n}", f"P {p}", f"K {k}"]
    if ca > 0:
        nutrient_texts.append(f"Ca {ca}")
    if mg > 0:
        nutrient_texts.append(f"Mg {mg}")

    return [
        {"type": "text", "text": "🧪 คำแนะนำปุ๋ย (กก./ต้น/ปี)",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#F5F5F5", "cornerRadius": "8px",
            "paddingAll": "10px", "margin": "sm",
            "contents": [
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": f"สูตร N-P-K: {formula}",
                         "size": "sm", "weight": "bold", "color": fert_color, "flex": 1},
                    ]
                },
                {"type": "text", "text": " | ".join(nutrient_texts),
                 "size": "xs", "color": "#666666", "margin": "sm"},
                {"type": "text", "text": fertilizer.get("note", ""),
                 "size": "xs", "color": "#888888", "wrap": True, "margin": "sm"},
            ]
        },
        {"type": "separator"},
    ]


def _build_impact_section(land_impact: dict) -> list[dict]:
    """สร้าง Flex components สำหรับแสดงผลกระทบจากดินเปลี่ยนแปลง"""
    if not land_impact or not land_impact.get("impacts"):
        return []

    severity = land_impact.get("severity", "low")
    bg_color = {"high": "#FFEBEE", "medium": "#FFF3E0"}.get(severity, "#F5F5F5")
    text_color = {"high": "#C62828", "medium": "#E65100"}.get(severity, "#555555")

    impact_items = []
    for imp in land_impact["impacts"][:2]:  # จำกัด 2 รายการ
        impact_items.append({
            "type": "box", "layout": "horizontal", "margin": "sm",
            "contents": [
                {"type": "text", "text": imp.get("icon", "🔄"), "size": "sm", "flex": 0},
                {"type": "text", "text": f"{imp['title']}: {imp['detail']}",
                 "size": "xs", "color": text_color, "wrap": True, "margin": "sm"}
            ]
        })

    return [
        {"type": "text", "text": "🌍 ผลกระทบจากพื้นดินเปลี่ยนแปลง",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg_color, "cornerRadius": "8px",
            "paddingAll": "7px", "margin": "sm",
            "contents": impact_items if impact_items else [
                {"type": "text", "text": "✅ ไม่พบผลกระทบ", "size": "sm", "color": "#2E7D32"}
            ]
        },
        {"type": "separator"},
    ]


def _build_topsoil_section(bsi, topsoil_risk: str) -> list[dict]:
    """สร้าง Flex components สำหรับแสดงสภาพหน้าดิน (Topsoil / BSI)"""
    bsi_text = f"{bsi:.3f}" if bsi is not None else "—"
    risk_color = {"high": "#C62828", "medium": "#E65100", "low": "#2E7D32"}.get(topsoil_risk, "#555555")
    bg_color   = {"high": "#FFEBEE", "medium": "#FFF3E0", "low": "#E8F5E9"}.get(topsoil_risk, "#F5F5F5")
    risk_label = {
        "high":   "⚠️ ระวัง! หน้าดินเปิดโล่ง เสี่ยงปุ๋ยและหน้าดินถูกชะล้าง",
        "medium": "🟠 หน้าดินเปิดบางส่วน ควรเฝ้าระวัง",
        "low":    "✅ หน้าดินสมบูรณ์ มีพืชคลุมดิน",
    }.get(topsoil_risk, "✅ หน้าดินสมบูรณ์ มีพืชคลุมดิน")

    # v4 (ผู้ใช้ขอ "อธิบายค่าต่างๆ ให้เกษตรกรทั่วไปเข้าใจ"): เดิมโชว์ "(Topsoil)" +
    # เลข BSI ดิบเป็นหัวข้อหลัก ซึ่งเป็นศัพท์เทคนิคล้วนไม่มีความหมายกับเกษตรกร —
    # risk_label (ข้อความภาษาไทยธรรมดา) สื่อความหมายจริงครบอยู่แล้ว ให้เด่นแทน
    # เลขดิบยังโชว์ได้แต่เป็นแค่ตัวเล็กๆ ท้ายคำอธิบาย ไม่ใช่หัวข้อหลักอีกต่อไป
    contents = [
        {
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "🌱 สภาพหน้าดิน",
                 "size": "sm", "color": "#555555", "weight": "bold", "flex": 1},
            ]
        },
        {
            "type": "text", "text": risk_label,
            "size": "xs", "color": risk_color, "wrap": True, "margin": "sm", "weight": "bold"
        },
        {
            "type": "text",
            "text": f"ดูจากพื้นที่ดินโล่งในภาพดาวเทียม (ค่าอ้างอิง {bsi_text})",
            "size": "xxs", "color": "#999999", "wrap": True, "margin": "xs"
        },
    ]

    if topsoil_risk == "high":
        contents.append({
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#FFEBEE", "cornerRadius": "8px",
            "paddingAll": "6px", "margin": "sm",
            "contents": [
                {"type": "text",
                 "text": "💡 คำแนะนำ: หน้าดินเปิดโล่งมาก แนะนำให้ปลูกพืชคลุมดิน (เช่น หญ้าแฝก) เพื่อป้องกันปุ๋ยไหลทิ้งช่วงหน้าฝน",
                 "size": "xs", "color": "#C62828", "wrap": True}
            ]
        })

    return [
        {"type": "text", "text": "🏜️ ตรวจสอบหน้าดิน",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg_color, "cornerRadius": "8px",
            "paddingAll": "7px", "margin": "sm",
            "contents": contents,
        },
        {"type": "separator"},
    ]


def _build_ai_yield_section(predicted_yield, quality: str | None = None) -> list[dict]:
    """
    แถว AI คาดการณ์ผลผลิต — สีเขียว/ส้ม/แดง ตามเกณฑ์

    เดิมตัดสีจากตัวเลข กก./ไร่ เองที่นี่ (≥1500/≥1000) คนละเกณฑ์กับ quality จริงที่
    _estimate_yield() คำนวณไว้แล้ว (≥1200/≥800) ทำให้ผลผลิตที่ระบบเรียกว่า "ดี" ขึ้น
    เป็นสีส้ม "ปานกลาง" ในการ์ดนี้ได้ (ดู formula-audit) — ใช้ quality ตรงๆ ถ้ามี
    ตัวเลขเกณฑ์ด้านล่างเหลือไว้แค่เป็น fallback สำหรับกรณีไม่มี quality ส่งมา
    """
    if predicted_yield is None or predicted_yield < 0:
        return []
    if quality is not None:
        color, label = {
            "high":     ("#2E7D32", "✅ ดี"),
            "medium":   ("#E65100", "🟠 ปานกลาง"),
            "low":      ("#C62828", "🔴 ต่ำ"),
            "very_low": ("#C62828", "🔴 ต่ำมาก"),
        }.get(quality, ("#555555", ""))
    elif predicted_yield >= 1200:
        color, label = "#2E7D32", "✅ ดี"
    elif predicted_yield >= 800:
        color, label = "#E65100", "🟠 ปานกลาง"
    else:
        color, label = "#C62828", "🔴 ต่ำ"
    return [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": "🤖 AI คาดการณ์ผลผลิต",
                    "size": "sm",
                    "color": "#555555",
                    "weight": "bold",
                    "flex": 1,
                },
                {
                    "type": "text",
                    "text": f"{predicted_yield:,} กก./ไร่  {label}",
                    "size": "sm",
                    "color": color,
                    "weight": "bold",
                    "align": "end",
                },
            ],
        },
        {"type": "separator"},
    ]


# สีโซนเกจ — ต้องตรงกับ STATUS_COLORS ใน map_image.py เป๊ะ (คนละไฟล์ คนละภาษา
# แต่ต้องให้สีที่เห็นในการ์ด LINE กับสีที่เห็นในรูปแผนที่ตรงกัน เกษตรกรจะได้ไม่งง)
_SWAB_ZONE_COLORS = ["#B71C1C", "#FB8C00", "#43A047", "#29B6F6", "#0D47A1"]

# ขอบเขตแต่ละโซนบนสเกลเกจ 0-100% แปลงมาจาก threshold จริงของ swab_index ใน
# gee_analysis._calc_swab() (>0.30 waterlogged | 0.10-0.30 wet | -0.15-0.10 optimal |
# -0.30-(-0.15) dry | <-0.30 drought) ผ่านสูตร pos=(swab_index+1)/2*100 — ใช้ flex
# weight ตามสัดส่วนจริงแทนแบ่งเท่าๆ กัน 5 ส่วน เพื่อให้ตำแหน่งหมุด "ตรง" กับสีโซน
# จริงเป๊ะ ไม่ใช่แค่ประมาณ
_SWAB_ZONE_FLEX = [70, 15, 25, 20, 70]  # 35% | 7.5% | 12.5% | 10% | 35% (x2 ให้เป็นจำนวนเต็ม)


def _swab_gauge_marker_pct(swab_index: float) -> float:
    """แปลง swab_index (-1..+1) → ตำแหน่ง % บนแถบเกจ (0=แห้งสุด, 100=ชื้นสุด)"""
    pct = (max(-1.0, min(1.0, swab_index)) + 1.0) / 2.0 * 100.0
    return max(3.0, min(97.0, pct))  # กันหมุดหลุดขอบแถบตอนค่าสุดขั้ว


def format_swab_trend(current_level: int, prev_level: int | None) -> str | None:
    """
    เทียบ "ระดับ" ความชื้นปัจจุบันกับผลวิเคราะห์ก่อนหน้า (ดู
    database.get_swab_level_days_ago) เป็นข้อความสั้นๆ ใส่ในการ์ดสรุปประจำวัน —
    ไอเดียต่อยอดจากระบบระดับ: 0=สมดุลดี ยิ่งใกล้ 0 ยิ่งดี ไม่ว่าจะติดลบ(แห้ง)หรือ
    บวก(ชื้น) เลยตัดสิน "ดีขึ้น/แย่ลง" จากระยะห่างจาก 0 ที่ลด/เพิ่ม ไม่ใช่ค่าดิบเพิ่ม/
    ลด ตรงๆ (เช่น -3 → -1 คือดีขึ้นทั้งที่ตัวเลขดิบเพิ่มขึ้น)

    คืน None ถ้าไม่มีข้อมูลเทียบ (แปลงใหม่ยังไม่ถึงสัปดาห์ ฯลฯ) — ผู้เรียกต้องเช็ค
    ก่อนใส่เข้าการ์ด
    """
    if prev_level is None:
        return None
    if current_level == prev_level:
        return "ระดับความชื้นเท่าสัปดาห์ก่อน — ยังไม่เปลี่ยนแปลง"

    delta_levels = abs(current_level - prev_level)
    arrow = f"(ระดับ {prev_level:+d} → {current_level:+d})"
    if abs(current_level) < abs(prev_level):
        return f"📈 ดีขึ้น {delta_levels} ระดับจากสัปดาห์ก่อน {arrow}"
    if abs(current_level) > abs(prev_level):
        return f"📉 แย่ลง {delta_levels} ระดับจากสัปดาห์ก่อน {arrow}"
    return f"↔️ ระดับความชื้นเปลี่ยนฝั่งจากสัปดาห์ก่อน {arrow}"


# สีป้ายกริดต่อจุดปัญหา — ใช้ชุดเดียวกับ _SWAB_ZONE_COLORS/gauge (ตรงกับสีในรูป
# แผนที่ที่ map_image.py วาดเป๊ะ กันเกษตรกรงงว่าทำไมสีในการ์ดกับรูปไม่ตรงกัน)
_PROBLEM_POINT_COLOR = {
    "drought": "#B71C1C", "dry": "#FB8C00", "optimal": "#43A047",
    "wet": "#29B6F6", "waterlogged": "#0D47A1",
}

# คำอธิบาย "ความหมาย" ของแต่ละระดับ/สถานะ — ใช้แทนคำแนะนำวิธีแก้ (swab.advice)
# สำหรับแปลงที่ไม่มี problem_points (ปักหมุดจุดเดียว ไม่มีขอบเขตให้บอกตำแหน่ง)
# ผู้ใช้ขอ "ไม่ต้องแนะนำวิธีแก้ ช่วยอธิบายว่าแต่ละระดับหมายความว่ายังไง" — บอก
# สถานการณ์/ผลกระทบที่กำลังเกิดกับดิน-ราก ไม่ใช่ขั้นตอนแก้ปัญหา
_SWAB_LEVEL_EXPLANATION = {
    "drought":     "ดินแห้งจัด เกินกว่าที่รากทุเรียนจะทนไหว รากฝอยขาดน้ำรุนแรง "
                    "ใบมีโอกาสเหี่ยวและร่วงถ้าปล่อยไว้",
    "dry":         "ดินแห้งกว่าระดับที่เหมาะสม รากเริ่มขาดน้ำ ต้นอาจแสดงอาการใบ "
                    "เหี่ยวในช่วงกลางวันได้ ยังไม่ถึงขั้นวิกฤต",
    "optimal":     "น้ำและอากาศในดินอยู่ในสัดส่วนที่เหมาะกับรากทุเรียน (รากตื้น "
                    "30-50 ซม. ไวต่อทั้งน้ำท่วมและแล้ง) ต้นดูดซึมน้ำ/อาหารได้ดี",
    "wet":         "ดินเริ่มชื้นเกินสมดุล อากาศในรูพรุนดินลดลง ถ้าฝนตกต่อเนื่อง "
                    "อาจเริ่มกระทบการหายใจของราก",
    "waterlogged": "น้ำในดินเกินความจุที่รากรับได้ อากาศในดินเหลือน้อยมาก "
                    "รากฝอยเสี่ยงขาดออกซิเจนและเน่าได้ง่ายในระยะนี้",
}


def _build_problem_points_section(problem_points: list[dict]) -> dict:
    """
    "จุดที่พบปัญหาในแปลง" — แทนที่บรรทัดคำแนะนำวิธีแก้ทั่วไปเมื่อมีข้อมูลตาราง
    จุดความชื้น (เฉพาะแปลงที่วาดขอบเขตไว้) ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้"
    — ป้าย A1/B2/... อ้างอิงตำแหน่งเดียวกับที่พิมพ์ไว้ในรูปแผนที่ (map_image.py)
    ให้เทียบรูปกับข้อความได้ทันที ไม่ต้องตีความเอง (ดู gee_analysis.select_problem_points)
    """
    rows = []
    for i, p in enumerate(problem_points):
        # color_hint (ถ้ามี) มาจาก gee_analysis.select_problem_points — จุดที่ไม่ใช่
        # เปียก/แห้งสุดจริงของแปลง ใช้สีเทียบสัมพัทธ์แทนสีตาม status ตายตัว กันสี
        # ป้ายขัดกับสีกระเบื้องในรูปแผนที่ (ดูเหตุผลเต็มที่ฟังก์ชันนั้น)
        color = p.get("color_hint") or _PROBLEM_POINT_COLOR.get(p.get("status"), "#666666")
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "sm",
            "margin": "sm" if i > 0 else "none",
            "contents": [
                {
                    "type": "box", "layout": "vertical",
                    "width": "32px", "height": "22px", "cornerRadius": "6px",
                    "backgroundColor": color, "justifyContent": "center",
                    "contents": [
                        {"type": "text", "text": p["grid_label"], "align": "center",
                         "size": "xxs", "color": "#FFFFFF", "weight": "bold"},
                    ],
                },
                {
                    "type": "box", "layout": "vertical", "flex": 1, "justifyContent": "center",
                    "contents": [
                        {"type": "text",
                         "text": f"{p['label_th']} — {p['position_desc']}",
                         "size": "xxs", "color": "#333333", "wrap": True},
                    ],
                },
            ],
        })

    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": "#F6F4EC", "cornerRadius": "8px",
        "paddingAll": "7px", "margin": "sm",
        "contents": [
            {"type": "text", "text": f"📍 จุดที่พบปัญหาในแปลง ({len(problem_points)} จุด)",
             "size": "xxs", "weight": "bold", "color": "#555555"},
        ] + rows,
    }


# ป้ายระดับหลักที่แสดงใต้แถบเกจ — ไม่แสดงทุกระดับ (-5..+5 ครบจะแน่นเกินไปในการ์ด
# แคบๆ) แสดงเฉพาะปลายสุดสองข้าง + จุดกึ่งกลาง + ขอบโซนแห้งเกิน/ชื้นเกิน (ระดับ ±2
# ใกล้เคียงขอบจริงของโซนนั้นๆ) ให้พอเห็นว่าตัวเลขไล่ระดับตามแนวไหน
_SWAB_MAJOR_LEVELS = [-5, -2, 0, 2, 5]


def _swab_level_tick_pct(level: int) -> float:
    """ระดับ (int, ดู gee_analysis.swab_index_to_level) → ตำแหน่ง % บนแถบเกจ —
    ผกผันสูตรเดียวกับที่ใช้แปลง swab_index → ระดับ เพื่อวางป้ายให้ตรงกับโซนสีจริง"""
    if level == 0:
        idx = -0.025  # กึ่งกลางโซนสมดุล (-0.15..+0.10)
    elif level > 0:
        idx = 0.10 + (level - 1) * 0.15 + 0.075
    else:
        idx = -0.15 - (-level - 1) * 0.15 - 0.075
    return _swab_gauge_marker_pct(idx)


def _build_swab_section(swab: dict, swab_trend: str | None = None,
                        problem_points: list[dict] | None = None) -> list[dict]:
    """
    สร้าง Flex components แสดงความชื้นในดิน (SWAB v3)

    v2 (ฟีดแบ็กผู้ใช้ "คนทั่วไปดูไม่เข้าใจ"): เดิมโชว์ตัวเลขดิบ (NDWI ±0.xxx,
    น้ำ/อากาศ/วัสดุดิน %) ซึ่งต้องมีความรู้พื้นฐานถึงจะตีความได้ว่าตัวเลขนั้น "ดีหรือ
    แย่" — เปลี่ยนเป็นแถบเกจแห้ง↔ชื้นแบบภาพ (เหมือนเทอร์โมมิเตอร์) พร้อมหมุดบอก
    ตำแหน่งปัจจุบันแทน ไม่ต้องตีความตัวเลขเอง แค่ดูว่าหมุดอยู่โซนสีไหนก็เข้าใจทันที
    """
    if not swab:
        return []

    status      = swab.get("status", "optimal")
    status_th   = swab.get("status_th", "—")
    severity    = swab.get("severity", "low")
    swab_index  = swab.get("swab_index", 0.0)
    # v5 (ผู้ใช้ขอ): "ไม่ต้องแนะนำวิธีแก้ อธิบายว่าแต่ละระดับหมายความว่ายังไง" —
    # เดิมตรงนี้โชว์ swab.advice (คำแนะนำวิธีแก้ เช่น "ขุดร่องระบายน้ำด่วน...")
    # ทั้งที่ไม่มี problem_points (แปลงไม่มี polygon เลยไม่มีตำแหน่งจุดให้บอก) —
    # เปลี่ยนเป็นอธิบาย "ความหมาย" ของสถานะ/ระดับนี้แทนคำแนะนำวิธีแก้ ให้สอดคล้อง
    # เจตนาเดียวกับ problem_points (บอกสถานการณ์ ไม่ใช่สอนวิธีแก้) — advice เดิม
    # ยังเก็บไว้ในข้อมูลเผื่อใช้ที่อื่น แค่ไม่โชว์ตรงนี้อีกต่อไป
    level_explanation = _SWAB_LEVEL_EXPLANATION.get(status, swab.get("advice", ""))
    # แถวเก่าที่บันทึกไว้ก่อนเพิ่มฟีเจอร์นี้ (ดู migrate ที่เกี่ยวข้อง) จะไม่มีคีย์นี้
    # ใน full_data ที่เก็บไว้ — คำนวณสดจาก swab_index ด้วยสูตรเดียวกันแทน กันพัง/
    # โชว์ "ระดับ None" กับผลวิเคราะห์เก่า (ดู gee_analysis.swab_index_to_level)
    swab_level  = swab.get("swab_level")
    if swab_level is None:
        swab_level = swab_index_to_level(swab_index)

    bg_color   = {"high": "#FFEBEE", "medium": "#FFF3E0"}.get(severity, "#E3F2FD")
    txt_color  = {"high": "#C62828", "medium": "#E65100"}.get(severity, "#1565C0")

    status_icon = {
        "waterlogged": "🟦 น้ำมากเกิน",
        "wet":         "🟦 ชื้นเกิน",
        "optimal":     "🟩 สมดุล",
        "dry":         "🟧 แห้งเกิน",
        "drought":     "🟥 แล้ง",
    }.get(status, "🟩")

    marker_pct = _swab_gauge_marker_pct(swab_index)
    level_text = f"ระดับ {swab_level:+d}" if swab_level != 0 else "ระดับ 0"

    # ป้ายเลขระดับลอยเหนือหมุด — ผู้ใช้ขอเปลี่ยนจาก % เป็น "ระดับ" ที่เทียบกันง่าย
    # กว่า (0=สมดุลดี, ติดลบ=ค่อนไปทางแห้ง, บวก=ค่อนไปทางชื้น/น้ำขัง) วางแบบเดียวกับ
    # หมุดด้านล่าง — ไม่มี transform ให้จัดกึ่งกลางเป๊ะแบบ CSS ได้ใน Flex เลยขยับซ้าย
    # เล็กน้อยเอาตามความยาวข้อความคร่าวๆ (2-3 ตัวอักษร) แทนการจัดกึ่งกลางจริง
    badge_offset = max(0.0, marker_pct - 10.0)

    gauge = {
        "type": "box", "layout": "vertical",
        "height": "32px", "margin": "sm",
        "contents": [
            {
                "type": "text", "text": level_text,
                "position": "absolute",
                "size": "xxs", "weight": "bold", "color": txt_color,
                "offsetTop": "0px", "offsetStart": f"{badge_offset:.1f}%",
            },
            {
                "type": "box", "layout": "horizontal",
                "position": "absolute",
                "width": "100%", "height": "6px", "cornerRadius": "3px",
                "offsetTop": "16px",
                "contents": [
                    {"type": "box", "layout": "vertical", "flex": w,
                     "backgroundColor": c, "contents": []}
                    for w, c in zip(_SWAB_ZONE_FLEX, _SWAB_ZONE_COLORS)
                ]
            },
            {
                "type": "box", "layout": "vertical",
                "position": "absolute",
                "width": "12px", "height": "12px", "cornerRadius": "6px",
                "backgroundColor": "#FFFFFF", "borderWidth": "2px", "borderColor": "#333333",
                "offsetTop": "13px", "offsetStart": f"{marker_pct:.1f}%",
                "contents": [],
            },
        ] + [
            {
                "type": "text",
                "text": (f"+{lvl}" if lvl > 0 else str(lvl)),
                "position": "absolute",
                "size": "xxs", "color": "#999999",
                "offsetTop": "27px",
                "offsetStart": f"{max(0.0, _swab_level_tick_pct(lvl) - 4.0):.1f}%",
            }
            for lvl in _SWAB_MAJOR_LEVELS
        ],
    }

    # ผู้ใช้ขอ "บอกจุดที่เกิดปัญหาแทนวิธีแก้" — มีเฉพาะแปลงที่วาดขอบเขตไว้ (ถึงจะมี
    # ตารางจุดความชื้นให้หาตำแหน่งจุดแย่สุดได้) แปลงปักหมุดจุดเดียวไม่มีตำแหน่งจุด
    # ให้บอก ใช้คำอธิบายความหมายของระดับ/สถานะแทน (level_explanation ข้างบน)
    advice_or_points = (
        _build_problem_points_section(problem_points) if problem_points else
        {"type": "text", "text": level_explanation,
         "size": "xs", "color": "#555555", "wrap": True, "margin": "sm"}
    )

    return [
        {"type": "text",
         "text": "💧 ความชื้นในดิน",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg_color, "cornerRadius": "8px",
            "paddingAll": "7px", "margin": "sm",
            "contents": [
                {"type": "text", "text": status_icon,
                 "size": "sm", "color": txt_color, "weight": "bold"},
                gauge,
                {"type": "text", "text": status_th,
                 "size": "xs", "color": txt_color, "wrap": True, "margin": "sm"},
                advice_or_points,
            ] + ([{"type": "text", "text": swab_trend, "size": "xxs",
                   "color": "#888888", "wrap": True, "margin": "sm"}]
                 if swab_trend else [])
        },
        {"type": "separator"},
    ]


def _build_topsoil_high_warning() -> list[dict]:
    """Block คำเตือนสีแดงด้านล่างสุดของการ์ด สำหรับ topsoil_risk == high"""
    return [
        {
            "type": "box",
            "layout": "horizontal",
            "backgroundColor": "#FFEBEE",
            "cornerRadius": "8px",
            "paddingAll": "8px",
            "margin": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️ ระวัง: หน้าดินเปิดโล่ง เสี่ยงปุ๋ยไหลทิ้ง แนะนำให้ปลูกพืชคลุมดิน",
                    "size": "sm",
                    "color": "#C62828",
                    "wrap": True,
                    "weight": "bold",
                }
            ],
        }
    ]


def build_weekly_alert_flex(data: dict, lat: float, lng: float, plain_text: str,
                            plot_name: str = "", problem_points: list[dict] | None = None) -> dict:
    """Flex สำหรับแจ้งเตือนรายวัน — มี header สีส้มบอกว่าเป็น alert"""
    base = build_result_flex(data, lat, lng, plain_text, plot_name=plot_name,
                             problem_points=problem_points)
    # Prepend alert banner ใน body
    base["altText"] = f"⚠️ แจ้งเตือนประจำวัน — {plot_name}" if plot_name else "⚠️ แจ้งเตือนประจำวัน — GEOai"
    base["contents"]["body"]["contents"].insert(0, {
        "type": "box",
        "layout": "horizontal",
        "backgroundColor": "#FFF3E0",
        "cornerRadius": "8px",
        "paddingAll": "10px",
        "contents": [
            {"type": "text", "text": "⚠️", "size": "lg", "flex": 0},
            {"type": "text", "text": "การแจ้งเตือนประจำวัน ระบบตรวจพบความผิดปกติในสวนของคุณ",
             "size": "sm", "wrap": True, "color": "#E65100", "margin": "md"}
        ]
    })
    return base


def build_escalation_flex(data: dict, lat: float, lng: float,
                          plain_text: str, consecutive_days: int,
                          plot_name: str = "", problem_points: list[dict] | None = None) -> dict:
    """
    Flex สำหรับ escalation alert — เสี่ยงสูงต่อเนื่อง 2+ วัน
    เน้นสีแดง + เตือนซ้ำรุนแรงขึ้น
    """
    base = build_result_flex(data, lat, lng, plain_text, plot_name=plot_name,
                             problem_points=problem_points)
    banner_name = f" — {plot_name}" if plot_name else ""
    base["altText"] = f"🚨 แจ้งเตือนฉุกเฉิน{banner_name} — เสี่ยงสูงต่อเนื่อง {consecutive_days} วัน"

    escalation_banner = {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#FFEBEE",
        "cornerRadius": "8px",
        "paddingAll": "12px",
        "contents": [
            {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": "🚨", "size": "xl", "flex": 0},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"เสี่ยงสูงต่อเนื่อง {consecutive_days} วัน!",
                                "size": "sm",
                                "weight": "bold",
                                "color": "#C62828",
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "ความเสี่ยงไม่ลดลง ควรตรวจสอบแปลงด้วยตนเองหรือปรึกษาผู้เชี่ยวชาญโดยด่วน",
                                "size": "xs",
                                "color": "#D32F2F",
                                "wrap": True,
                                "margin": "sm",
                            }
                        ]
                    }
                ]
            }
        ]
    }

    base["contents"]["body"]["contents"].insert(0, escalation_banner)
    # Override header color to red
    base["contents"]["header"]["backgroundColor"] = "#B71C1C"
    return base


# ─────────────────────────────────────────────────
# Daily digest — สรุปทุกแปลงทุกเช้า ไม่ว่าจะเสี่ยงหรือไม่ (ผู้ใช้เลือกเปิดเอง
# แยกจาก "แจ้งเตือนเฉพาะเสี่ยง" — ดู action=digest_on ใน webhook.py)
# ─────────────────────────────────────────────────
def build_daily_digest_message(plots: list[dict]) -> str:
    """plots = [{'name': str, 'data': dict}, ...] — ข้อความ plain text สำรอง"""
    date_str = datetime.now(_BANGKOK_TZ).strftime("%d/%m/%Y")
    lines = [f"📋 สรุปแปลงประจำวัน — {date_str}", "━━━━━━━━━━━━━━━━━━━━"]
    for p in plots:
        level = _risk_level(p["data"])
        icon = _COLORS[level]["icon"]
        status_th = p["data"].get("swab", {}).get("status_th", _COLORS[level]["label"])
        lines.append(f"{icon} {p['name']}: {status_th}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("เปิดแอปเพื่อดูรายละเอียดแต่ละแปลง")
    return "\n".join(lines)


def build_daily_digest_flex(plots: list[dict]) -> dict:
    """
    สรุปทุกแปลงของ user เป็น bubble เดียว — คนละกับ build_weekly_alert_flex ที่ส่ง
    เฉพาะตอนเสี่ยง (1 ข้อความ/1 แปลงที่เสี่ยง) อันนี้ตั้งใจให้ "สั้น สแกนตาเดียวจบ"
    เพราะส่งทุกวันไม่ว่าผลจะเป็นอย่างไร ไม่อยากให้ยาวจนเบื่อ
    """
    date_str = datetime.now(_BANGKOK_TZ).strftime("%d/%m/%Y")
    rows = []
    for p in plots:
        level = _risk_level(p["data"])
        colors = _COLORS[level]
        status_th = p["data"].get("swab", {}).get("status_th", colors["label"])
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
                {"type": "text", "text": colors["icon"], "size": "md", "flex": 0},
                {"type": "text", "text": p["name"], "size": "sm", "weight": "bold",
                 "color": "#333333", "margin": "md", "flex": 3, "wrap": True},
                {"type": "text", "text": status_th, "size": "xs", "color": colors["header"],
                 "align": "end", "flex": 4, "wrap": True},
            ]
        })

    return {
        "type": "flex",
        "altText": f"📋 สรุปแปลงประจำวัน {date_str} ({len(plots)} แปลง)",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#1a7a3c",
                "paddingAll": "14px",
                "contents": [
                    {"type": "text", "text": "📋 สรุปแปลงประจำวัน", "color": "#FFFFFF",
                     "weight": "bold", "size": "md"},
                    {"type": "text", "text": date_str, "color": "#C8E6C9", "size": "xs"},
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "14px",
                "contents": rows if rows else [
                    {"type": "text", "text": "ยังไม่มีแปลงที่ปักหมุดไว้", "size": "sm", "color": "#888888"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [{
                    "type": "text",
                    "text": "ปิดสรุปนี้ได้ที่เมนู ⚙️ ตั้งค่าการแจ้งเตือน",
                    "size": "xxs",
                    "color": "#AAAAAA",
                    "wrap": True,
                    "align": "center",
                }]
            }
        }
    }
