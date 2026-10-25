"""
features.py — สกัด feature ต่อแปลงจาก GEE (sync — ผู้เรียกห่อด้วย threadpool) · ผลแคชในตาราง crop_features
GEE 1 ครั้งต่อแปลง (ทุกแหล่งของชุด feature รวมใน ee.Dictionary เดียว)
"""

import hashlib
import json
import math
from datetime import datetime, timezone

import gee_analysis

from . import sources

_emb_year_cache: dict[str, int] = {}


def geom_hash(lat: float, lng: float, polygon: list | None) -> str:
    """ขอบเขตเปลี่ยน → hash เปลี่ยน → คำนวณ feature/ทายใหม่"""
    g = [[round(x, 6), round(y, 6)] for x, y in polygon] if polygon and len(polygon) >= 3 else [round(lat, 6), round(lng, 6)]
    return hashlib.sha1(json.dumps(g).encode()).hexdigest()[:16]


def geometry(ee, lat: float, lng: float, polygon: list | None):
    if polygon and len(polygon) >= 3:
        return ee.Geometry.Polygon([polygon])
    return ee.Geometry.Point([lng, lat]).buffer(gee_analysis.PLOT_BUFFER_M)


def latest_embedding_year() -> int:
    """ปีล่าสุดที่จบแล้วและมีในแคตตาล็อก embedding (ตรวจครั้งเดียวต่อ process)"""
    if "y" not in _emb_year_cache:
        ee = gee_analysis.ee
        t = ee.ImageCollection(sources.SatelliteEmbedding.COLLECTION).aggregate_max("system:time_start")
        year = datetime.fromtimestamp(gee_analysis._retry_gee(t.getInfo) / 1000, timezone.utc).year
        _emb_year_cache["y"] = min(year, datetime.now(timezone.utc).year - 1)
    return _emb_year_cache["y"]


def to_vector(raw_by_source: dict[str, dict], feature_set: str = sources.DEFAULT_FEATURE_SET) -> list[float | None]:
    """ค่าดิบจาก GEE ต่อแหล่ง → เวกเตอร์ตามลำดับชื่อ feature (ค่าที่ไม่มี = None) — ฟังก์ชันล้วน"""
    out: list[float | None] = []
    for src in sources.FEATURE_SETS[feature_set]:
        f = src.to_features(raw_by_source.get(src.name) or {})
        for n in src.names:
            v = f.get(n)
            out.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
    return out


def extract(lat: float, lng: float, polygon: list | None, year: int | None = None,
            feature_set: str = sources.DEFAULT_FEATURE_SET) -> dict:
    """{"year", "geom_hash", "vals", "meta"} — vals มี None ได้ (เดือน/แหล่งที่ไม่มีภาพ)"""
    ee = gee_analysis.ee
    year = year or latest_embedding_year()
    geom = geometry(ee, lat, lng, polygon)
    d = ee.Dictionary({s.name: s.image(ee, geom, year).reduceRegion(ee.Reducer.mean(), geom, s.scale, maxPixels=1e9)
                       for s in sources.FEATURE_SETS[feature_set]})
    raw = gee_analysis._retry_gee(d.getInfo)
    vals = to_vector(raw, feature_set)
    names = sources.feature_names(feature_set)
    dw = raw.get("dw") or {}
    return {"year": year, "geom_hash": geom_hash(lat, lng, polygon), "vals": vals,
            "meta": {"tree_fraction": None if dw.get("dw_trees") is None else round(dw["dw_trees"], 3),
                     "missing": [n for n, v in zip(names, vals) if v is None][:20],
                     "embedding_missing": all(v is None for v in vals[:64])}}
