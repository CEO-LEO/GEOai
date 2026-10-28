"""
store.py — อ่าน/เขียน Supabase ของ crop_classifier (ตาราง migrate_v21) — ใช้ service_role ผ่าน database.py
ทุกฟังก์ชันคืนค่าว่าง/False เมื่อไม่ได้ตั้งค่า Supabase (dev/test)
"""

import logging
from datetime import datetime, timezone

import httpx

import database as db

logger = logging.getLogger(__name__)


def _ready() -> bool:
    return bool(db.SUPABASE_URL and db.SUPABASE_KEY)


def _url(table: str) -> str:
    return f"{db.SUPABASE_URL}/rest/v1/{table}"


async def _get(table: str, params: dict) -> list[dict]:
    if not _ready():
        return []
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_url(table), headers={**db._HEADERS(), "Prefer": ""}, params=params)
    if r.status_code != 200:
        logger.error(f"crop store GET {table} failed: {r.status_code} — {r.text[:200]}")
        return []
    return r.json()


async def _write(method: str, table: str, params: dict | None, body, prefer: str = "return=representation") -> list[dict] | None:
    if not _ready():
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, _url(table), headers={**db._HEADERS(), "Prefer": prefer}, params=params or {}, json=body)
    if r.status_code not in (200, 201, 204):
        logger.error(f"crop store {method} {table} failed: {r.status_code} — {r.text[:200]}")
        return None
    return r.json() if r.content else []


# ── feature ──────────────────────────────────────────────
async def get_plot_features(plot_id: int, feature_set: str, year: int) -> dict | None:
    rows = await _get("crop_features", {"plot_id": f"eq.{plot_id}", "feature_set": f"eq.{feature_set}",
                                        "year": f"eq.{year}", "limit": "1"})
    return rows[0] if rows else None


async def upsert_features(row: dict) -> bool:
    key = "plot_id" if row.get("plot_id") is not None else "ref_id"
    await _write("DELETE", "crop_features", {key: f"eq.{row[key]}", "feature_set": f"eq.{row['feature_set']}",
                                             "year": f"eq.{row['year']}"}, None, prefer="return=minimal")
    return await _write("POST", "crop_features", None, row, prefer="return=minimal") is not None


async def list_features(feature_set: str, year: int) -> list[dict]:
    if not _ready():
        return []
    return await db._fetch_all("crop_features", {"feature_set": f"eq.{feature_set}", "year": f"eq.{year}",
                                                 "select": "plot_id,ref_id,geom_hash,vals,meta"}) or []


# ── โมเดล ────────────────────────────────────────────────
_MODEL_COLS = "version,algorithm,feature_set,classes,params,metrics,dataset,passes_gate,active,created_by,created_at"


async def get_active_model() -> dict | None:
    rows = await _get("crop_models", {"active": "is.true", "select": _MODEL_COLS, "limit": "1"})
    return rows[0] if rows else None


async def list_models(limit: int = 20) -> list[dict]:
    return await _get("crop_models", {"select": "version,algorithm,feature_set,classes,metrics,dataset,passes_gate,active,"
                                                "created_by,created_at", "order": "created_at.desc", "limit": str(limit)})


async def get_model(version: str) -> dict | None:
    rows = await _get("crop_models", {"version": f"eq.{version}", "select": _MODEL_COLS, "limit": "1"})
    return rows[0] if rows else None


async def insert_model(row: dict) -> bool:
    return await _write("POST", "crop_models", None, row, prefer="return=minimal") is not None


async def set_active_model(version: str | None) -> bool:
    """เปิดใช้ทีละตัว (version=None = ปิดทั้งหมด)"""
    if await _write("PATCH", "crop_models", {"active": "is.true"}, {"active": False}, prefer="return=minimal") is None:
        return False
    if version is None:
        return True
    return await _write("PATCH", "crop_models", {"version": f"eq.{version}"}, {"active": True},
                        prefer="return=minimal") is not None


# ── ผลทาย / คำตอบ ─────────────────────────────────────────
async def latest_prediction(plot_id: int) -> dict | None:
    rows = await _get("crop_predictions", {"plot_id": f"eq.{plot_id}", "order": "created_at.desc", "limit": "1",
                                           "select": "id,plot_id,model_version,geom_hash,status,top,confidence,created_at"})
    return rows[0] if rows else None


async def insert_prediction(row: dict) -> dict | None:
    rows = await _write("POST", "crop_predictions", None, row)
    return rows[0] if rows else None


async def insert_feedback(row: dict) -> dict | None:
    rows = await _write("POST", "crop_label_feedback", None, row)
    return rows[0] if rows else None


async def list_feedback(plot_id: int | None = None) -> list[dict]:
    if not _ready():
        return []
    params = {"select": "id,plot_id,prediction_id,answer,crop_ids,by_user,role,created_at", "order": "created_at.asc"}
    if plot_id is not None:
        params["plot_id"] = f"eq.{plot_id}"
    return await db._fetch_all("crop_label_feedback", params) or []


# ── ข้อมูลสำหรับชุดฝึก ──────────────────────────────────────
async def training_inputs() -> dict:
    """แปลง+พืช+คำตอบ+รูปมีพิกัด ของเจ้าของที่ยินยอม "ใช้ปรับปรุงระบบ" + แปลงอ้างอิง"""
    if not _ready():
        return {"plots": [], "feedback": [], "geo_photo_plots": set(), "references": [], "consented_users": set()}
    users = await db._fetch_all("users", {"consent_photos_research": "is.true", "deletion_requested_at": "is.null",
                                          "select": "user_id"}) or []
    consented = {u["user_id"] for u in users}
    plots = await db._fetch_all("plots", {"is_active": "is.true", "select": "id,user_id,lat,lng,polygon,area_code,plot_crops(crop_id)"}) or []
    plots = [p for p in plots if p["user_id"] in consented]
    for p in plots:
        p["crop_ids"] = [c["crop_id"] for c in p.pop("plot_crops", None) or []]
    photos = await db._fetch_all("photos", {"lat": "not.is.null", "select": "plot_id"}) or []
    refs = await db._fetch_all("reference_labels", {"select": "id,source,crop_ids,lat,lng,polygon,area_code"}) or []
    return {"plots": plots, "feedback": await list_feedback(), "geo_photo_plots": {r["plot_id"] for r in photos},
            "references": refs, "consented_users": consented}


async def upsert_reference_labels(rows: list[dict]) -> int:
    if not rows:
        return 0
    out = await _write("POST", "reference_labels", {"on_conflict": "source,source_ref"}, rows,
                       prefer="return=representation,resolution=merge-duplicates")
    return len(out or [])


_SURVEY_COLS = "id,source_ref,crop_ids,lat,lng,area_code,imported_by,imported_at"


async def upsert_survey(row: dict) -> dict | None:
    """จุดสำรวจของเจ้าหน้าที่ — ส่งซ้ำ (สัญญาณหลุดแล้วส่งใหม่) ด้วย source_ref เดิมไม่เกิดแถวซ้ำ"""
    rows = await _write("POST", "reference_labels", {"on_conflict": "source,source_ref", "select": _SURVEY_COLS}, [row],
                        prefer="return=representation,resolution=merge-duplicates")
    return rows[0] if rows else None


async def list_surveys(by_user: str, limit: int = 50) -> list[dict]:
    return await _get("reference_labels", {"source": "eq.officer_survey", "imported_by": f"eq.{by_user}",
                                           "select": _SURVEY_COLS, "order": "imported_at.desc", "limit": str(limit)})


async def count_surveys(by_user: str) -> int:
    if not _ready():
        return 0
    rows = await db._fetch_all("reference_labels", {"source": "eq.officer_survey", "imported_by": f"eq.{by_user}",
                                                    "select": "id"}) or []
    return len(rows)


async def delete_survey(survey_id: int, by_user: str) -> bool:
    """ลบได้เฉพาะจุดที่ตัวเองสำรวจ (feature ที่คำนวณไว้ลบตาม ON DELETE CASCADE)"""
    rows = await _write("DELETE", "reference_labels", {"id": f"eq.{survey_id}", "source": "eq.officer_survey",
                                                       "imported_by": f"eq.{by_user}"}, None)
    return bool(rows)


async def label_counts() -> dict:
    """สรุปจำนวนต่อชนิดพืช/อำเภอสำหรับหน้า dashboard (ไม่มีพิกัด/ผู้ใช้)"""
    if not _ready():
        return {}
    fb = await list_feedback()
    refs = await db._fetch_all("reference_labels", {"select": "source,crop_ids,area_code"}) or []
    return {"feedback_total": len(fb),
            "feedback_by_answer": _count(f["answer"] for f in fb),
            "feedback_by_role": _count(f["role"] for f in fb),
            "references_total": len(refs),
            "references_by_source": _count(r["source"] for r in refs),
            "at": datetime.now(timezone.utc).isoformat()}


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out
