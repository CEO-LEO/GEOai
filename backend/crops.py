"""
crops.py — รายการพืชจาก config/crops.json (spec ข้อ 4.3: config ได้ ไม่ hardcode)
แก้รายการพืชได้ที่ไฟล์ JSON โดยไม่ต้องแก้โค้ด (Phase 1d จะย้ายไปแก้ผ่านหน้า admin)
"""

import json
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "crops.json"
PLOT_TYPES = ("flat", "raised_bed", "vegetable_bed")
PLOT_TYPE_LABELS_TH = {"flat": "แปลงเรียบ", "raised_bed": "สวนยกร่อง", "vegetable_bed": "ยกแปลงผัก"}
MAX_CROPS_PER_PLOT = 10


@lru_cache(maxsize=1)
def load() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["crops"]]
    assert len(ids) == len(set(ids)), "duplicate crop id in crops.json"
    return data


def all_crops() -> list[dict]:
    return load()["crops"]


def by_id() -> dict[str, dict]:
    return {c["id"]: c for c in all_crops()}


def validate_ids(crop_ids: list[str] | None) -> list[str]:
    """คืน list ที่ไม่ซ้ำ เรียงตามลำดับเดิม — ValueError ถ้ามี id ที่ไม่รู้จักหรือเกินจำนวน"""
    if not crop_ids:
        return []
    known = by_id()
    out: list[str] = []
    for cid in crop_ids:
        if cid not in known:
            raise ValueError(f"ไม่รู้จักชนิดพืช: {cid}")
        if cid not in out:
            out.append(cid)
    if len(out) > MAX_CROPS_PER_PLOT:
        raise ValueError(f"เลือกพืชได้ไม่เกิน {MAX_CROPS_PER_PLOT} ชนิดต่อแปลง")
    return out


def groups(crop_ids: list[str] | None) -> set[str]:
    known = by_id()
    return {known[c]["group"] for c in crop_ids or [] if c in known}


def is_vegetable_context(crop_ids: list[str] | None, plot_type: str | None) -> bool:
    """แปลงผัก/ยกแปลงผัก หรือสวนผสมที่มีผัก — ห้ามเตือนเรื่องต้นไม้น้อย (เจ้าของสั่ง 2026-10-24)"""
    return plot_type == "vegetable_bed" or "vegetable" in groups(crop_ids)


def is_fruit_tree_context(crop_ids: list[str] | None) -> bool:
    return "fruit_tree" in groups(crop_ids)


def names_th(crop_ids: list[str] | None) -> list[str]:
    known = by_id()
    return [known[c]["name_th"] for c in crop_ids or [] if c in known]
