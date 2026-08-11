"""
flex_messages.py — LINE Flex Message templates
ส่งผลวิเคราะห์เป็น Flex bubble ที่สวยงาม
แทน plain text เดิมสำหรับ push message

v2: เพิ่ม land displacement, fertilizer, yield estimation
"""

from typing import Literal


RiskLevel = Literal["high", "medium", "ok"]


def _risk_level(data: dict) -> RiskLevel:
    swab = data.get("swab", {})
    if (data["ndvi_change"] < -0.20
            or (data["elevation_diff"] < -1.5 and data["soil_moisture_vv"] > -10)
            or data.get("displacement", {}).get("change_level") == "high"
            or swab.get("severity") == "high"):
        return "high"
    if (data["ndvi_change"] < -0.10 or data["elevation_diff"] < -1.5
            or data.get("displacement", {}).get("change_level") == "medium"
            or swab.get("severity") == "medium"):
        return "medium"
    return "ok"


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
    fertilizer   = data.get("fertilizer", {})
    yield_est    = data.get("yield_estimate", {})
    land_impact  = data.get("land_impact", {})
    bsi                   = data.get("bsi", None)
    topsoil_risk          = data.get("topsoil_risk_level", "low")
    predicted_yield_kg    = data.get("predicted_yield_kg_per_rai")

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

    # แยก plain_text เป็นบรรทัดคำแนะนำ
    advice_lines = []
    in_advice = False
    for line in plain_text.split("\n"):
        if "คำแนะนำ" in line:
            in_advice = True
            continue
        if in_advice and line.strip().startswith("•"):
            advice_lines.append(line.strip().lstrip("•").strip())
        if in_advice and line.startswith("🛰️"):
            break
    if not advice_lines:
        advice_lines = ["รักษาระดับน้ำและปุ๋ยตามปกติ"]

    advice_components = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "•", "size": "sm", "color": colors["badge"], "flex": 0},
                {"type": "text", "text": a, "size": "sm", "wrap": True, "color": "#333333",
                 "margin": "sm"}
            ],
            "margin": "sm"
        }
        for a in advice_lines[:3]  # จำกัด 3 ข้อ
    ]

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

                    # v2: Fertilizer recommendation
                    *(_build_fertilizer_section(fertilizer, colors) if fertilizer else []),

                    # v2: Land impact summary
                    *(_build_impact_section(land_impact) if land_impact and land_impact.get("severity") != "low" else []),

                    # v2: Topsoil section
                    *(_build_topsoil_section(bsi, topsoil_risk)),

                    # AI yield prediction row
                    *(_build_ai_yield_section(predicted_yield_kg)),

                    # Advice
                    {
                        "type": "text",
                        "text": "📋 คำแนะนำ",
                        "size": "sm",
                        "weight": "bold",
                        "color": "#333333"
                    },
                    *advice_components,

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


def _build_ai_yield_section(predicted_yield) -> list[dict]:
    """แถว AI คาดการณ์ผลผลิต — สีเขียว/ส้ม/แดง ตามเกณฑ์"""
    if predicted_yield is None or predicted_yield < 0:
        return []
    if predicted_yield >= 1500:
        color, label = "#2E7D32", "✅ ดี"
    elif predicted_yield >= 1000:
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


def _build_swab_section(swab: dict) -> list[dict]:
    """สร้าง Flex components แสดงความสมดุลน้ำ-อากาศในดิน (SWAB v3)"""
    if not swab:
        return []

    status    = swab.get("status", "optimal")
    status_th = swab.get("status_th", "—")
    severity  = swab.get("severity", "low")
    water_pct = swab.get("soil_water_pct", 45.0)
    air_pct   = swab.get("soil_air_pct", 30.0)
    ndwi      = swab.get("ndwi", 0.0)
    advice    = swab.get("advice", "")

    bg_color   = {"high": "#FFEBEE", "medium": "#FFF3E0"}.get(severity, "#E3F2FD")
    txt_color  = {"high": "#C62828", "medium": "#E65100"}.get(severity, "#1565C0")

    # gauge — needle position (ไปยัง Flex ไม่รองรับ absolute pos) — ใช้ text icon แทน
    status_icon = {
        "waterlogged": "🟦 น้ำมากเกิน",
        "wet":         "🟦 ชื้นเกิน",
        "optimal":     "🟩 สมดุล",
        "dry":         "🟧 แห้งเกิน",
        "drought":     "🟥 แล้ง",
    }.get(status, "🟩")

    # ปริมาณวัสดุดิน (คงที่)
    solid_pct = max(0.0, 100.0 - water_pct - air_pct)

    return [
        {"type": "text",
         "text": "💧 ความสมดุลน้ำ-อากาศในดิน",
         "size": "sm", "weight": "bold", "color": "#333333"},
        {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg_color, "cornerRadius": "8px",
            "paddingAll": "10px", "margin": "sm",
            "contents": [
                # Status row
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": status_icon,
                         "size": "sm", "color": txt_color, "weight": "bold", "flex": 1},
                        {"type": "text", "text": f"NDWI {ndwi:+.3f}",
                         "size": "xs", "color": "#888888", "align": "end"},
                    ]
                },
                {"type": "text", "text": status_th,
                 "size": "xs", "color": txt_color, "wrap": True, "margin": "sm"},
                # Soil composition bar (3 segments: water | air | solid)
                {"type": "text",
                 "text": f"🔵น้ำ {water_pct:.0f}%   ⚪️อากาศ {air_pct:.0f}%   🟤วัสดุดิน {solid_pct:.0f}%",
                 "size": "xs", "color": "#666666", "margin": "sm"},
                # Advice
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
