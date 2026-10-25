"""
recommendations.py — คำแนะนำ "แก้อย่างไร" (Phase 1d)

กติกา spec ข้อ 2.4: คำแนะนำต้องมาจากตารางที่คนเขียน/ตรวจเท่านั้น (ห้าม AI แต่งสด) และบอกสถานะเสมอ
  draft    = "ร่าง — รอเจ้าหน้าที่เกษตรตรวจ"
  reviewed = "ตรวจแล้วโดย …"
  retired  = เลิกใช้ (ไม่แสดง)

ตัวจริงอยู่ในตาราง recommendation_rules (แก้/อนุมัติจากหน้า admin /dashboard/recommendations.html)
ไฟล์ config/recommendation_rules_seed.json = ร่างตั้งต้น ใช้ seed DB และเป็นค่าสำรองเมื่อไม่มี DB

การเลือกกฎ (ฟังก์ชันล้วน ทดสอบได้):
  - กฎของพืชนั้นโดยตรง > กฎของกลุ่มพืช (group:vegetable) > กฎทั่วไป (*)
  - สวนผสม: รวมคำแนะนำของทุกพืช ตัดข้อที่ซ้ำ ความรุนแรงตามพืชที่อ่อนไหวที่สุด
  - ปัญหาฝั่งชื้น ปรับความรุนแรงตาม waterlogging_sensitivity ของพืช (high +1 ระดับ, low −1 ระดับ)
  - สวนยกร่อง: ไม่ให้คำแนะนำเรื่องจุดชื้น/จุดน้ำรวม/ทางน้ำไหล (น้ำในร่อง + ภาพ 10 ม. แยกร่องไม่ได้)
"""

import json
import logging
import time
from functools import lru_cache
from pathlib import Path

import crops
# เกณฑ์ใบเขียวลดลง — ใช้ค่าเดียวกับ rule_engine.compute_risk_level (ไม่ตั้งเกณฑ์ใหม่เอง)
from rule_engine import NDVI_DECLINE_THRESHOLD, NDVI_SEVERE_THRESHOLD

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent / "config" / "recommendation_rules_seed.json"

PROBLEMS_TH = {
    "wet_spot":          "จุดชื้นกว่าส่วนอื่นของแปลง",
    "water_collect":     "จุดที่น้ำไหลมารวม",
    "dry_spot":          "จุดแห้งกว่าส่วนอื่นของแปลง",
    "uneven_moisture":   "ความชื้นในแปลงไม่เท่ากัน",
    "wetter_than_usual": "ชื้นกว่าปกติของแปลงนี้",
    "drier_than_usual":  "แห้งกว่าปกติของแปลงนี้",
    "flow_through_rows": "ทางน้ำไหลผ่านแนวปลูก",
    "bare_soil":         "หน้าดินเปิดโล่ง",
    "leaf_decline":      "ใบเขียวลดลง",
}
SEVERITIES = ("low", "medium", "high")
SEVERITY_TH = {"low": "เล็กน้อย", "medium": "ควรดู", "high": "ควรดูโดยเร็ว"}
STATUSES = ("draft", "reviewed", "retired")
WET_PROBLEMS = {"wet_spot", "water_collect", "wetter_than_usual"}
SKIP_FOR_RAISED_BED = {"wet_spot", "water_collect", "flow_through_rows"}
DRAFT_LABEL_TH = "ร่าง — รอเจ้าหน้าที่เกษตรตรวจ"


@lru_cache(maxsize=1)
def seed_rules() -> tuple[dict, ...]:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    rules = []
    for r in data["rules"]:
        assert r["problem"] in PROBLEMS_TH and r["severity"] in SEVERITIES, r["key"]
        rules.append({**r, "id": r["key"], "status": "draft", "reviewed_by": None, "reviewed_at": None,
                      "version": 1})
    return tuple(rules)


# ── แคชกฎจาก DB (หน้า admin แก้แล้วเรียก invalidate) ─────────────
_CACHE_TTL_S = 300
_cache: dict = {"rules": None, "at": 0.0}


def invalidate() -> None:
    _cache["rules"], _cache["at"] = None, 0.0


async def get_rules() -> list[dict]:
    if _cache["rules"] is not None and time.monotonic() - _cache["at"] < _CACHE_TTL_S:
        return _cache["rules"]
    from database import list_recommendation_rules
    rows = None
    try:
        rows = await list_recommendation_rules()
    except Exception as e:
        logger.warning(f"load recommendation rules failed ({type(e).__name__}) — using seed")
    rules = rows if rows else list(seed_rules())
    _cache["rules"], _cache["at"] = rules, time.monotonic()
    return rules


# ── เลือกกฎ ──────────────────────────────────────────────
def _shift(severity: str, n: int) -> str:
    i = min(max(SEVERITIES.index(severity) + n, 0), len(SEVERITIES) - 1)
    return SEVERITIES[i]


def sensitivity_shift(crop_ids: list[str] | None, problem: str) -> int:
    """ปัญหาฝั่งชื้น: พืชอ่อนไหวน้ำขัง (high) +1 ระดับ, ทนน้ำขัง (low ทุกชนิด) −1 · ยังไม่ระบุ = ไม่ปรับ"""
    if problem not in WET_PROBLEMS or not crop_ids:
        return 0
    known = crops.by_id()
    levels = [known[c].get("waterlogging_sensitivity") for c in crop_ids if c in known]
    if "high" in levels:
        return 1
    if levels and all(lv == "low" for lv in levels):
        return -1
    return 0


def _crop_keys(crop_id: str | None) -> list[str]:
    """ลำดับความเฉพาะเจาะจง: พืชนั้น > กลุ่มพืช > ทั่วไป"""
    if not crop_id:
        return ["*"]
    c = crops.by_id().get(crop_id)
    keys = [crop_id]
    if c and c.get("group"):
        keys.append(f"group:{c['group']}")
    return keys + ["*"]


def _pick_for_crop(rules: list[dict], crop_id: str | None, problem: str, severity: str) -> dict | None:
    live = [r for r in rules if r.get("status") != "retired" and r["problem"] == problem]
    # ระดับตรงก่อน แล้วค่อยระดับใกล้เคียง (ต่ำกว่าก่อน) — มีกฎระดับเดียวก็ยังใช้ได้
    idx = SEVERITIES.index(severity)
    order = sorted(SEVERITIES, key=lambda s: (abs(SEVERITIES.index(s) - idx), SEVERITIES.index(s) > idx))
    for sev in order:
        for key in _crop_keys(crop_id):
            for r in live:
                if r["severity"] == sev and r["crop"] == key:
                    return r
    return None


def recommend(rules: list[dict], crop_ids: list[str] | None, plot_type: str | None,
              problem: str, severity: str) -> dict | None:
    """คำแนะนำของปัญหาหนึ่งในแปลงหนึ่ง (รวมทุกพืชในแปลง) — None = ไม่มีกฎ หรือไม่ใช้กับแปลงประเภทนี้"""
    if problem not in PROBLEMS_TH or severity not in SEVERITIES:
        return None
    if plot_type == "raised_bed" and problem in SKIP_FOR_RAISED_BED:
        return None
    sev = _shift(severity, sensitivity_shift(crop_ids, problem))
    picked: list[dict] = []
    for cid in (crop_ids or [None]):
        r = _pick_for_crop(rules, cid, problem, sev)
        if r and all(r["id"] != p["id"] for p in picked):
            picked.append(r)
    if not picked:
        return None
    # กฎที่เฉพาะเจาะจงที่สุดขึ้นก่อน (ชื่อเรื่อง/เหตุผลใช้ของข้อแรก)
    picked.sort(key=lambda r: 0 if r["crop"] not in ("*",) and not r["crop"].startswith("group:")
                else (1 if r["crop"].startswith("group:") else 2))
    actions: list[str] = []
    for r in picked:
        for a in r.get("actions_th") or []:
            if a not in actions:
                actions.append(a)
    reviewed = all(r.get("status") == "reviewed" for r in picked)
    reviewers = sorted({r["reviewed_by"] for r in picked if r.get("reviewed_by")})
    return {
        "problem": problem, "problem_th": PROBLEMS_TH[problem],
        "severity": sev, "severity_th": SEVERITY_TH[sev],
        "title": picked[0]["title_th"], "reason": picked[0].get("reason_th") or "",
        "actions": actions,
        "status": "reviewed" if reviewed else "draft",
        "status_th": f"ตรวจแล้วโดย {', '.join(reviewers)}" if reviewed and reviewers else DRAFT_LABEL_TH,
        "rule_ids": [r["id"] for r in picked],
    }


# ── หาปัญหาจากข้อมูลที่มีอยู่ ────────────────────────────────
def plot_problems(signals: dict | None) -> list[tuple[str, str]]:
    """
    ปัญหาระดับแปลงจากผลวิเคราะห์ล่าสุด:
      risk_v2 (เทียบกับปกติของแปลงเอง) → ชื้น/แห้งกว่าปกติ · ndvi_change → ใบเขียวลดลง
      topsoil_risk_level → หน้าดินเปิดโล่ง
    """
    s = signals or {}
    out: list[tuple[str, str]] = []
    rv = s.get("risk_v2") or {}
    if rv.get("status") in ("medium", "high"):
        out.append(("wetter_than_usual", rv["status"]))
    else:
        dry = rv.get("dry_status")
        if dry is None and rv.get("status") == "ok" and rv.get("z") is not None:
            dry = "high" if rv["z"] <= -2 else ("medium" if rv["z"] <= -1 else "ok")
        if dry in ("medium", "high"):
            out.append(("drier_than_usual", dry))
    ndvi_change = s.get("ndvi_change")
    if ndvi_change is not None:
        if ndvi_change < NDVI_SEVERE_THRESHOLD:
            out.append(("leaf_decline", "high"))
        elif ndvi_change < NDVI_DECLINE_THRESHOLD:
            out.append(("leaf_decline", "medium"))
    if s.get("topsoil_risk_level") in ("medium", "high"):
        out.append(("bare_soil", s["topsoil_risk_level"]))
    return out


POINT_PROBLEMS = (("wet_spot", "medium"), ("wet_spot", "high"), ("dry_spot", "medium"),
                  ("dry_spot", "high"), ("water_collect", "medium"), ("flow_through_rows", "medium"))


def for_plot(rules: list[dict], crop_ids: list[str] | None, plot_type: str | None,
             signals: dict | None) -> dict:
    """
    ชุดคำแนะนำของแปลง:
      plot   = ปัญหาระดับแปลงที่พบตอนนี้ (เรียงรุนแรงก่อน)
      points = คำแนะนำสำหรับแตะจุดบนแผนที่ {problem: {severity: rec}} (หน้าแอปเลือกตามจุดที่แตะ)
    """
    plot = [rec for p, sev in plot_problems(signals)
            if (rec := recommend(rules, crop_ids, plot_type, p, sev))]
    plot.sort(key=lambda r: -SEVERITIES.index(r["severity"]))
    points: dict[str, dict] = {}
    for p, sev in POINT_PROBLEMS:
        rec = recommend(rules, crop_ids, plot_type, p, sev)
        if rec:
            points.setdefault(p, {})[sev] = rec
    return {"plot": plot, "points": points, "crop_ids": crop_ids or [], "plot_type": plot_type,
            "note": DRAFT_LABEL_TH if any(r["status"] == "draft" for r in plot) else None}


# ── ตรวจข้อมูลจากหน้า admin ──────────────────────────────
def validate_rule(body: dict) -> dict:
    """คืน dict ที่สะอาดแล้ว — ValueError ถ้าข้อมูลไม่ถูกต้อง"""
    crop = (body.get("crop") or "*").strip()
    if crop != "*" and not crop.startswith("group:") and crop not in crops.by_id():
        raise ValueError(f"ไม่รู้จักพืช: {crop}")
    if crop.startswith("group:") and crop[6:] not in {c["group"] for c in crops.all_crops()}:
        raise ValueError(f"ไม่รู้จักกลุ่มพืช: {crop}")
    if body.get("problem") not in PROBLEMS_TH:
        raise ValueError("ประเภทปัญหาไม่ถูกต้อง")
    if body.get("severity") not in SEVERITIES:
        raise ValueError("ระดับความรุนแรงไม่ถูกต้อง")
    title = (body.get("title_th") or "").strip()
    actions = [a.strip() for a in body.get("actions_th") or [] if a and a.strip()]
    if not title or not actions:
        raise ValueError("ต้องมีหัวข้อ และวิธีแก้อย่างน้อย 1 ข้อ")
    if len(title) > 200 or len(actions) > 10 or any(len(a) > 300 for a in actions):
        raise ValueError("ข้อความยาวเกินไป")
    return {"crop": crop, "problem": body["problem"], "severity": body["severity"], "title_th": title,
            "reason_th": (body.get("reason_th") or "").strip()[:500], "actions_th": actions,
            "source": (body.get("source") or "").strip()[:300], "removed": (body.get("removed") or "").strip()[:300]}
