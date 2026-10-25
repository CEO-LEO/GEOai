"""
service.py — interface ของ crop_classifier ที่แอปหลักเรียก (spec 7.1)

  guess(plot)                 ผลทายของแปลง (แคชใน crop_predictions — ไม่คำนวณใหม่ถ้าโมเดล/ขอบเขตเดิม)
  ensure_features(plot)       feature ของแปลง (แคชใน crop_features)
  batch_features(limit)       สกัด feature ชุดฝึก (แปลงที่ยินยอม + แปลงอ้างอิง) ที่ยังไม่มี/ขอบเขตเปลี่ยน
  build_dataset()             ชุดฝึก + สรุป + sha256 (ไม่มีพิกัด/ผู้ใช้ในผลลัพธ์)
  train(created_by)           ประเมินแบบ spatial + เทรนทั้งชุด + เก็บโมเดล (ยังไม่เปิดใช้)
  activate(version)           เปิดใช้โมเดลที่ผ่านเกณฑ์เท่านั้น

การทายแสดงผู้ใช้เมื่อ CROP_GUESS_ENABLED=1 และมีโมเดลที่เปิดใช้ (ผ่านเกณฑ์) — ไม่งั้นตอบ status "collecting"
(แอปถาม "แปลงนี้ปลูกอะไร" แทน เพื่อเก็บ label) · ไลเซนส์: docs/v2/license-review.md
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

import crops as crops_mod
import gee_analysis

from . import evaluate, features, labels, model, sources, store

logger = logging.getLogger(__name__)

FEATURE_TIMEOUT_S = 90
ATTRIBUTION_TH = ("ทายจากภาพดาวเทียม: AlphaEarth Satellite Embedding (Google/Google DeepMind, CC BY 4.0), "
                  "Copernicus Sentinel-1/2, Dynamic World")


def guess_enabled() -> bool:
    return os.environ.get("CROP_GUESS_ENABLED", "") == "1"


def _with_names(top: list[dict]) -> list[dict]:
    known = crops_mod.by_id()
    names = {labels.MIXED: "สวนผสม", labels.OTHER: "อื่น ๆ"}
    return [{**t, "name_th": known.get(t["crop_id"], {}).get("name_th") or names.get(t["crop_id"], t["crop_id"])}
            for t in top]


async def ensure_features(plot_id: int | None, lat: float, lng: float, polygon: list | None,
                          ref_id: int | None = None, year: int | None = None) -> dict | None:
    """feature ของแปลง/แปลงอ้างอิง — ใช้แคชถ้าขอบเขตเดิม · None ถ้า GEE ใช้ไม่ได้"""
    if not gee_analysis._gee_ready:
        return None
    fs = sources.DEFAULT_FEATURE_SET
    year = year or await run_in_threadpool(features.latest_embedding_year)
    gh = features.geom_hash(lat, lng, polygon)
    if plot_id is not None:
        hit = await store.get_plot_features(plot_id, fs, year)
        if hit and hit["geom_hash"] == gh:
            return hit
    out = await asyncio.wait_for(run_in_threadpool(features.extract, lat, lng, polygon, year, fs),
                                 timeout=FEATURE_TIMEOUT_S)
    row = {"plot_id": plot_id, "ref_id": ref_id, "feature_set": fs, "year": year,
           "geom_hash": out["geom_hash"], "vals": out["vals"], "meta": out["meta"]}
    await store.upsert_features(row)
    return row


async def guess(plot: dict) -> dict:
    """ผลทายสำหรับหน้าแอป — {"status": collecting|ok|uncertain|not_orchard|unavailable, "top": [...], ...}"""
    if not guess_enabled():
        return {"status": "collecting"}
    m = await store.get_active_model()
    if not m or not m.get("passes_gate"):
        return {"status": "collecting"}
    gh = features.geom_hash(plot["lat"], plot["lng"], plot.get("polygon"))
    prev = await store.latest_prediction(plot["id"])
    if prev and prev["model_version"] == m["version"] and prev["geom_hash"] == gh:
        return _public(prev)
    try:
        f = await ensure_features(plot["id"], plot["lat"], plot["lng"], plot.get("polygon"))
    except Exception as e:      # noqa: BLE001 — GEE ล่ม/ช้า ไม่ให้หน้าแปลงพัง
        logger.error(f"crop features failed plot={plot['id']}: {type(e).__name__}: {e}")
        f = None
    if not f or (f.get("meta") or {}).get("embedding_missing"):
        return {"status": "unavailable"}
    probs = model.predict_proba(m["params"], [f["vals"]])[0]
    d = model.decide(m["params"]["classes"], probs, (f.get("meta") or {}).get("tree_fraction"), crops_mod.by_id())
    row = await store.insert_prediction({"plot_id": plot["id"], "model_version": m["version"], "geom_hash": gh,
                                         "status": d["status"], "top": d["top"], "confidence": d["confidence"]})
    return _public(row or {**d, "model_version": m["version"]})


def _public(p: dict) -> dict:
    return {"status": p["status"], "top": _with_names(p.get("top") or []), "confidence": p.get("confidence"),
            "prediction_id": p.get("id"), "model_version": p.get("model_version"), "attribution_th": ATTRIBUTION_TH}


async def batch_features(limit: int = 30) -> dict:
    """สกัด feature ให้แปลงในชุดฝึกที่ยังไม่มี (ทีละไม่เกิน limit — เรียกซ้ำได้)"""
    if not gee_analysis._gee_ready:
        return {"status": "gee_unavailable"}
    year = await run_in_threadpool(features.latest_embedding_year)
    inp = await store.training_inputs()
    have = {(r.get("plot_id"), r.get("ref_id")): r["geom_hash"]
            for r in await store.list_features(sources.DEFAULT_FEATURE_SET, year)}
    todo = []
    for p in inp["plots"]:
        if have.get((p["id"], None)) != features.geom_hash(p["lat"], p["lng"], p.get("polygon")):
            todo.append((p["id"], None, p))
    for r in inp["references"]:
        if (None, r["id"]) not in have:
            todo.append((None, r["id"], r))
    done, failed = 0, 0
    for plot_id, ref_id, s in todo[:limit]:
        try:
            await ensure_features(plot_id, s["lat"], s["lng"], s.get("polygon"), ref_id=ref_id, year=year)
            done += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.error(f"crop batch features failed plot={plot_id} ref={ref_id}: {type(e).__name__}: {e}")
    return {"status": "ok", "year": year, "computed": done, "failed": failed, "remaining": max(len(todo) - limit, 0)}


async def build_dataset() -> tuple[list[dict], dict]:
    """ชุดฝึก (rows มี vals/cls/weight/group) + สรุปที่ไม่มีพิกัด/ผู้ใช้"""
    year = await run_in_threadpool(features.latest_embedding_year) if gee_analysis._gee_ready else None
    inp = await store.training_inputs()
    feats = {(r.get("plot_id"), r.get("ref_id")): r
             for r in (await store.list_features(sources.DEFAULT_FEATURE_SET, year) if year else [])}
    index = crops_mod.by_id()
    fb_by_plot: dict[int, list[dict]] = {}
    for f in inp["feedback"]:
        fb_by_plot.setdefault(f["plot_id"], []).append(f)
    rows, no_label, no_features = [], 0, 0
    for p in inp["plots"]:
        lab = labels.plot_label(p, p["crop_ids"], fb_by_plot.get(p["id"], []), p["id"] in inp["geo_photo_plots"], index)
        if not lab:
            no_label += 1
            continue
        f = feats.get((p["id"], None))
        if not f or f["geom_hash"] != features.geom_hash(p["lat"], p["lng"], p.get("polygon")):
            no_features += 1
            continue
        rows.append({"key": f"p{p['id']}", "vals": f["vals"], "cls": lab["cls"], "weight": lab["weight"],
                     "basis": lab["basis"], "group": labels.spatial_group(p.get("area_code"), p["lat"], p["lng"])})
    for r in inp["references"]:
        cls = labels.crop_class(r["crop_ids"], index)
        f = feats.get((None, r["id"]))
        if not cls or not f:
            no_features += bool(cls)
            continue
        rows.append({"key": f"r{r['id']}", "vals": f["vals"], "cls": cls, "weight": labels.W_REFERENCE,
                     "basis": "reference", "group": labels.spatial_group(r.get("area_code"), r["lat"], r["lng"])})
    rows, per_class = labels.fold_rare(rows)
    digest = hashlib.sha256(json.dumps(sorted((r["key"], r["cls"], r["weight"], r["vals"]) for r in rows),
                                       default=str).encode()).hexdigest()
    summary = {"feature_set": sources.DEFAULT_FEATURE_SET, "year": year, "n": len(rows), "per_class": per_class,
               "by_basis": _count(r["basis"] for r in rows), "groups": len({r["group"] for r in rows}),
               "consented_plots": len(inp["plots"]), "without_label": no_label, "without_features": no_features,
               "sha256": digest}
    return rows, summary


async def train(created_by: str) -> dict:
    """ประเมิน + เทรน + เก็บ (ไม่เปิดใช้เอง) · ข้อมูลไม่พอ → คืนสรุปโดยไม่สร้างโมเดล"""
    rows, ds = await build_dataset()
    y = [r["cls"] for r in rows]
    if len(set(y)) < 2:
        return {"status": "insufficient", "dataset": ds,
                "message_th": "ยังมีแปลงที่ยืนยันชนิดพืชไม่พอ (ต้องมีอย่างน้อย 2 ชนิด) — ยังไม่สร้างโมเดล"}
    X, w, g = [r["vals"] for r in rows], [r["weight"] for r in rows], [r["group"] for r in rows]
    metrics = await run_in_threadpool(evaluate.spatial_cv, X, y, w, g)
    params = await run_in_threadpool(model.fit_logreg, X, y, w)
    ok, why = evaluate.passes_gate(metrics)
    version = "crop-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    saved = await store.insert_model({"version": version, "algorithm": model.ALGORITHM, "feature_set": ds["feature_set"],
                                      "classes": params["classes"], "params": params, "metrics": metrics,
                                      "dataset": ds, "passes_gate": ok, "created_by": created_by})
    names = {c["id"]: c["name_th"] for c in crops_mod.all_crops()} | {labels.MIXED: "สวนผสม", labels.OTHER: "อื่น ๆ"}
    return {"status": "trained" if saved else "save_failed", "version": version, "passes_gate": ok, "gate_reasons": why,
            "metrics": metrics, "dataset": ds, "report_md": evaluate.report_markdown(version, metrics, ds, names)}


async def activate(version: str | None) -> tuple[bool, str]:
    if version is None:
        return await store.set_active_model(None), "ปิดการทายแล้ว"
    m = await store.get_model(version)
    if not m:
        return False, "ไม่พบโมเดลนี้"
    if not m["passes_gate"]:
        return False, "โมเดลนี้ไม่ผ่านเกณฑ์ประเมิน — เปิดใช้ไม่ได้"
    return await store.set_active_model(version), "เปิดใช้แล้ว"


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out
