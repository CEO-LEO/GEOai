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

    # สวนทุเรียนจันทบุรี/ตราด พิกัดจริง
    plots = [
        (12.6011, 102.1042, "U001"),
        (12.5834, 102.0918, "U001"),
        (12.6247, 102.1389, "U002"),
        (12.5502, 102.0671, "U002"),
        (12.6129, 102.0553, "U003"),
        (12.5701, 102.1204, "U003"),
        (12.6388, 102.0812, "U004"),
        (12.5945, 102.1467, "U004"),
        (12.5215, 102.0395, "U005"),
        (12.6478, 102.1601, "U005"),
        (12.5612, 102.0987, "U006"),
        (12.6055, 102.0234, "U007"),
    ]

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
        "risk_level":       data.get("topsoil_risk_level"),
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
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers=headers,
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error(f"Supabase upsert user failed: {resp.status_code} — {resp.text}")


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
                    polygon: list | None = None) -> int | None:
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
    """ดึงแปลงทั้งหมดของ user"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/plots",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "user_id": f"eq.{user_id}",
                "order":   "created_at.asc",
                "select":  "*",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_user_plots failed: {resp.status_code}")
        return []
    return resp.json()


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


async def get_notifiable_users() -> set[str]:
    """ดึง user_id ทั้งหมดที่เปิดรับการแจ้งเตือน (notify_weekly = true)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "notify_weekly": "eq.true",
                "select":        "user_id",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_notifiable_users failed: {resp.status_code}")
        return set()
    return {row["user_id"] for row in resp.json()}


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
    """ดึง user_id ทั้งหมดที่เปิดรับสรุปแปลงประจำวัน (notify_daily_digest = true)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "notify_daily_digest": "eq.true",
                "select":              "user_id",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Supabase get_digest_users failed: {resp.status_code}")
        return set()
    return {row["user_id"] for row in resp.json()}


PRESET_NOTIFY_HOURS = (5, 6, 7, 8, 9, 10)  # ต้องตรงกับ CHECK constraint ใน migrate_v8.sql


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

    คืน None (แทน set() ว่าง) เฉพาะกรณีคอลัมน์ notify_hour ยังไม่มีในฐานข้อมูลจริง
    (ยังไม่ได้รัน migrate_v8.sql) — เพื่อให้ตัวเรียกรู้ว่า "ยังกรองตามชั่วโมงไม่ได้"
    แล้ว fallback ไปแบบเดิม (ไม่กรอง สแกนทุกคน) แทนที่จะกรองแล้วเจอ error ตลอดจน
    ไม่มีใครถูกสแกนเลยสักคนเงียบๆ (อันตรายกว่า "ยังไม่กรอง" มาก)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**_HEADERS(), "Prefer": ""},
            params={
                "notify_hour": f"eq.{hour}",
                "select":      "user_id",
            },
        )

    if resp.status_code != 200:
        if resp.status_code == 400 and "42703" in resp.text:
            logger.warning(
                "get_users_by_hour: notify_hour column missing (migrate_v8.sql not "
                "run yet?) — falling back to unfiltered (everyone scanned this run)"
            )
            return None
        logger.error(f"Supabase get_users_by_hour failed: {resp.status_code}")
        return set()
    return {row["user_id"] for row in resp.json()}


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
            params={"id": f"eq.{plot_id}", "select": "id,user_id,lat,lng,name", "limit": "1"},
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
