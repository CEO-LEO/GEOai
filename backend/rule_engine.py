"""
Rule Engine
แปลงผลการวิเคราะห์ดาวเทียมเป็นข้อความภาษาไทยที่เข้าใจง่าย
สำหรับส่งกลับเกษตรกรทาง LINE

v2: เพิ่ม land displacement, fertilizer, yield estimation
v3: เพิ่ม Root-Zone Risk (SWAB) สำหรับ อ.นายายอาม จ.จันทบุรี
    — รากทุเรียนตื้น 30–50 ซม. ต้องการสมดุลน้ำ-อากาศในดินสูง
"""


# ─────────────────────────────────────────────────
# Thresholds (ปรับตามลักษณะพื้นที่ อ.นายายอาม)
# ─────────────────────────────────────────────────
NDVI_DECLINE_THRESHOLD      = -0.10   # ลดลง 10% = น่ากังวล
NDVI_SEVERE_THRESHOLD       = -0.20   # ลดลง 20% = วิกฤต
MOISTURE_WET_THRESHOLD      = -10.0   # dB : สูงกว่านี้ = ชื้นมาก/น้ำขัง
ELEVATION_LOW_THRESHOLD     = -1.5    # เมตร: ต่ำกว่ารอบข้างเกิน 1.5 ม. = แอ่ง

# ── Root-Zone Thresholds (v3) ─────────────────────
# รากทุเรียน อ.นายายอาม: ตื้น 30–50 ซม. ไวต่อน้ำขังมาก
SWAB_WATERLOG_THRESHOLD     =  0.30   # SWAB index > นี้ = น้ำขัง (รุนแรงกว่าเดิม 0.35)
SWAB_WET_THRESHOLD          =  0.10   # เฝ้าระวัง (เตือนไวขึ้นเพราะฝนสูงตลอดปี)
SWAB_DRY_THRESHOLD          = -0.25   # แห้งเกิน (เนินเขา → แห้งเร็ว)
SWAB_DROUGHT_THRESHOLD      = -0.40   # แล้งวิกฤต
NDWI_SURFACE_WATER          =  0.10   # MNDWI > นี้ = มีน้ำบนผิวดิน/ใกล้ผิวดิน

PREDICT_BASE_YIELD_KG       = 1500    # กก./ไร่ — ผลผลิตฐานทุเรียนหมอนทองอายุ 8+ ปี


def predict_yield(ndvi_now: float, topsoil_risk_level: str,
                  elevation_diff: float) -> int:
    """
    พยากรณ์ผลผลิตทุเรียน (Mock ML Logic) กก./ไร่
    ใช้ปัจจัย NDVI, BSI/Topsoil Risk, และความลาดชันของพื้นที่
    """
    base = float(PREDICT_BASE_YIELD_KG)

    # ปัจจัย 1: สุขภาพพืช (NDVI)
    if ndvi_now > 0.6:
        base *= 1.20   # +20%

    # ปัจจัย 2: หน้าดินเปิดโล่ง (BSI สูง)
    if topsoil_risk_level == "high":
        base *= 0.85   # -15%

    # ปัจจัย 3: น้ำขัง (พื้นที่ต่ำกว่ารอบข้าง)
    if elevation_diff < ELEVATION_LOW_THRESHOLD:
        base *= 0.90   # -10%

    return round(base)


def calculate_root_rot_risk(data: dict) -> dict:
    """
    คะแนนความเสี่ยงรากเน่า 0-100 รวมทุกปัจจัย
    คืน: { "score", "level": critical/warning/watch/safe, "days_to_act" }
    """
    score = 0

    if data.get("elevation_diff", 0) < -1.5:
        score += 30
    swab_idx = data.get("swab", {}).get("swab_index", 0)
    if swab_idx > 0.30:
        score += 30
    if data.get("soil_moisture_vv", -15) > -10:
        score += 20
    if data.get("ndvi_change", 0) < -0.10:
        score += 10
    if data.get("displacement", {}).get("change_level") == "high":
        score += 10

    # Bonus จาก wetness streak (Gap 4)
    ws = data.get("wetness_streak", {})
    if ws.get("is_prolonged") and swab_idx > 0.10:
        score += 25

    score = min(100, score)

    if score >= 80:
        level, days = "critical", 1
    elif score >= 60:
        level, days = "warning", 2
    elif score >= 40:
        level, days = "watch", 5
    else:
        level, days = "safe", 14

    return {"score": score, "level": level, "days_to_act": days}


def format_message(data: dict, lat: float, lng: float) -> str:
    """สร้างข้อความรายงานภาษาไทยจาก dict ผลวิเคราะห์"""

    risks   = []
    advices = []
    status_icon = "🟢"

    ndvi_change     = data["ndvi_change"]
    moisture        = data["soil_moisture_vv"]
    elev_diff       = data["elevation_diff"]
    ndvi_now        = data["ndvi_now"]
    bsi             = data.get("bsi", 0.0)

    # v2 data (backward-compatible)
    displacement = data.get("displacement", {})
    fertilizer   = data.get("fertilizer", {})
    yield_est    = data.get("yield_estimate", {})
    land_impact  = data.get("land_impact", {})

    # ─── กฎที่ 1: เสี่ยงรากเน่า (แอ่งน้ำ + ชื้นสูง) ───
    if elev_diff < ELEVATION_LOW_THRESHOLD and moisture > MOISTURE_WET_THRESHOLD:
        risks.append("🔴 เสี่ยงสูง: พื้นที่ต่ำกว่ารอบข้างและมีความชื้นสูง น้ำอาจขังบริเวณโคนต้น")
        advices.append("ขุดร่องระบายน้ำรอบแปลง ลึกอย่างน้อย 30 ซม. ก่อนฤดูฝนเข้า")
        advices.append("ยกแปลงปลูกให้สูงขึ้น และตรวจรากต้นทุเรียนสัปดาห์นี้")
        status_icon = "🔴"

    # ─── กฎที่ 2: น้ำขังในพื้นที่ต่ำ (แม้ไม่ชื้นมากตอนนี้) ───
    elif elev_diff < ELEVATION_LOW_THRESHOLD:
        risks.append("🟠 เฝ้าระวัง: พื้นที่ต่ำกว่ารอบข้าง เสี่ยงน้ำขังในช่วงฝนตกหนัก")
        advices.append("วางแผนระบบระบายน้ำไว้ล่วงหน้า ตรวจดูร่องน้ำให้ไม่อุดตัน")
        status_icon = "🟠"

    # ─── กฎที่ 3: ต้นโทรมวิกฤต ───
    if ndvi_change < NDVI_SEVERE_THRESHOLD:
        risks.append("🔴 วิกฤต: ต้นทุเรียนโทรมมากผิดปกติ ค่าสุขภาพพืชลดลง {:.0f}% จากปีก่อน".format(abs(ndvi_change) * 100))
        advices.append("ตรวจโรครากเน่าและโรคราต่างๆ ทันที ควรปรึกษาเกษตรกรจังหวัดหรือผู้เชี่ยวชาญ")
        advices.append("ฉีดสารป้องกันโรค เช่น เมทาแลกซิล และเพิ่มธาตุแคลเซียม (Ca) + โพแทสเซียม (K)")
        status_icon = "🔴"

    # ─── กฎที่ 4: ต้นโทรมปานกลาง ───
    elif ndvi_change < NDVI_DECLINE_THRESHOLD:
        risks.append("🟠 เฝ้าระวัง: สุขภาพพืชลดลง {:.0f}% จากปีก่อน ควรตรวจสอบ".format(abs(ndvi_change) * 100))
        advices.append("เพิ่มธาตุอาหาร ไนโตรเจน (N) และแมกนีเซียม (Mg) เพื่อฟื้นฟูใบ")
        advices.append("ตรวจดูแมลงศัตรูพืช และปริมาณน้ำว่าเพียงพอหรือไม่")
        if status_icon == "🟢":
            status_icon = "🟠"

    # ─── กฎที่ 5: พื้นดินเปลี่ยนแปลง (v2) ───
    if displacement.get("change_level") == "high":
        risks.append("🔴 พื้นดินเปลี่ยนแปลงมาก: ค่าเสถียรภาพ {:.0f}% — ดินอาจทรุดหรือเคลื่อนตัว".format(
            displacement.get("surface_stability", 0) * 100))
        advices.append("ตรวจสอบรากต้นทุเรียนและฐานต้น อาจต้องพยุงลำต้นเพิ่มเติม")
        if status_icon == "🟢":
            status_icon = "🔴"
    elif displacement.get("change_level") == "medium":
        risks.append("🟠 พื้นดินมีการเปลี่ยนแปลง: ค่าเสถียรภาพ {:.0f}% — ควรเฝ้าระวัง".format(
            displacement.get("surface_stability", 0) * 100))
        if status_icon == "🟢":
            status_icon = "🟠"

    # ─── กฎที่ 6: การตรวจสอบหน้าดิน (Topsoil Analysis) ───
    if bsi > 0.2 and abs(elev_diff) > abs(ELEVATION_LOW_THRESHOLD):
        risks.append("🔴 ระวังหน้าดินพังทลาย: ดินเปิดโล่ง (BSI สูง) และอยู่ในพื้นที่ความลาดชันสูง เสี่ยงต่อการถูกชะล้าง")
        advices.append("ควรปลูกพืชคลุมดินหรือจัดทำขั้นบันได/ร่องน้ำเพื่อป้องกันหน้าดินพังทลาย")
        if status_icon in ("🟢", "🟠"):
            status_icon = "🔴"
    elif bsi > 0.2:
        risks.append("🟠 ดินเปิดโล่ง: พื้นที่หน้าดินขาดพืชคลุม (BSI สูง) อาจเสียความชื้นและแร่ธาตุหน้าดินได้ง่าย")
        advices.append("แนะนำให้ปกคลุมหน้าดินด้วยเศษใบไม้/หญ้า หรือเพาะปลูกพืชคลุมดิน")
        if status_icon == "🟢":
            status_icon = "🟠"

    # ─── กฎที่ 7: ความสมดุลน้ำ-อากาศในดิน (Root-Zone SWAB — v3) ───
    # เฉพาะสำหรับ อ.นายายอาม: รากตื้น 30-50 ซม. ไวต่อสมดุลน้ำ-อากาศมาก
    swab = data.get("swab", {})
    swab_idx  = swab.get("swab_index", 0.0)
    swab_stat = swab.get("status", "optimal")
    ndwi      = swab.get("ndwi", 0.0)
    water_pct = swab.get("soil_water_pct", 45.0)
    air_pct   = swab.get("soil_air_pct", 30.0)

    if swab_idx >= SWAB_WATERLOG_THRESHOLD:
        risks.append(
            f"🔴 น้ำขังในโซนราก: สัดส่วนน้ำในดิน {water_pct:.0f}% "
            f"อากาศในช่องดิน {air_pct:.0f}% — รากฝอยขาดออกซิเจน เสี่ยงรากเน่า!")
        advices.append("ด่วน! ขุดร่องระบายน้ำให้ลึก ≥ 50 ซม. รอบโคนต้น")
        advices.append("หยุดให้น้ำทันที ฉีด Metalaxyl/Phosphonate ป้องกันรากเน่า")
        if ndwi >= NDWI_SURFACE_WATER:
            advices.append(f"พบน้ำบนผิวดิน (MNDWI={ndwi:+.2f}) สูบน้ำออกก่อนฝนรอบใหม่")
        if status_icon != "🔴":
            status_icon = "🔴"
    elif swab_idx >= SWAB_WET_THRESHOLD:
        risks.append(
            f"🟠 เฝ้าระวังน้ำในดินสูง: {water_pct:.0f}% (เกณฑ์ปลอดภัย 35–55%) "
            f"— ช่องอากาศเหลือ {air_pct:.0f}% ควรตรวจร่องน้ำ")
        advices.append("ตรวจร่องระบายน้ำและลดความถี่การให้น้ำลง")
        advices.append("สังเกตใบร่วง/ใบเหลืองที่โคน เป็นสัญญาณรากเน่าระยะแรก")
        if status_icon == "🟢":
            status_icon = "🟠"
    elif swab_idx <= SWAB_DROUGHT_THRESHOLD:
        risks.append(
            f"🔴 แล้งวิกฤต: น้ำในดิน {water_pct:.0f}% ต่ำมาก "
            f"— รากฝอยแห้งตาย สูญเสียความสามารถดูดสารอาหาร")
        advices.append("ให้น้ำทันที! แนะนำ micro-irrigation หรือน้ำหยดทางใบ")
        advices.append("คลุมโคนต้นด้วยฟาง/ใบไม้ ลดการระเหยน้ำ")
        if status_icon != "🔴":
            status_icon = "🔴"
    elif swab_idx <= SWAB_DRY_THRESHOLD:
        risks.append(
            f"🟠 ดินแห้งเกิน: น้ำในดิน {water_pct:.0f}% (เนินเขาระบายน้ำเร็ว) "
            f"— ควรเพิ่มความถี่ให้น้ำ")
        advices.append("เพิ่มความถี่การให้น้ำ และตรวจสอบสายน้ำหยดไม่อุดตัน")
        if status_icon == "🟢":
            status_icon = "🟠"

    # ─── กฎที่ 8: สวนสมบูรณ์ ───
    if not risks:
        risks.append("🟢 สวนอยู่ในเกณฑ์ปกติ ไม่พบสัญญาณผิดปกติจากข้อมูลดาวเทียม")
        advices.append("รักษาระดับน้ำและปุ๋ยตามปกติ")
        if ndvi_now > 0.5:
            advices.append("ต้นสมบูรณ์ดี เหมาะสำหรับการบังคับดอกในช่วงนี้")

    # ─── สร้างข้อความ ───
    direction = "ต่ำกว่า" if elev_diff < 0 else "สูงกว่า"
    moisture_label = "สูง (เสี่ยงน้ำขัง)" if moisture > MOISTURE_WET_THRESHOLD else "ปกติ"
    ndvi_trend = f"{'▼' if ndvi_change < 0 else '▲'} {abs(ndvi_change * 100):.1f}%"

    # Displacement labels
    stability_pct = displacement.get("surface_stability", 1.0) * 100
    disp_label = {
        "high": "⚠️ เปลี่ยนแปลงมาก",
        "medium": "🔶 เปลี่ยนแปลงปานกลาง",
        "low": "✅ เสถียร",
    }.get(displacement.get("change_level", "low"), "✅ เสถียร")

    lines = [
        f"{status_icon} *ผลวิเคราะห์แปลงทุเรียน — GEOai v3 (นายายอาม)*",
        f"📍 ({lat:.4f}, {lng:.4f})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌿 สุขภาพพืช (NDVI):  {ndvi_now:.2f}  ({ndvi_trend} จากปีก่อน)",
        f"💧 ความชื้นดิน:       {moisture_label}",
        f"💦 น้ำ-อากาศในดิน:   น้ำ {water_pct:.0f}% / อากาศ {air_pct:.0f}% [{swab.get('status_th','—')}]",
        f"⛰️  ระดับพื้นที่:       {direction}รอบข้าง {abs(elev_diff):.1f} ม.",
        f"🌍 สภาพพื้นดิน:      {disp_label} (เสถียร {stability_pct:.0f}%)",
        f"🏜️ หน้าดิน (BSI):      {bsi:.3f} {'(ดินเปิดโล่ง)' if bsi > 0.2 else '(มีพืชคลุม)'}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        *risks,
        "",
        "📋 คำแนะนำ:",
        *[f"  • {a}" for a in advices],
    ]

    # ─── ส่วน v2: ผลกระทบจากดินเปลี่ยน ───
    if land_impact and land_impact.get("impacts"):
        lines.append("")
        lines.append("🌍 ผลกระทบจากการเปลี่ยนแปลงพื้นดิน:")
        for imp in land_impact["impacts"]:
            lines.append(f"  {imp['icon']} {imp['title']}")
            lines.append(f"    → {imp['detail']}")

    # ─── ส่วน v2: แนะนำปุ๋ย ───
    if fertilizer and fertilizer.get("level") != "critical":
        lines.append("")
        lines.append("🧪 คำแนะนำปุ๋ย (กก./ต้น/ปี):")
        lines.append(f"  สูตร N-P-K: {fertilizer.get('formula_display', '-')}")
        if fertilizer.get("ca_kg_per_tree", 0) > 0:
            lines.append(f"  แคลเซียม (Ca): {fertilizer['ca_kg_per_tree']} กก.")
        if fertilizer.get("mg_kg_per_tree", 0) > 0:
            lines.append(f"  แมกนีเซียม (Mg): {fertilizer['mg_kg_per_tree']} กก.")
        lines.append(f"  📝 {fertilizer.get('note', '')}")
    elif fertilizer and fertilizer.get("level") == "critical":
        lines.append("")
        lines.append("🧪 ปุ๋ย: ⚠️ ต้นวิกฤต! ควรตรวจดินก่อนใส่ปุ๋ย")

    # ─── ส่วน v2: ประเมินผลผลิต ───
    if yield_est:
        lines.append("")
        lines.append("📊 ประเมินผลผลิตเบื้องต้น:")
        lines.append(f"  🎯 ประมาณ {yield_est.get('estimated_kg_per_rai', '-')} กก./ไร่/ปี "
                     f"({yield_est.get('quality_label', '-')})")
        lines.append(f"  📈 ช่วง: {yield_est.get('range_low', '-')}–{yield_est.get('range_high', '-')} กก./ไร่")

    lines.append("")
    lines.append("🛰️ ข้อมูล: Sentinel-1/2 | SRTM | SWAB | GEOai v3.0 — อ.นายายอาม")

    return "\n".join(lines)
