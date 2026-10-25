"""
areas.py — อำเภอของแปลง (Phase 2a) สำหรับพื้นที่รับผิดชอบของเจ้าหน้าที่และแดชบอร์ดจังหวัด

จุดกลางแปลง → อำเภอ ด้วยขอบเขต geoBoundaries ADM2 ใน Earth Engine (ไม่ต้องดาวน์โหลดไฟล์)
แล้วจับคู่ชื่อกับ config/admin_areas.json (รหัสอำเภอกรมการปกครอง + ชื่อไทย)
อำเภอนอกรายการใน config: เก็บ area_code = None และ area_name = ชื่อจาก geoBoundaries
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

import gee_analysis

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "admin_areas.json"
ADM2_ASSET = "WM/geoLab/geoBoundaries/600/ADM2"


@lru_cache(maxsize=1)
def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def districts() -> list[dict]:
    """อำเภอทั้งหมดใน config [{code, name_th, geo_name, province_code, province_th}]"""
    out = []
    for p in config()["provinces"]:
        for d in p["districts"]:
            out.append({**d, "province_code": p["code"], "province_th": p["name_th"]})
    return out


def by_code() -> dict[str, dict]:
    return {d["code"]: d for d in districts()}


def by_geo_name() -> dict[str, dict]:
    return {d["geo_name"]: d for d in districts()}


def name_th(code: str | None) -> str | None:
    d = by_code().get(code or "")
    return d["name_th"] if d else None


def validate_codes(codes: list[str] | None) -> list[str]:
    known = by_code()
    out: list[str] = []
    for c in codes or []:
        if c not in known:
            raise ValueError(f"ไม่รู้จักรหัสอำเภอ: {c}")
        if c not in out:
            out.append(c)
    return out


def from_geo_name(geo_name: str | None) -> dict:
    """ชื่อจาก geoBoundaries → {"area_code", "area_name"} — ฟังก์ชันล้วน ทดสอบได้"""
    if not geo_name:
        return {"area_code": None, "area_name": None}
    d = by_geo_name().get(geo_name)
    if d:
        return {"area_code": d["code"], "area_name": f"อ.{d['name_th']} จ.{d['province_th']}"}
    return {"area_code": None, "area_name": geo_name}


def lookup_point(lat: float, lng: float) -> dict:
    """GEE 1 ครั้ง (sync — เรียกผ่าน threadpool) · พังคืนค่าว่าง ไม่ทำให้งานหลักพัง"""
    if not gee_analysis._gee_ready:
        return {"area_code": None, "area_name": None}
    ee = gee_analysis.ee
    try:
        f = ee.FeatureCollection(ADM2_ASSET).filterBounds(ee.Geometry.Point([lng, lat])).first()
        name = ee.Algorithms.If(f, ee.Feature(f).get("shapeName"), None).getInfo()
    except Exception as e:
        logger.warning(f"area lookup failed ({type(e).__name__})")
        return {"area_code": None, "area_name": None}
    return from_geo_name(name)
