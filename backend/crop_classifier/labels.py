"""
labels.py — ประกอบชุดฝึก (spec 7.7) — ฟังก์ชันล้วน ไม่เรียก DB

ที่มาของ label และน้ำหนัก (docs/v2/decisions.md):
  เจ้าหน้าที่ยืนยัน (ใช่/เลือกพืชอื่น)        3.0
  เจ้าของยืนยัน หรือเลือกพืชตอนสร้างแปลง     1.0  (+0.5 ถ้ามีรูปที่มีพิกัดในแปลง)
  ทะเบียนเกษตรกร (reference_labels)          1.0
  เจ้าหน้าที่สำรวจจุดในแปลง (officer_survey)  2.0  (เห็นพืชเอง แต่เป็นจุดเดียว ไม่มีขอบเขต)
คำตอบล่าสุดของแต่ละบทบาทชนะ · เจ้าหน้าที่ตอบ "ไม่ใช่"/"ไม่แน่ใจ" ล่าสุด → ไม่ใช้แปลงนั้น (ขัดกัน)
ใช้เฉพาะแปลงของเจ้าของที่ยินยอมข้อ "ใช้ปรับปรุงระบบ" (users.consent_photos_research)
"""

from collections import Counter, defaultdict

W_OFFICER, W_OWNER, W_PHOTO_BONUS, W_REFERENCE, W_SURVEY = 3.0, 1.0, 0.5, 1.0, 2.0
SURVEY_SOURCE = "officer_survey"
MIXED, OTHER = "mixed", "other"
MIN_PLOTS_PER_CLASS = 10
MIN_DISTRICTS_PER_CLASS = 2


def crop_class(crop_ids: list[str] | None, crop_index: dict[str, dict]) -> str | None:
    """ชุดพืชของแปลง → คลาสเดียว: พืชเดียว = id พืช · ไม้ผลหลายชนิด = สวนผสม · ไม่มีพืช = None"""
    ids = [c for c in dict.fromkeys(crop_ids or []) if c in crop_index]
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    fruit = [c for c in ids if crop_index[c].get("group") == "fruit_tree"]
    if len(fruit) >= 2:
        return MIXED
    return fruit[0] if fruit else ids[0]


def plot_label(plot: dict, plot_crop_ids: list[str], feedback: list[dict], has_geo_photo: bool,
               crop_index: dict[str, dict]) -> dict | None:
    """label ของแปลงหนึ่ง {"cls", "weight", "basis"} หรือ None (ไม่มี/ขัดกัน)"""
    latest: dict[str, dict] = {}
    for f in sorted(feedback, key=lambda f: f.get("created_at") or ""):
        role = "officer" if f.get("role") in ("officer", "admin") else "owner"
        latest[role] = f
    off = latest.get("officer")
    if off:
        if off["answer"] in ("no", "unsure"):
            return None
        cls = crop_class(off.get("crop_ids") or plot_crop_ids, crop_index)
        return {"cls": cls, "weight": W_OFFICER, "basis": "officer"} if cls else None
    own = latest.get("owner")
    if own and own["answer"] in ("no", "unsure") and not own.get("crop_ids"):
        return None
    ids = (own.get("crop_ids") if own and own.get("crop_ids") else None) or plot_crop_ids
    cls = crop_class(ids, crop_index)
    if not cls:
        return None
    return {"cls": cls, "weight": W_OWNER + (W_PHOTO_BONUS if has_geo_photo else 0.0),
            "basis": "owner_confirmed" if own else "owner_selected"}


def fold_rare(rows: list[dict], min_plots: int = MIN_PLOTS_PER_CLASS,
              min_districts: int = MIN_DISTRICTS_PER_CLASS) -> tuple[list[dict], dict]:
    """คลาสที่ตัวอย่างน้อย/อยู่อำเภอเดียว → "อื่น ๆ" (กันโมเดลจำพื้นที่แทนพืช) · คืน (rows ใหม่, สรุปต่อคลาส)"""
    n, districts = Counter(), defaultdict(set)
    for r in rows:
        n[r["cls"]] += 1
        districts[r["cls"]].add(r.get("group"))
    keep = {c for c in n if n[c] >= min_plots and len(districts[c]) >= min_districts and c != OTHER}
    out = [{**r, "cls": r["cls"] if r["cls"] in keep else OTHER, "orig_cls": r["cls"]} for r in rows]
    summary = {c: {"plots": n[c], "districts": len(districts[c] - {None}), "kept": c in keep} for c in sorted(n)}
    return out, summary


def spatial_group(area_code: str | None, lat: float, lng: float, block_deg: float = 0.05) -> str:
    """กลุ่มสำหรับแบ่ง train/test: อำเภอ ถ้ามี ไม่งั้นช่องกริด ~5 กม."""
    if area_code:
        return f"d{area_code}"
    return f"g{int(lat // block_deg)}_{int(lng // block_deg)}"
