"""
flex_messages.py — LINE Flex Message templates
ส่งผลวิเคราะห์เป็น Flex bubble ที่สวยงาม
แทน plain text เดิมสำหรับ push message

v2: เพิ่ม land displacement, fertilizer, yield estimation
"""

from datetime import datetime, timezone, timedelta
from typing import Literal

from rule_engine import compute_risk_level

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


def build_result_flex(data: dict, lat: float, lng: float, plain_text: str) -> dict:
    """
    สร้าง LINE Flex Message bubble พร้อมกราฟ meter และคำแนะนำ
    คืน dict สำหรับใส่ใน messages array ของ LINE API
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

    return {
        "type": "flex",
        "altText": f"{colors['icon']} ผลวิเคราะห์แปลงทุเรียน — {colors['label']}",
        "contents": {
            "type": "bubble",
            "size": "mega",

            # ── Header ──────────────────────────────────
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": colors["header"],
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🌿 GEOai",
                                "color": "#FFFFFF",
                                "weight": "bold",
                                "size": "lg",
                                "flex": 1
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
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "spacing": "md",
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
                                        "text": "🌿 ความสมบูรณ์พืช (NDVI)",
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
                            }
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # Stats grid
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "md",
                        "contents": [
                            _stat_box("💧 ความชื้นดิน", moist_str,
                                      "#E53935" if moisture > -10 else "#2E7D32"),
                            _stat_box("⛰️ ระดับพื้นที่", elev_str,
                                      "#E53935" if elev_diff < -1.5 else "#555555"),
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # v2: Land displacement section
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "md",
                        "contents": [
                            _stat_box("🌍 สภาพพื้นดิน", f"{disp_str} ({stability_pct}%)", disp_color),
                            _stat_box("📊 ผลผลิตประเมิน",
                                      f"{yield_est.get('estimated_kg_per_rai', '-')} กก./ไร่"
                                      if yield_est else "—",
                                      {"high": "#2E7D32", "medium": "#FB8C00",
                                       "low": "#E65100", "very_low": "#C62828"
                                       }.get(yield_est.get("quality", "medium"), "#555555")
                                      if yield_est else "#555555"),
                        ]
                    },

                    # Separator
                    {"type": "separator"},

                    # v3: Soil Water-Air Balance
                    *(_build_swab_section(data.get("swab", {}))),

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
                "paddingAll": "12px",
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


def _stat_box(label: str, value: str, color: str) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "backgroundColor": "#F5F5F5",
        "cornerRadius": "8px",
        "paddingAll": "10px",
        "contents": [
            {"type": "text", "text": label,  "size": "xxs", "color": "#888888"},
            {"type": "text", "text": value,  "size": "sm",  "color": color,
             "weight": "bold", "wrap": True, "margin": "sm"}
        ]
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
            "paddingAll": "10px", "margin": "sm",
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

    contents = [
        {
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "🌱 สภาพหน้าดิน (Topsoil)",
                 "size": "sm", "color": "#555555", "weight": "bold", "flex": 1},
                {"type": "text", "text": f"BSI {bsi_text}",
                 "size": "sm", "color": risk_color, "weight": "bold", "align": "end"},
            ]
        },
        {
            "type": "text", "text": risk_label,
            "size": "xs", "color": risk_color, "wrap": True, "margin": "sm"
        },
    ]

    if topsoil_risk == "high":
        contents.append({
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#FFEBEE", "cornerRadius": "8px",
            "paddingAll": "8px", "margin": "sm",
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
            "paddingAll": "10px", "margin": "sm",
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


def _build_swab_section(swab: dict) -> list[dict]:
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
    advice      = swab.get("advice", "")

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

    gauge = {
        "type": "box", "layout": "vertical",
        "height": "18px", "margin": "sm",
        "contents": [
            {
                "type": "box", "layout": "horizontal",
                "height": "6px", "cornerRadius": "3px", "margin": "sm",
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
                "offsetTop": "2px", "offsetStart": f"{marker_pct:.1f}%",
                "contents": [],
            },
        ],
    }

    return [
        {"type": "text",
         "text": "💧 ความชื้นในดิน",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg_color, "cornerRadius": "8px",
            "paddingAll": "10px", "margin": "sm",
            "contents": [
                {"type": "text", "text": status_icon,
                 "size": "sm", "color": txt_color, "weight": "bold"},
                gauge,
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "แห้ง", "size": "xxs",
                         "color": "#999999", "flex": 1},
                        {"type": "text", "text": "ชื้นเกิน", "size": "xxs",
                         "color": "#999999", "align": "end"},
                    ]
                },
                {"type": "text", "text": status_th,
                 "size": "xs", "color": txt_color, "wrap": True, "margin": "sm"},
                {"type": "text", "text": advice,
                 "size": "xs", "color": "#555555", "wrap": True, "margin": "sm"},
            ]
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
            "paddingAll": "12px",
            "margin": "md",
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


def build_weekly_alert_flex(data: dict, lat: float, lng: float, plain_text: str) -> dict:
    """Flex สำหรับแจ้งเตือนรายวัน — มี header สีส้มบอกว่าเป็น alert"""
    base = build_result_flex(data, lat, lng, plain_text)
    # Prepend alert banner ใน body
    base["altText"] = "⚠️ แจ้งเตือนประจำวัน — GEOai"
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
                          plain_text: str, consecutive_days: int) -> dict:
    """
    Flex สำหรับ escalation alert — เสี่ยงสูงต่อเนื่อง 2+ วัน
    เน้นสีแดง + เตือนซ้ำรุนแรงขึ้น
    """
    base = build_result_flex(data, lat, lng, plain_text)
    base["altText"] = f"🚨 แจ้งเตือนฉุกเฉิน — เสี่ยงสูงต่อเนื่อง {consecutive_days} วัน"

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
