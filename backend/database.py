"""
Database Layer — Supabase (PostgreSQL)
เก็บประวัติการวิเคราะห์แปลงของเกษตรกรทุกราย

SQL สำหรับสร้างตารางใน Supabase SQL Editor:
─────────────────────────────────────────────
CREATE TABLE analyses (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    ndvi_now    REAL,
    ndvi_prev   REAL,
    ndvi_change REAL,
    soil_moisture_vv REAL,
    elevation   REAL,
    elevation_diff   REAL,
    message     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_analyses_user_id ON analyses(user_id);
─────────────────────────────────────────────
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from rule_engine import compute_risk_level

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# In-memory demo data (dev mode when Supabase not configured)
_demo_reports: list[dict] = []


def seed_demo_data():
    """Generate realistic v2 demo reports for dev mode dashboard"""
    import random
    random.seed(42)
    _demo_reports.clear()

    # 1g: แปลงตัวอย่างสมมติในราชบุรี หลายพืช/หลายประเภทสวน (config/demo_plots_ratchaburi.json)
    # เดิมเป็นพิกัดสวนทุเรียนจันทบุรี/ตราด — โครงการย้ายมาใช้กับเกษตรจังหวัดราชบุรี
    import json
    from pathlib import Path
    demo = json.loads((Path(__file__).resolve().parent / "config" / "demo_plots_ratchaburi.json")
                      .read_text(encoding="utf-8"))
    plots = [(p["lat"], p["lng"], f"{demo['user_id']}-{i % 3 + 1}") for i, p in enumerate(demo["plots"])]

    base_time = datetime.now(timezone.utc)

    for i, (lat, lng, uid) in enumerate(plots):
        # Vary risk profiles
        profile = random.choice(["healthy", "healthy", "medium", "medium", "risky"])

        if profile == "healthy":
            ndvi_now = round(random.uniform(0.55, 0.78), 3)
            ndvi_change = round(random.uniform(-0.05, 0.08), 3)
            stability = round(random.uniform(0.80, 0.98), 2)
            disp_level = "low"
            yield_kg = random.randint(1200, 1800)
            yield_q = random.choice(["high", "medium"])
            fert_level = "maintenance"
            impact_sev = "low"
            impact_score = random.randint(5, 20)
        elif profile == "medium":
            ndvi_now = round(random.uniform(0.35, 0.55), 3)
            ndvi_change = round(random.uniform(-0.15, -0.05), 3)
            stability = round(random.uniform(0.55, 0.80), 2)
            disp_level = random.choice(["low", "medium"])
            yield_kg = random.randint(800, 1200)
            yield_q = "medium"
            fert_level = "recovery"
            impact_sev = "medium"
            impact_score = random.randint(30, 55)
        else:  # risky
            ndvi_now = round(random.uniform(0.18, 0.35), 3)
            ndvi_change = round(random.uniform(-0.30, -0.15), 3)
            stability = round(random.uniform(0.25, 0.55), 2)
            disp_level = random.choice(["medium", "high"])
            yield_kg = random.randint(300, 800)
            yield_q = random.choice(["low", "very_low"])
            fert_level = random.choice(["intensive", "critical"])
            impact_sev = "high"
            impact_score = random.randint(60, 95)

        moisture_vv = round(random.uniform(-18, -8), 1)
        elev_diff = round(random.uniform(-3.0, 2.0), 1)
        fert_n = round(random.uniform(0.3, 1.2), 1)
        fert_p = round(random.uniform(0.1, 0.6), 1)
        fert_k = round(random.uniform(0.4, 1.5), 1)
        fert_ca = round(random.uniform(0.1, 0.4), 1)
        fert_mg = round(random.uniform(0.05, 0.2), 2)

        created = (base_time - timedelta(hours=random.randint(1, 168))).isoformat()

        _demo_reports.append({
            "id": i + 1,
            "user_id": uid,
            "lat": lat,
            "lng": lng,
            "ndvi_now": ndvi_now,
            "ndvi_prev": round(ndvi_now - ndvi_change, 3),
            "ndvi_change": ndvi_change,
            "soil_moisture_vv": moisture_vv,
            "elevation_m": round(random.uniform(20, 120), 1),
            "elevation_diff": elev_diff,
            "message": f"ผลวิเคราะห์แปลง ({lat}, {lng})",
            "created_at": created,
            # v2 fields
            "displacement_vv_change": round(random.uniform(-2.5, 1.0), 2),
            "displacement_vh_change": round(random.uniform(-1.8, 0.8), 2),
            "surface_stability": stability,
            "displacement_level": disp_level,
            "fertilizer_n": fert_n,
            "fertilizer_p": fert_p,
            "fertilizer_k": fert_k,
            "fertilizer_ca": fert_ca,
            "fertilizer_mg": fert_mg,
            "fertilizer_level": fert_level,
            "yield_estimated_kg": yield_kg,
            "yield_quality": yield_q,
            "land_impact_severity": impact_sev,
            "land_impact_score": impact_score,
        })

    _demo_reports.sort(key=lambda r: r["created_at"], reverse=True)
    logger.info(f"Seeded {len(_demo_reports)} demo reports for dev mode")

_HEADERS = lambda: {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}


async def save_analysis(user_id: str, data: dict, message: str,
                        plot_id: int | None = None) -> None:
    """บันทึกผลวิเคราะห์ลง Supabase"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase not configured — skipping DB save")
        return

    payload = {
        "user_id":          user_id,
        "lat":              data["lat"],
        "lng":              data["lng"],
        "ndvi_now":         data["ndvi_now"],
        "ndvi_prev":        data["ndvi_prev"],
        "ndvi_change":      data["ndvi_change"],
        "soil_moisture_vv": data["soil_moisture_vv"],
        "elevation":        data["elevation"],
        "elevation_diff":   data["elevation_diff"],
        "bsi_score":        data.get("bsi"),
        # บั๊กที่เจอระหว่างทำฟีเจอร์สีขอบเขตแปลงตามความเสี่ยง (LIFF map): เดิมใช้
        # topsoil_risk_level (ความเสี่ยงเฉพาะหน้าดินโล่ง, ค่าดีคือ "low") มาเก็บใน
        # คอลัมน์ risk_level ทั้งที่ทุกจุดอื่น (การ์ด LINE, LIFF history modal,
        # scheduler) คาดหวัง overall_risk_level (ความเสี่ยงรวมทั้งแปลง ค่าดีคือ
        # "ok") ตามที่ rule_engine.compute_risk_level() ระบุไว้ — ทำให้ history
        # modal ใน LIFF โชว์สีป้ายผิด (ไล่สีตามหน้าดินโล่ง ไม่ใช่ความเสี่ยงรวมจริง)
        # มาตลอด แก้ให้ตรงคอลัมน์แล้ว (ข้อมูลเก่าที่บันทึกไปแล้วยังผิดอยู่ ต้อง
        # backfill แยกถ้าต้องการ)
        "risk_level":       data.get("overall_risk_level") or compute_risk_level(data),
        "predicted_yield_kg_per_rai": data.get("predicted_yield_kg_per_rai"),
        "message":          message,
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }

    # v2 fields (backward-compatible)
    displacement = data.get("displacement")
    if displacement:
        payload["displacement_vv_change"] = displacement.get("vv_change_db")
        payload["displacement_vh_change"] = displacement.get("vh_change_db")
        payload["surface_stability"]      = displacement.get("surface_stability")
        payload["displacement_level"]     = displacement.get("change_level")

    fertilizer = data.get("fertilizer")
    if fertilizer:
        payload["fertilizer_n"]     = fertilizer.get("n_kg_per_tree")
        payload["fertilizer_p"]     = fertilizer.get("p_kg_per_tree")
        payload["fertilizer_k"]     = fertilizer.get("k_kg_per_tree")
        payload["fertilizer_ca"]    = fertilizer.get("ca_kg_per_tree")
        payload["fertilizer_mg"]    = fertilizer.get("mg_kg_per_tree")
        payload["fertilizer_level"] = fertilizer.get("level")

    yield_est = data.get("yield_estimate")
    if yield_est:
        payload["yield_estimated_kg"] = yield_est.get("estimated_kg_per_rai")
        payload["yield_quality"]      = yield_est.get("quality")

    land_impact = data.get("land_impact")
    if land_impact:
        payload["land_impact_severity"] = land_impact.get("severity")
        payload["land_impact_score"]    = land_impact.get("risk_score")
    if plot_id is not None:
        payload["plot_id"] = plot_id

    # v5: เก็บ dict ผลวิเคราะห์ทั้งก้อนแบบดิบ (nested) ไว้ด้วย — คอลัมน์ flat ด้านบน
    # ใช้ชื่อ/โครงสร้างต่างจาก data จริง (เช่น bsi_score vs bsi) และไม่มีที่เก็บ
    # swab เลย ทำให้ get_latest_report() คืนค่าที่ build_result_flex() ใช้ไม่ได้ตรงๆ
    # ต้องมี full_data ไว้ reconstruct ให้ตรงกับผลวิเคราะห์สดเป๊ะ (ดู migrate_v5.sql)
    payload["full_data"] = data

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers=_HEADERS(),
            json=payload,
        )

        # เผื่อยังไม่ได้รัน migrate_v5.sql (คอลัมน์ full_data ยังไม่มี) — ไม่ให้การบันทึก
        # ทั้งแถวพังไปเลย ลองใหม่แบบไม่มี full_data แทน (เสีย history detail แต่ยังบันทึกได้)
        if resp.status_code not in (200, 201) and "full_data" in payload:
            logger.warning(
                f"Supabase insert with full_data failed ({resp.status_code}) — "
                f"retrying without it (ยังไม่ได้รัน migrate_v5.sql?): {resp.text}"
            )
            payload.pop("full_data")
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/analyses",
                headers=_HEADERS(),
                json=payload,
            )

    if resp.status_code not in (200, 201):
        logger.error(f"Supabase insert failed: {resp.status_code} — {resp.text}")
    else:
        logger.info(f"Analysis saved for user {user_id}")


async def get_latest_report(user_id: str) -> dict | None:
    """ดึงผลวิเคราะห์ล่าสุดของ user_id (ทุกแปลงรวมกัน — เอาแค่รายการล่าสุดสุด)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "user_id":  f"eq.{user_id}",
                "order":    "created_at.desc",
                "limit":    "1",
                "select":   "*",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase query failed: {resp.status_code}")
        return None

    rows = resp.json()
    if not rows:
        return None
    return _reconstruct_full_report(rows[0])


async def get_latest_report_by_plot(plot_id: int) -> dict | None:
    """
    ดึงผลวิเคราะห์ล่าสุดของแปลง plot_id เดียว (ต่างจาก get_latest_report ที่ดึง
    ล่าสุดสุดของ user รวมทุกแปลง) — ใช้สร้างการ์ดเต็มรูปแบบต่อแปลงตอนผู้ใช้กด
    "📋 แปลงของฉัน" ในเมนู LINE (ดู webhook.py::_handle_postback action=history)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":  f"eq.{plot_id}",
                "order":    "created_at.desc",
                "limit":    "1",
                "select":   "*",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_latest_report_by_plot failed: {resp.status_code}")
        return None

    rows = resp.json()
    if not rows:
        return None
    return _reconstruct_full_report(rows[0])


async def get_plot_analysis_since(plot_id: int, since_iso: str) -> dict | None:
    """
    ดึงผลวิเคราะห์ของแปลงนี้ที่คำนวณไปแล้วตั้งแต่ since_iso (ISO timestamp, UTC) —
    ใช้ตัดสินใจว่าต้องยิง GEE ใหม่มั้ย หรือมีผลที่คำนวณไปแล้ว "วันนี้" (ผู้เรียกส่ง
    เวลาเที่ยงคืนตามเขตเวลาไทยของวันนี้มาเป็น since_iso) ให้ใช้ซ้ำได้เลย — ผู้ใช้ขอ
    "เริ่มคำนวณตั้งแต่ตี 5 แล้วค่อยส่งตอน 6-7 โมง" (ดู scheduler.py::daily_scan_job)
    คืน None ถ้ายังไม่เคยคำนวณตั้งแต่เวลานั้น (ต้องคำนวณใหม่)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":     f"eq.{plot_id}",
                "created_at":  f"gte.{since_iso}",
                "order":       "created_at.desc",
                "limit":       "1",
                "select":      "*",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_plot_analysis_since failed: {resp.status_code}")
        return None

    rows = resp.json()
    if not rows:
        return None
    return _reconstruct_full_report(rows[0])


async def get_latest_risk_by_plots(plot_ids: list[int]) -> dict[int, dict]:
    """
    ดึง risk_level + เวลาที่วิเคราะห์ล่าสุดของหลายแปลงพร้อมกันในคำขอเดียว (ไม่ใช่
    วนยิงทีละแปลง) — ใช้เติมข้อมูลให้ /plots/{user_id} สำหรับ:
      - ป้าย "อัปเดตล่าสุด X ชม.ที่แล้ว" บนการ์ดแปลงใน LIFF
      - ย้อมสีขอบเขตแปลงบนแผนที่ตามระดับเสี่ยง (แดง/ส้ม/เขียว)
    คืน {plot_id: {"risk_level": ..., "analyzed_at": ...}} เฉพาะแปลงที่มีผลวิเคราะห์
    แล้วเท่านั้น (แปลงที่ยังไม่เคยวิเคราะห์จะไม่มี key นี้)
    """
    if not plot_ids or not SUPABASE_URL or not SUPABASE_KEY:
        return {}

    ids_csv = ",".join(str(pid) for pid in plot_ids)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id": f"in.({ids_csv})",
                "order":   "created_at.desc",
                "select":  "plot_id,risk_level,created_at",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_latest_risk_by_plots failed: {resp.status_code}")
        return {}

    result: dict[int, dict] = {}
    for row in resp.json():
        pid = row.get("plot_id")
        # เรียง created_at.desc มาแล้ว — เจอ plot_id ครั้งแรกคือแถวล่าสุดของแปลงนั้น
        if pid is not None and pid not in result:
            result[pid] = {"risk_level": row.get("risk_level"), "analyzed_at": row.get("created_at")}
    return result


def _reconstruct_full_report(row: dict) -> dict:
    """
    build_result_flex()/format_message() ต้องการ dict รูปแบบ nested เหมือนผลวิเคราะห์สด
    จาก analyze_durian_plot() (เช่น data["bsi"], data["swab"], data["yield_estimate"])
    แต่แถวที่เก็บใน Supabase เป็นคอลัมน์ flat ชื่อไม่ตรงกัน (เช่น bsi_score) และไม่มี
    ที่เก็บ swab เลย — ถ้ามี full_data (v5+, ดู migrate_v5.sql) ให้ใช้ก้อนนั้นแทน
    (เก็บ nested ตรงกับของสดทุกอย่างอยู่แล้ว) ทับคอลัมน์ flat ที่มาพร้อมกัน
    ส่วนแถวเก่าก่อน migrate_v5 (full_data เป็น null) จะได้แค่คอลัมน์ flat เหมือนเดิม
    """
    full_data = row.get("full_data")
    if not full_data:
        return row
    return {**row, **full_data}


async def get_all_reports(limit: int = 100) -> list[dict]:
    """ดึงผลวิเคราะห์ทั้งหมด (สำหรับ admin dashboard)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        # Dev mode: return in-memory demo data
        return _demo_reports[:limit]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "order":  "created_at.desc",
                "limit":  str(limit),
                "select": "*",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase query failed: {resp.status_code}")
        return []

    return resp.json()


async def upsert_user(user_id: str, display_name: str = "") -> None:
    """บันทึกหรืออัพเดตข้อมูลเกษตรกร (upsert by user_id)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    payload = {
        "user_id":      user_id,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if display_name:
        payload["display_name"] = display_name

    headers = {
        **_HEADERS(),
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # ผู้ใช้ใหม่: สรุปแปลงประจำวัน "ปิด" เป็นค่าเริ่มต้น ให้ผู้ใช้เลือกเปิดเอง (ตัดสินใจ
        # 2026-10-15 — ส่งทุกแปลงทุกเช้ากินโควตาข้อความ LINE OA ตามจำนวนผู้ใช้ × วัน)
        # ignore-duplicates = insert เฉพาะตอนยังไม่มีแถว ไม่แตะค่าที่ผู้ใช้เดิมเลือกไว้แล้ว
        # (คอลัมน์ DEFAULT ใน DB ยังเป็น TRUE จาก migrate_v11 — ดู migrate_v12.sql)
        new_user_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"user_id": user_id, "notify_daily_digest": False},
        )
        if new_user_resp.status_code not in (200, 201):
            logger.error(f"Supabase insert new user failed: {new_user_resp.status_code} — {new_user_resp.text}")

        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers=headers,
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error(f"Supabase upsert user failed: {resp.status_code} — {resp.text}")


async def get_user(user_id: str) -> dict | None:
    """
    ดึงข้อมูล user แถวเดียว (notify_hour/notify_daily_digest/notify_weekly ฯลฯ) —
    ใช้โชว์ "ตอนนี้ของคุณ: ..." สรุปการตั้งค่าจริงบนการ์ด ⚙️ ตั้งค่าการแจ้งเตือน
    (ผู้ใช้ขอ — เดิมการ์ดนั้นมีแต่คำอธิบายทั่วไป ไม่บอกค่าที่ตั้งไว้จริง)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": ""},
            params={"user_id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_user failed: {resp.status_code}")
        return None

    rows = resp.json()
    return rows[0] if rows else None


async def find_nearby_plot(user_id: str, lat: float, lng: float,
                          tolerance: float = 0.001) -> int | None:
    """
    ตรวจว่า user มีแปลงอยู่แล้วใกล้ ±0.001° (~111 m) หรือไม่
    คืน plot_id ถ้าเจอ, None ถ้าไม่เจอ
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    lat_lo, lat_hi = lat - tolerance, lat + tolerance
    lng_lo, lng_hi = lng - tolerance, lng + tolerance

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": ""},
            params=[
                ("user_id",   f"eq.{user_id}"),
                ("is_active", "eq.true"),
                ("lat",       f"gte.{lat_lo}"),
                ("lat",       f"lte.{lat_hi}"),
                ("lng",       f"gte.{lng_lo}"),
                ("lng",       f"lte.{lng_hi}"),
                ("select",    "id,lat,lng"),
                ("limit",     "1"),
            ],
        )

    if resp.status_code != 200:
        return None

    rows = resp.json()
    # Double-check precision in Python (Supabase params can't combine gte+lte on same col)
    for row in rows:
        if abs(row["lat"] - lat) <= tolerance and abs(row["lng"] - lng) <= tolerance:
            return row["id"]
    return None


async def save_plot(user_id: str, lat: float, lng: float,
                    name: str = "แปลงที่ 1", area_rai: float | None = None,
                    polygon: list | None = None, plot_type: str | None = None,
                    extra: dict | None = None) -> int | None:
    """บันทึกแปลงใหม่ของ user คืน plot_id — ตรวจซ้ำก่อนสร้าง"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    # ── ตรวจซ้ำ: ถ้ามีแปลงใกล้กันภายใน ~111 m (0.001°) คืน id เดิม ──
    existing = await find_nearby_plot(user_id, lat, lng)
    if existing:
        logger.info(f"Duplicate plot detected for user {user_id} → returning existing plot {existing}")
        return existing

    payload: dict = {"user_id": user_id, "lat": lat, "lng": lng, "name": name}
    if area_rai is not None:
        payload["area_rai"] = area_rai
    if polygon is not None:
        payload["polygon"] = polygon
    if plot_type is not None:
        payload["plot_type"] = plot_type
    if extra:   # 2a: created_by / owner_label (คอลัมน์จาก migrate_v16)
        payload.update(extra)

    headers = {**_HEADERS(), "Prefer": "return=representation"}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers=headers,
            json=payload,
        )

    if resp.status_code in (200, 201):
        rows = resp.json()
        return rows[0]["id"] if rows else None
    logger.error(f"Supabase save_plot failed: {resp.status_code} — {resp.text}")
    return None


async def set_plot_thumbnail(plot_id: int, url: str) -> bool:
    """
    บันทึก URL รูปย่อดาวเทียมของแปลง (ดู make_list_thumbnail ใน map_image.py) —
    เรียกจาก plot_image_service.py หลังอัปโหลดรูปย่อขึ้น Supabase Storage แล้ว
    เพื่อให้หน้า "แปลงของฉัน" (get_user_plots คืนมาพร้อม select "*" อยู่แล้ว) ใช้
    รูปนี้แยกความแตกต่างระหว่างแปลงในลิสต์ได้ โดยไม่ต้องรอ user เปิดแปลงนั้นก่อน
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"id": f"eq.{plot_id}"},
            json={"thumbnail_url": url},
        )
    if resp.status_code not in (200, 204):
        logger.error(f"Supabase set_plot_thumbnail failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def get_user_plots(user_id: str) -> list[dict]:
    """
    ดึงแปลงที่ใช้งานอยู่ของ user พร้อม crop_ids (จาก plot_crops — 1c)
    แปลงที่ถูกรวมประวัติเข้าแปลงอื่นแล้ว (is_active = false) ไม่แสดง
    ถ้ายังไม่ได้รัน migrate_v14 (ไม่มีตาราง plot_crops) จะ fallback เป็น select * แบบเดิม
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    base = {"user_id": f"eq.{user_id}", "is_active": "not.is.false", "order": "created_at.asc"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": ""},
            params={**base, "select": "*,plot_crops(crop_id)"},
        )
        if resp.status_code == 400:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/plots",
                headers={**_HEADERS(), "Prefer": ""},
                params={**base, "select": "*"},
            )

    if resp.status_code != 200:
        logger.error(f"Supabase get_user_plots failed: {resp.status_code}")
        return []
    plots = resp.json()
    for p in plots:
        p["crop_ids"] = [c["crop_id"] for c in p.pop("plot_crops", None) or []]
    return plots


async def update_plot(plot_id: int, fields: dict) -> bool:
    """แก้ข้อมูลแปลง (ชื่อ/ประเภทสวน/ขอบเขต/boundary_check) — ผู้เรียกต้องตรวจเจ้าของแปลงก่อน"""
    if not SUPABASE_URL or not SUPABASE_KEY or not fields:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"id": f"eq.{plot_id}"},
            json=fields,
        )
    if resp.status_code not in (200, 204):
        logger.error(f"Supabase update_plot failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def get_plot_crop_ids(plot_id: int) -> list[str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/plot_crops",
            headers={**_HEADERS(), "Prefer": ""},
            params={"plot_id": f"eq.{plot_id}", "select": "crop_id", "order": "created_at.asc"},
        )
    if resp.status_code != 200:
        return []
    return [r["crop_id"] for r in resp.json()]


async def set_plot_crops(plot_id: int, crop_ids: list[str], source: str = "user") -> bool:
    """แทนที่ชนิดพืชของแปลงทั้งชุด (ลบของเดิม แล้วใส่ชุดใหม่)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{SUPABASE_URL}/rest/v1/plot_crops",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"plot_id": f"eq.{plot_id}"},
        )
        if resp.status_code not in (200, 204):
            logger.error(f"Supabase set_plot_crops (delete) failed: {resp.status_code} — {resp.text}")
            return False
        if not crop_ids:
            return True
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/plot_crops",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            json=[{"plot_id": plot_id, "crop_id": c, "source": source} for c in crop_ids],
        )
    if resp.status_code not in (200, 201, 204):
        logger.error(f"Supabase set_plot_crops failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def archive_plot_boundary(plot: dict) -> bool:
    """เก็บขอบเขตเดิมไว้ใน plot_boundary_history ก่อนแก้ — ประวัติวิเคราะห์ยังผูกกับ plot_id เดิม"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/plot_boundary_history",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            json={"plot_id": plot["id"], "lat": plot.get("lat"), "lng": plot.get("lng"),
                  "area_rai": plot.get("area_rai"), "polygon": plot.get("polygon")},
        )
    if resp.status_code not in (200, 201, 204):
        logger.error(f"Supabase archive_plot_boundary failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def merge_plot_history(user_id: str, source_plot_id: int, target_plot_id: int) -> bool:
    """
    ย้ายประวัติวิเคราะห์ + ตารางจุดความชื้นจากแปลงซ้ำ (source) ไปแปลงที่ใช้ต่อ (target)
    แปลงต้นทางถูกปิดใช้งาน (is_active = false) ไม่ลบ · เก็บ merged_from_plot_id ไว้ย้อนกลับได้
    (ดู supabase/rollback_v14.sql) — ผู้เรียกต้องตรวจว่าทั้งสองแปลงเป็นของ user_id ก่อน
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    hdr = {**_HEADERS(), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(timeout=60) as client:
        for table in ("analyses", "grid_snapshots"):
            # แถวที่ยังไม่เคยถูกรวม → จำแปลงต้นทาง · แถวที่เคยรวมมาแล้ว → คงต้นทางเดิมไว้
            for cond, body in (("is.null", {"plot_id": target_plot_id, "merged_from_plot_id": source_plot_id}),
                               ("not.is.null", {"plot_id": target_plot_id})):
                resp = await client.patch(
                    f"{SUPABASE_URL}/rest/v1/{table}", headers=hdr,
                    params={"plot_id": f"eq.{source_plot_id}", "merged_from_plot_id": cond},
                    json=body,
                )
                if resp.status_code not in (200, 204):
                    logger.error(f"Supabase merge {table} failed: {resp.status_code} — {resp.text}")
                    return False
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/plots", headers=hdr,
            params={"id": f"eq.{source_plot_id}", "user_id": f"eq.{user_id}"},
            json={"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        if resp.status_code not in (200, 204):
            logger.error(f"Supabase merge deactivate failed: {resp.status_code} — {resp.text}")
            return False
        await client.post(
            f"{SUPABASE_URL}/rest/v1/plot_merges", headers=hdr,
            json={"user_id": user_id, "source_plot_id": source_plot_id, "target_plot_id": target_plot_id},
        )
    return True


async def get_plot_history(plot_id: int, limit: int = 20) -> list[dict]:
    """ดึงประวัติการวิเคราะห์ทั้งหมดของแปลง plot_id"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id": f"eq.{plot_id}",
                "order":   "created_at.desc",
                "limit":   str(limit),
                "select":  "id,lat,lng,ndvi_now,ndvi_prev,ndvi_change,"
                           "soil_moisture_vv,elevation,elevation_diff,"
                           "displacement_vv_change,surface_stability,displacement_level,"
                           "fertilizer_n,fertilizer_p,fertilizer_k,"
                           "fertilizer_ca,fertilizer_mg,fertilizer_level,"
                           "yield_estimated_kg,yield_quality,"
                           "land_impact_severity,land_impact_score,risk_level,"
                           "message,created_at,full_data",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_plot_history failed: {resp.status_code}")
        return []
    return resp.json()


async def delete_plot(user_id: str, plot_id: int) -> bool:
    """ลบแปลง (ตรวจสอบ user_id เจ้าของด้วย)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={
                "id":      f"eq.{plot_id}",
                "user_id": f"eq.{user_id}",
            },
        )

    ok = resp.status_code in (200, 204)
    if not ok:
        logger.error(f"Supabase delete_plot failed: {resp.status_code}")
    return ok


async def set_notify(user_id: str, enabled: bool) -> bool:
    """เปิด/ปิดการแจ้งเตือนรายสัปดาห์ของ user"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json={"notify_weekly": enabled},
        )

    ok = resp.status_code in (200, 204)
    if not ok:
        logger.error(f"Supabase set_notify failed: {resp.status_code}")
    return ok


PAGE_SIZE = 1000   # Supabase/PostgREST คืนไม่เกิน 1,000 แถวต่อคำขอโดยค่าเริ่มต้น


async def _fetch_all(table: str, params: dict) -> list[dict] | None:
    """
    ดึงทุกแถวที่ตรงเงื่อนไขแบบแบ่งหน้า — เดิมฟังก์ชันรายชื่อผู้ใช้ยิงคำขอเดียว ถ้าเกิน
    1,000 แถว Supabase ตัดทิ้งเงียบๆ (ผู้ใช้หลุดจากการแจ้งเตือนโดยไม่มี error)
    คืน None ถ้าคำขอใดล้มเหลว ให้ผู้เรียกตัดสินใจเอง
    """
    rows: list[dict] = []
    offset = 0
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers={**_HEADERS(), "Prefer": ""},
                params={**params, "limit": str(PAGE_SIZE), "offset": str(offset)},
            )
            if resp.status_code != 200:
                logger.error(f"Supabase _fetch_all({table}) failed: {resp.status_code} — {resp.text[:200]}")
                return None
            page = resp.json()
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE


async def get_all_user_ids() -> list[str]:
    """
    รายชื่อผู้ใช้ทั้งหมด — ใช้แทนการหาผู้ใช้จาก "รายงานล่าสุด 1,000 แถว" ใน
    daily_scan_job/rain_alert_job (เดิมเมื่อมีแปลงเกินราว 1,000 แปลง ผู้ใช้บางคนจะ
    หลุดจากการสแกนถาวรโดยไม่มี error)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return sorted({r["user_id"] for r in _demo_reports})
    rows = await _fetch_all("users", {"select": "user_id", "order": "user_id.asc"})
    return [r["user_id"] for r in rows or []]


async def get_active_plots() -> list[dict]:
    """แปลงที่ใช้งานอยู่ทั้งหมด (ทุกผู้ใช้) — ใช้ตรวจคุณภาพขอบเขตย้อนหลัง (admin)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    rows = await _fetch_all("plots", {"is_active": "not.is.false", "order": "id.asc",
                                      "select": "*,plot_crops(crop_id)"})
    for p in rows or []:
        p["crop_ids"] = [c["crop_id"] for c in p.pop("plot_crops", None) or []]
    return rows or []


# ── บทบาท/พื้นที่/รหัสเชิญ/audit/events (Phase 2a — migrate_v16) ─────────────
_FARMER = {"role": "farmer", "areas": [], "share_with_officers": False}


async def get_user_access(user_id: str) -> dict:
    """{"role", "areas", "share_with_officers"} — ยังไม่ได้รัน v16 / ไม่มีผู้ใช้ = farmer (ปลอดภัยไว้ก่อน)"""
    if not SUPABASE_URL or not SUPABASE_KEY or not user_id:
        return dict(_FARMER)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users", headers={**_HEADERS(), "Prefer": ""},
            params={"user_id": f"eq.{user_id}", "select": "role,share_with_officers,user_areas(area_code)", "limit": "1"},
        )
    if resp.status_code != 200 or not resp.json():
        return dict(_FARMER)
    r = resp.json()[0]
    return {"role": r.get("role") or "farmer", "share_with_officers": bool(r.get("share_with_officers")),
            "areas": [a["area_code"] for a in r.get("user_areas") or []]}


async def get_share_flags(user_ids: list[str]) -> dict[str, bool]:
    """เจ้าของแปลงคนไหนยินยอมแบ่งปันกับเจ้าหน้าที่"""
    ids = sorted({u for u in user_ids if u})
    if not ids or not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    rows = await _fetch_all("users", {"user_id": f"in.({','.join(ids)})", "select": "user_id,share_with_officers"})
    return {r["user_id"]: bool(r.get("share_with_officers")) for r in rows or []}


async def get_plots_for_officer(user_id: str, area_codes: list[str]) -> list[dict]:
    """แปลงที่เจ้าหน้าที่วาดเอง + แปลงในอำเภอที่รับผิดชอบ (ยังไม่กรองความยินยอม — ผู้เรียกกรอง)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    sel = "*,plot_crops(crop_id)"
    filters = [f"created_by.eq.{user_id}"] + ([f"area_code.in.({','.join(area_codes)})"] if area_codes else [])
    rows = await _fetch_all("plots", {"is_active": "not.is.false", "or": f"({','.join(filters)})",
                                      "order": "area_code.asc,created_at.asc", "select": sel})
    for p in rows or []:
        p["crop_ids"] = [c["crop_id"] for c in p.pop("plot_crops", None) or []]
    return rows or []


async def list_users_admin() -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    users = await _fetch_all("users", {"select": "user_id,display_name,role,share_with_officers,created_at,last_seen_at,"
                                                 "user_areas(area_code)", "order": "created_at.asc"}) or []
    plots = await _fetch_all("plots", {"is_active": "not.is.false", "select": "user_id"}) or []
    counts: dict[str, int] = {}
    for p in plots:
        counts[p["user_id"]] = counts.get(p["user_id"], 0) + 1
    for u in users:
        u["areas"] = [a["area_code"] for a in u.pop("user_areas", None) or []]
        u["plot_count"] = counts.get(u["user_id"], 0)
    return users


async def set_user_role(user_id: str, role: str, areas: list[str]) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    hdr = {**_HEADERS(), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(f"{SUPABASE_URL}/rest/v1/users", headers=hdr,
                                  params={"user_id": f"eq.{user_id}"}, json={"role": role})
        if resp.status_code not in (200, 204):
            logger.error(f"Supabase set_user_role failed: {resp.status_code} — {resp.text}")
            return False
        await client.delete(f"{SUPABASE_URL}/rest/v1/user_areas", headers=hdr, params={"user_id": f"eq.{user_id}"})
        if areas:
            resp = await client.post(f"{SUPABASE_URL}/rest/v1/user_areas", headers=hdr,
                                     json=[{"user_id": user_id, "area_code": a} for a in areas])
            if resp.status_code not in (200, 201, 204):
                logger.error(f"Supabase set user_areas failed: {resp.status_code} — {resp.text}")
                return False
    return True


async def set_share_with_officers(user_id: str, enabled: bool) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(f"{SUPABASE_URL}/rest/v1/users", headers={**_HEADERS(), "Prefer": "return=minimal"},
                                  params={"user_id": f"eq.{user_id}"}, json={"share_with_officers": enabled})
    return resp.status_code in (200, 204)


async def create_invite(row: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{SUPABASE_URL}/rest/v1/invites",
                                 headers={**_HEADERS(), "Prefer": "return=minimal"}, json=row)
    if resp.status_code not in (200, 201, 204):
        logger.error(f"Supabase create_invite failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def get_invite(code: str) -> dict | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{SUPABASE_URL}/rest/v1/invites", headers={**_HEADERS(), "Prefer": ""},
                                params={"code": f"eq.{code}", "select": "*", "limit": "1"})
    return resp.json()[0] if resp.status_code == 200 and resp.json() else None


async def record_invite_use(code: str, user_id: str, used_count: int) -> bool:
    """บันทึกการใช้รหัส — UNIQUE(code, user_id) กันคนเดิมใช้ซ้ำนับเกิน"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    hdr = {**_HEADERS(), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{SUPABASE_URL}/rest/v1/invite_redemptions", headers=hdr,
                                 json={"code": code, "user_id": user_id})
        if resp.status_code not in (200, 201, 204):
            return False
        await client.patch(f"{SUPABASE_URL}/rest/v1/invites", headers=hdr,
                           params={"code": f"eq.{code}", "used_count": f"eq.{used_count}"},
                           json={"used_count": used_count + 1})
    return True


async def list_invites(limit: int = 100) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{SUPABASE_URL}/rest/v1/invites", headers={**_HEADERS(), "Prefer": ""},
                                params={"select": "*", "order": "created_at.desc", "limit": str(limit)})
    return resp.json() if resp.status_code == 200 else []


async def expire_invite(code: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(f"{SUPABASE_URL}/rest/v1/invites", headers={**_HEADERS(), "Prefer": "return=minimal"},
                                  params={"code": f"eq.{code}"}, json={"expires_at": datetime.now(timezone.utc).isoformat()})
    return resp.status_code in (200, 204)


async def log_audit(actor_user_id: str | None, actor_role: str, action: str, target_type: str | None = None,
                    target_id=None, target_owner: str | None = None, detail: dict | None = None) -> None:
    """บันทึก audit — ล้มเหลวได้โดยไม่ทำให้คำขอหลักพัง (log ERROR ไว้ตรวจ)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{SUPABASE_URL}/rest/v1/audit_logs",
                                     headers={**_HEADERS(), "Prefer": "return=minimal"},
                                     json={"actor_user_id": actor_user_id, "actor_role": actor_role, "action": action,
                                           "target_type": target_type,
                                           "target_id": None if target_id is None else str(target_id),
                                           "target_owner": target_owner, "detail": detail})
        if resp.status_code not in (200, 201, 204):
            logger.error(f"audit log failed: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.error(f"audit log failed ({type(e).__name__})")


async def list_audit_logs(since_iso: str, target_owner: str | None = None, limit: int = 500) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    params = {"created_at": f"gte.{since_iso}", "order": "created_at.desc", "limit": str(limit), "select": "*"}
    if target_owner:
        params["target_owner"] = f"eq.{target_owner}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{SUPABASE_URL}/rest/v1/audit_logs", headers={**_HEADERS(), "Prefer": ""}, params=params)
    return resp.json() if resp.status_code == 200 else []


async def log_event(user_id: str | None, name: str, plot_id: int | None = None, props: dict | None = None) -> None:
    """สถิติการใช้งาน (B2G) — ล้มเหลวเงียบ ๆ ได้ ไม่กระทบผู้ใช้"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{SUPABASE_URL}/rest/v1/events", headers={**_HEADERS(), "Prefer": "return=minimal"},
                              json={"user_id": user_id, "name": name, "plot_id": plot_id, "props": props})
    except Exception as e:
        logger.warning(f"log_event failed ({type(e).__name__})")


# ── คำแนะนำ (Phase 1d — migrate_v15) ─────────────────────
_RULE_COLS = ("id,rule_key,crop,problem,severity,title_th,reason_th,actions_th,status,source,removed,"
              "reviewed_by,reviewed_at,version,updated_by,updated_at")


def _rule_row(r: dict) -> dict:
    return {**r, "key": r.get("rule_key"), "actions_th": r.get("actions_th") or []}


async def list_recommendation_rules() -> list[dict] | None:
    """กฎทั้งหมด — None ถ้ายังไม่มีตาราง/อ่านไม่ได้ (ผู้เรียก fallback ไปใช้ร่างใน config)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    rows = await _fetch_all("recommendation_rules", {"select": _RULE_COLS, "order": "problem.asc,severity.asc,id.asc"})
    return None if rows is None else [_rule_row(r) for r in rows]


async def get_recommendation_rule(rule_id: int) -> dict | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/recommendation_rules",
            headers={**_HEADERS(), "Prefer": ""},
            params={"id": f"eq.{rule_id}", "select": _RULE_COLS, "limit": "1"},
        )
    if resp.status_code != 200 or not resp.json():
        return None
    return _rule_row(resp.json()[0])


async def _log_rule_history(client: httpx.AsyncClient, rule: dict, action: str, changed_by: str) -> None:
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/recommendation_rule_history",
        headers={**_HEADERS(), "Prefer": "return=minimal"},
        json={"rule_id": rule["id"], "version": rule.get("version"), "action": action,
              "changed_by": changed_by, "snapshot": {k: v for k, v in rule.items() if k != "key"}},
    )
    if resp.status_code not in (200, 201, 204):
        logger.error(f"Supabase rule history failed: {resp.status_code} — {resp.text}")


async def save_recommendation_rule(rule_id: int | None, fields: dict, action: str, changed_by: str) -> dict | None:
    """
    สร้าง (rule_id=None) หรือแก้กฎ + บันทึกประวัติทุกครั้ง (recommendation_rule_history เก็บทั้งแถวหลังแก้)
    action: create / edit / approve / retire / restore
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    now = datetime.now(timezone.utc).isoformat()
    hdr = {**_HEADERS(), "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=10) as client:
        if rule_id is None:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/recommendation_rules", headers=hdr,
                params={"select": _RULE_COLS},
                json={**fields, "version": 1, "updated_by": changed_by, "updated_at": now},
            )
        else:
            current = await get_recommendation_rule(rule_id)
            if not current:
                return None
            resp = await client.patch(
                f"{SUPABASE_URL}/rest/v1/recommendation_rules", headers=hdr,
                params={"id": f"eq.{rule_id}", "select": _RULE_COLS},
                json={**fields, "version": (current.get("version") or 1) + 1,
                      "updated_by": changed_by, "updated_at": now},
            )
        if resp.status_code not in (200, 201) or not resp.json():
            logger.error(f"Supabase save_recommendation_rule failed: {resp.status_code} — {resp.text}")
            return None
        row = _rule_row(resp.json()[0])
        await _log_rule_history(client, row, action, changed_by)
    return row


async def get_recommendation_rule_history(rule_id: int) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/recommendation_rule_history",
            headers={**_HEADERS(), "Prefer": ""},
            params={"rule_id": f"eq.{rule_id}", "order": "changed_at.desc", "limit": "50",
                    "select": "id,version,action,changed_by,changed_at,snapshot"},
        )
    return resp.json() if resp.status_code == 200 else []


async def get_latest_plot_signals(plot_id: int) -> dict | None:
    """
    สัญญาณระดับแปลงล่าสุดสำหรับคำแนะนำ (1d): risk_v2 (มีเฉพาะแถวจากสแกนรายวัน) + ndvi_change + หน้าดิน
    ใช้แถวล่าสุดที่มี risk_v2 ถ้าไม่มีเลยใช้แถวล่าสุด
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    sel = "created_at,ndvi_change,risk_v2:full_data->risk_v2,topsoil_risk_level:full_data->>topsoil_risk_level"
    async with httpx.AsyncClient(timeout=10) as client:
        for extra in ({"full_data->risk_v2": "not.is.null"}, {}):
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/analyses",
                headers={**_HEADERS(), "Prefer": ""},
                params={"plot_id": f"eq.{plot_id}", "order": "created_at.desc", "limit": "1",
                        "select": sel, **extra},
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]
    return None


async def get_notifiable_users() -> set[str]:
    """ดึง user_id ทั้งหมดที่เปิดรับการแจ้งเตือน (notify_weekly = true)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    rows = await _fetch_all("users", {"notify_weekly": "eq.true", "select": "user_id"})
    return {row["user_id"] for row in rows or []}


async def set_notify_digest(user_id: str, enabled: bool) -> bool:
    """
    เปิด/ปิด "สรุปแปลงประจำวัน" — คนละอย่างกับ set_notify (notify_weekly) ซึ่งแจ้ง
    เฉพาะตอนเสี่ยงสูงเท่านั้น อันนี้ส่งสรุปทุกแปลงทุกเช้าไม่ว่าผลจะเป็นอย่างไร
    (ผู้ใช้ขอเพิ่มมาแยกต่างหาก ไม่อยากให้ทุกคนที่เปิด notify_weekly โดนสรุปทุกวันไปด้วย)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json={"notify_daily_digest": enabled},
        )

    ok = resp.status_code in (200, 204)
    if not ok:
        logger.error(f"Supabase set_notify_digest failed: {resp.status_code}")
    return ok


async def get_digest_users() -> set[str]:
    """ดึง user_id ทั้งหมดที่เปิดรับสรุปแปลง (คอลัมน์ notify_daily_digest — ตั้งแต่ Phase 1b ส่งเป็นรายสัปดาห์)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    rows = await _fetch_all("users", {"notify_daily_digest": "eq.true", "select": "user_id"})
    return {row["user_id"] for row in rows or []}


PRESET_NOTIFY_HOURS = (5, 6, 7, 8, 9, 10)  # ต้องตรงกับ CHECK constraint ใน migrate_v8.sql


async def get_scheduler_last_run(job_name: str) -> dict | None:
    """
    ดึงสถานะ "ชั่วโมงล่าสุดของวันนี้ที่ job นี้ทำสำเร็จแล้ว" — ให้
    scheduler.py::run_job_with_catchup ใช้ตัดสินใจว่าต้อง "ตามงานย้อนหลัง" กี่ชั่วโมง
    (ดู migrate_v10.sql — เพิ่มหลังพบว่า GitHub Actions ทิ้งรอบ cron 07:00 น. ไปทั้ง
    รอบจริง ทำให้ผู้ใช้ทุกคนพลาดการแจ้งเตือนทั้งวัน)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/scheduler_runs",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "job_name": f"eq.{job_name}",
                "select":   "last_run_date,last_run_hour",
                "limit":    "1",
            },
        )
    # 42P01 = ยังไม่ได้รัน migrate_v10.sql (ตารางยังไม่มี) — คืน None แบบเดียวกับ
    # "ไม่เคยรันมาก่อน" กันการตามงานย้อนหลังพังเงียบๆ ทั้งที่แค่ยัง migrate ไม่เสร็จ
    if resp.status_code != 200 or not resp.json():
        return None
    return resp.json()[0]


async def set_scheduler_last_run(job_name: str, run_date: str, hour: int) -> bool:
    """บันทึกว่า job นี้ทำถึงชั่วโมง hour แล้วสำหรับวันที่ run_date (upsert ตาม job_name)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/scheduler_runs",
            headers={**_HEADERS(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"job_name": job_name, "last_run_date": run_date, "last_run_hour": hour},
        )
    ok = resp.status_code in (200, 201, 204)
    if not ok:
        logger.error(f"Supabase set_scheduler_last_run failed: {resp.status_code} — {resp.text}")
    return ok


async def set_notify_hour(user_id: str, hour: int) -> bool:
    """
    ตั้งเวลาแจ้งเตือนที่ผู้ใช้เลือกเอง — ใช้ร่วมกันทั้ง "แจ้งเตือนเฉพาะตอนเสี่ยง"
    และ "สรุปแปลงประจำวัน" (ผู้ใช้ขอให้ทั้งสองแบบใช้เวลาเดียวกัน ไม่ต้องตั้งแยก)
    """
    if hour not in PRESET_NOTIFY_HOURS:
        logger.error(f"set_notify_hour rejected out-of-range hour={hour} for {user_id}")
        return False
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json={"notify_hour": hour},
        )

    ok = resp.status_code in (200, 204)
    if not ok:
        logger.error(f"Supabase set_notify_hour failed: {resp.status_code}")
    return ok


async def get_users_by_hour(hour: int) -> set[str] | None:
    """
    ดึง user_id ทั้งหมดที่ตั้งเวลาแจ้งเตือนไว้ตรงกับ hour นี้ (0-23, เวลาไทย) — ใช้กรอง
    ก่อนสแกน/ส่งใน daily_scan_job และ rain_alert_job ตอนถูกทริกเกอร์จากภายนอกเป็น
    รายชั่วโมง (ดู .github/workflows/keep-alive.yml) กันสแกน/ส่งซ้ำให้คนที่ไม่ได้ตั้ง
    เวลานี้ไว้

    ถ้าดึงไม่สำเร็จคืน set() ว่าง (รอบนี้ไม่ส่งให้ใคร) — เดิมคืน None ให้ผู้เรียก
    "ไม่กรอง" ซึ่งทำให้ทุกคนได้ข้อความทุกชั่วโมงที่ทริกเกอร์ (6 รอบ/วัน) เปลืองโควตา LINE
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    rows = await _fetch_all("users", {"notify_hour": f"eq.{hour}", "select": "user_id"})
    if rows is None:
        # คำขอล้มเหลว (รายละเอียดใน log ของ _fetch_all) — คืน set ว่าง = รอบนี้ไม่ส่งให้ใคร
        # (ถ้าคืน None ระบบจะ "ไม่กรอง" แล้วส่งให้ทุกคนทุกชั่วโมงที่ทริกเกอร์ เปลืองโควตา
        # LINE มาก) — กรณีคอลัมน์ notify_hour ยังไม่มีเลิกรองรับแล้ว migrate_v8 รันไปนานแล้ว
        logger.error("get_users_by_hour failed — no notifications for this hour")
        return set()
    return {row["user_id"] for row in rows}


async def get_recent_analyses(plot_id: int, days: int = 10) -> list[dict]:
    """ดึงผลวิเคราะห์ล่าสุด n วัน สำหรับตรวจ escalation (scan รายวัน)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":     f"eq.{plot_id}",
                "created_at":  f"gte.{cutoff}",
                "order":       "created_at.desc",
                "select":      "ndvi_change,elevation_diff,soil_moisture_vv,created_at",
            },
        )

    if resp.status_code != 200:
        return []
    return resp.json()


async def get_plot_by_id(plot_id: int) -> dict | None:
    """ดึงข้อมูล plot (รวม user_id) จาก plot_id — ใช้โดย IoT endpoint"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": ""},
            # select * — ได้ polygon/plot_type ไปใช้ตอนแก้ไขแปลง และไม่พังถ้ายังไม่ได้รัน migration ใหม่
            params={"id": f"eq.{plot_id}", "select": "*", "limit": "1"},
        )
    if resp.status_code != 200 or not resp.json():
        return None
    return resp.json()[0]


async def save_iot_reading(plot_id: int, sensor_id: str, depth_cm: int,
                           moisture_pct: float, temp_c: float,
                           timestamp: str | None = None) -> int | None:
    """บันทึก IoT sensor reading ลง Supabase → คืน id"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    payload: dict = {
        "plot_id":      plot_id,
        "sensor_id":    sensor_id,
        "depth_cm":     depth_cm,
        "moisture_pct": moisture_pct,
        "temp_c":       temp_c,
    }
    if timestamp:
        payload["timestamp"] = timestamp

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/iot_readings",
            headers={**_HEADERS(), "Prefer": "return=representation"},
            json=payload,
        )
    if resp.status_code in (200, 201):
        rows = resp.json()
        return rows[0]["id"] if rows else None
    logger.error(f"Supabase save_iot_reading failed: {resp.status_code} — {resp.text}")
    return None


async def save_field_observation(plot_id: int, actual_yield_kg: float,
                                  root_rot_occurred: bool,
                                  observation_date: str) -> int | None:
    """บันทึก field observation จริงจากเกษตรกร (Gap 5: ใช้ retrain model)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    payload = {
        "plot_id":           plot_id,
        "actual_yield_kg":   actual_yield_kg,
        "root_rot_occurred": root_rot_occurred,
        "observation_date":  observation_date,
        "created_at":        datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/field_observations",
            headers={**_HEADERS(), "Prefer": "return=representation"},
            json=payload,
        )
    if resp.status_code in (200, 201):
        rows = resp.json()
        return rows[0]["id"] if rows else None
    logger.error(f"Supabase save_field_observation failed: {resp.status_code} — {resp.text}")
    return None


async def get_latest_plot_analysis(plot_id: int) -> dict | None:
    """ดึงผลวิเคราะห์ล่าสุดของแปลง (รวม displacement + soil data สำหรับ weather alert)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":  f"eq.{plot_id}",
                "order":    "created_at.desc",
                "limit":    "1",
                "select":   "elevation_diff,soil_moisture_vv,displacement_level,"
                            "surface_stability,displacement_vv_change,"
                            "land_impact_severity,land_impact_score,ndvi_now",
            },
        )

    if resp.status_code != 200 or not resp.json():
        return None
    return resp.json()[0]


async def get_swab_level_days_ago(plot_id: int, days_ago: int = 7) -> int | None:
    """
    ดึง "ระดับ" ความชื้น (swab_level, ดู gee_analysis.swab_index_to_level) ของแปลง
    นี้จากผลวิเคราะห์ที่ใกล้เคียง days_ago วันก่อนที่สุด — ใช้เทียบแนวโน้มในการ์ด
    สรุปประจำวัน (ผู้ใช้ขอ "บอกได้ว่าดีขึ้น/แย่ลงกี่ระดับจากสัปดาห์ก่อน")

    เอาแถวล่าสุดที่เก่ากว่า (days_ago - 1) วัน (ไม่ใช่แถวที่ตรง days_ago เป๊ะ เพราะ
    สแกนไม่ได้รันทุกวันแม่นยำเป๊ะเสมอ) — คืน None ถ้าแปลงนี้ยังไม่มีประวัติเก่าขนาดนั้น
    (เช่น เพิ่งเพิ่มแปลงมาไม่ถึงสัปดาห์) หรือแถวนั้นไม่มี full_data (ก่อน migrate_v5)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_ago - 1)).isoformat()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":     f"eq.{plot_id}",
                "created_at":  f"lte.{cutoff}",
                "order":       "created_at.desc",
                "limit":       "1",
                "select":      "full_data",
            },
        )

    if resp.status_code != 200 or not resp.json():
        return None
    full_data = resp.json()[0].get("full_data")
    if not full_data:
        return None
    swab = full_data.get("swab") or {}
    level = swab.get("swab_level")
    if level is not None:
        return level
    # แถวเก่าก่อนมีคีย์นี้ — คำนวณสดจาก swab_index แทน (ไม่ import gee_analysis ที่นี่
    # กัน database.py ผูกกับ ee/GEE โดยไม่จำเป็น — สูตรง่ายพอทำซ้ำตรงนี้ได้)
    swab_index = swab.get("swab_index")
    if swab_index is None:
        return None
    if -0.15 <= swab_index <= 0.10:
        return 0
    if swab_index > 0.10:
        return min(5, 1 + int((swab_index - 0.10) // 0.15))
    return max(-5, -1 - int((-0.15 - swab_index) // 0.15))


# ─────────────────────────────────────────────────────────
# Grid snapshots — เก็บผลตารางความชื้น/น้ำขังรายวันของแต่ละแปลง
# ใช้หาจุด "ชื้นซ้ำๆ ต่อเนื่อง" → บ่งชี้แนวโน้มทางน้ำไหลใต้ผิวดิน (ดู migrate_v6.sql)
# ─────────────────────────────────────────────────────────

async def save_grid_snapshot(plot_id: int, points: list[dict]) -> bool:
    """
    บันทึกผลตาราง grid ของแปลงหนึ่งๆ ณ เวลานี้ (1 แถวต่อ 1 จุด) — insert
    เป็นก้อนเดียว (bulk) ไม่วนยิงทีละจุด ให้ daily_scan_job เรียกทุกวันสำหรับ
    แปลงที่มี polygon เพื่อสะสมประวัติไว้คำนวณ persistence
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    if not points:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "plot_id":    plot_id,
            "lat":        p["lat"],
            "lng":        p["lng"],
            "status":     p.get("status", "optimal"),
            "swab_index": p.get("swab_index"),
            "created_at": now_iso,
        }
        for p in points
    ]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/grid_snapshots",
            headers=_HEADERS(),
            json=rows,
        )

    if resp.status_code not in (200, 201):
        logger.error(f"Supabase save_grid_snapshot failed for plot {plot_id}: "
                     f"{resp.status_code} — {resp.text}")
        return False
    return True


async def delete_old_grid_snapshots(older_than_days: int = 30) -> bool:
    """
    ลบ grid_snapshots ที่เก่ากว่า older_than_days วัน — เจอระหว่างตรวจความพร้อม
    ก่อนขยายผู้ใช้ (2026-08-22): บันทึก 1 แถวต่อ 1 จุดตาราง ทุกแปลงที่มีขอบเขต
    ทุกวัน (ดู save_grid_snapshot) แต่ get_persistent_wet_points() มองย้อนหลัง
    แค่ 14 วันเป็นค่าเริ่มต้นเท่านั้น — ไม่เคยมีการลบมาก่อน ตารางเลยโตไม่จำกัด
    (พบจริง ~11,900 แถวจากแค่ 13 แปลงช่วงทดสอบสั้นๆ) ขยายไปหลายสิบผู้ใช้จะเกิน
    Supabase free tier 500MB ได้เร็วมาก เก็บ buffer ไว้ 30 วัน (มากกว่า 14 วันที่
    query จริงใช้ เผื่ออนาคตอยากขยายช่วงดูย้อนหลัง) — เรียกจาก daily_scan_job
    ทุกรอบที่ทำงาน (DELETE ตาม cutoff วันที่ ไม่แพง เรียกซ้ำได้ไม่มีผลข้างเคียง)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{SUPABASE_URL}/rest/v1/grid_snapshots",
            headers={**_HEADERS(), "Prefer": "return=minimal"},
            params={"created_at": f"lt.{cutoff}"},
        )
    if resp.status_code not in (200, 204):
        logger.error(f"Supabase delete_old_grid_snapshots failed: {resp.status_code} — {resp.text}")
        return False
    return True


async def get_persistent_wet_points(
    plot_id: int, days: int = 14, min_days_observed: int = 3, min_wet_ratio: float = 0.6
) -> list[dict]:
    """
    หาจุดที่ "ชื้น/น้ำขัง" ซ้ำๆ ในหลายๆ วันที่ผ่านมา (ไม่ใช่แค่ฝนตกวันเดียวแล้วแห้ง)
    รวมจุดจาก grid_snapshots ตาม lat/lng ที่ปัดเป็นบัคเก็ตหยาบๆ (~11ม.) เพราะจุดตาราง
    ที่สุ่มจาก GEE แต่ละวันอาจไม่ได้พิกัดตรงเป๊ะทุก decimal เหมือนกันทุกครั้ง
    คืนเฉพาะจุดที่สังเกตมาแล้วอย่างน้อย min_days_observed วัน และ "ชื้น/น้ำขัง"
    ในสัดส่วน ≥ min_wet_ratio ของวันที่สังเกต — เรียงจาก wet_ratio มากไปน้อย
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/grid_snapshots",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":    f"eq.{plot_id}",
                "created_at": f"gte.{cutoff}",
                "select":     "lat,lng,status,swab_index,created_at",
            },
        )
    if resp.status_code != 200:
        logger.error(f"Supabase get_persistent_wet_points failed: {resp.status_code}")
        return []

    rows = resp.json()
    if not rows:
        return []

    WET_STATUSES = {"waterlogged", "wet"}
    buckets: dict[tuple[float, float], dict] = {}
    for r in rows:
        key = (round(r["lat"], 4), round(r["lng"], 4))  # ~11ม. ที่ละติจูดไทย
        b = buckets.setdefault(key, {"days": set(), "wet_days": 0, "swab_sum": 0.0, "swab_n": 0})
        day = r["created_at"][:10]  # YYYY-MM-DD — นับ 1 ครั้งต่อวัน แม้มีหลาย snapshot วันเดียวกัน
        if day in b["days"]:
            continue
        b["days"].add(day)
        if r.get("status") in WET_STATUSES:
            b["wet_days"] += 1
        if r.get("swab_index") is not None:
            b["swab_sum"] += r["swab_index"]
            b["swab_n"] += 1

    persistent = []
    for (lat, lng), b in buckets.items():
        days_observed = len(b["days"])
        if days_observed < min_days_observed:
            continue
        wet_ratio = b["wet_days"] / days_observed
        if wet_ratio < min_wet_ratio:
            continue
        persistent.append({
            "lat": lat,
            "lng": lng,
            "days_observed": days_observed,
            "wet_days": b["wet_days"],
            "wet_ratio": round(wet_ratio, 2),
            "avg_swab_index": round(b["swab_sum"] / b["swab_n"], 3) if b["swab_n"] else None,
        })

    persistent.sort(key=lambda p: p["wet_ratio"], reverse=True)
    return persistent


# ─────────────────────────────────────────────────────────
# Phase 1b — ลดข้อความ LINE
# ─────────────────────────────────────────────────────────

async def get_previous_risk_level(plot_id: int, before_iso: str) -> str | None:
    """
    ระดับความเสี่ยงรวม (high/medium/ok) ของผลวิเคราะห์ล่าสุด "ก่อน" เวลา before_iso
    ใช้ตัดสินว่าความเสี่ยง "เพิ่มขึ้น" หรือไม่ — แจ้งเตือนเฉพาะตอนเพิ่มขึ้นเท่านั้น
    คืน None ถ้าไม่เคยมีผลก่อนหน้า
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/analyses",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "plot_id":    f"eq.{plot_id}",
                "created_at": f"lt.{before_iso}",
                "order":      "created_at.desc",
                "limit":      "1",
                "select":     "full_data,ndvi_change,elevation_diff,soil_moisture_vv",
            },
        )
    if resp.status_code != 200 or not resp.json():
        return None
    row = resp.json()[0]
    full = row.get("full_data") or {}
    level = full.get("overall_risk_level")
    if level in ("high", "medium", "ok"):
        return level
    return compute_risk_level(full or row)


# ตาราง line_message_log (migrate_v13.sql) — ถ้ายังไม่ได้รัน migration ฟังก์ชันด้านล่าง
# ทำงานแบบ "ไม่มีบันทึก" (log เตือนครั้งเดียว) ระบบหลักยังทำงานได้ปกติ
_message_log_available: bool | None = None


def _message_log_missing(resp) -> bool:
    global _message_log_available
    if resp.status_code == 404 or "PGRST205" in resp.text or "42P01" in resp.text:
        if _message_log_available is not False:
            logger.warning("line_message_log table missing — run supabase/migrate_v13.sql "
                           "(message logging / alert de-duplication disabled until then)")
        _message_log_available = False
        return True
    return False


async def log_line_message(user_id: str, kind: str, priority: str, status: str,
                           http_status: int | None = None, plot_id: int | None = None,
                           level: str | None = None, error: str | None = None) -> None:
    """บันทึกทุกการส่ง LINE (สำเร็จ/ไม่สำเร็จ/ข้ามเพราะโควตา) — ไม่ให้ล้มงานหลักถ้าบันทึกพัง"""
    if not SUPABASE_URL or not SUPABASE_KEY or _message_log_available is False:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/line_message_log",
                headers={**_HEADERS(), "Prefer": "return=minimal"},
                json={"user_id": user_id, "plot_id": plot_id, "kind": kind,
                      "priority": priority, "status": status, "http_status": http_status,
                      "level": level, "error": (error or "")[:500] or None},
            )
        if resp.status_code not in (200, 201) and not _message_log_missing(resp):
            logger.warning(f"log_line_message failed: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"log_line_message error: {type(e).__name__}")


async def get_last_sent_message(plot_id: int, kind: str | list[str], since_iso: str) -> dict | None:
    """ข้อความประเภท kind (หรือหลายประเภท) ที่ "ส่งสำเร็จ" ล่าสุดของแปลงนี้ตั้งแต่ since_iso (ใช้กันส่งซ้ำ)"""
    kind_filter = f"in.({','.join(kind)})" if isinstance(kind, list) else f"eq.{kind}"
    if not SUPABASE_URL or not SUPABASE_KEY or _message_log_available is False:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/line_message_log",
            headers={**_HEADERS(), "Prefer": ""},
            params={"plot_id": f"eq.{plot_id}", "kind": kind_filter, "status": "eq.sent",
                    "created_at": f"gte.{since_iso}", "order": "created_at.desc", "limit": "1",
                    "select": "kind,level,created_at"},
        )
    if resp.status_code != 200:
        _message_log_missing(resp)
        return None
    rows = resp.json()
    return rows[0] if rows else None


async def get_shadow_rows(since_iso: str) -> list[dict]:
    """ผลวิเคราะห์ตั้งแต่ since_iso พร้อมระดับเดิม + risk_v2 + plot_profile สำหรับสรุปผลรันคู่"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    rows = await _fetch_all("analyses", {
        "select": "plot_id,created_at,old:full_data->>overall_risk_level,"
                  "risk_v2:full_data->risk_v2,profile:full_data->plot_profile",
        "created_at": f"gte.{since_iso}", "plot_id": "not.is.null", "order": "created_at.asc"})
    return rows or []


async def get_recent_message_failures(since_iso: str, limit: int = 50) -> list[dict] | None:
    """การส่งที่ล้มเหลว/ถูกข้ามล่าสุด สำหรับหน้า admin — None ถ้ายังไม่มีตาราง"""
    if not SUPABASE_URL or not SUPABASE_KEY or _message_log_available is False:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/line_message_log",
            headers={**_HEADERS(), "Prefer": ""},
            params={"status": "neq.sent", "created_at": f"gte.{since_iso}",
                    "order": "created_at.desc", "limit": str(limit),
                    "select": "kind,priority,status,http_status,error,created_at"},
        )
    if resp.status_code != 200:
        _message_log_missing(resp)
        return None
    return resp.json()
