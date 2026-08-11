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

    async with httpx.AsyncClient(timeout=10) as client:
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
    """ดึงผลวิเคราะห์ล่าสุดของ user_id"""
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
    return rows[0] if rows else None


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
                           "land_impact_severity,land_impact_score,"
                           "message,created_at",
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
