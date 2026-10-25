"""
province.py — แดชบอร์ดระดับจังหวัด + สถิติการใช้งาน (Phase 2e) — ฟังก์ชันล้วน ทดสอบได้

หลักข้อมูลส่วนบุคคล (docs/data-governance.md):
  - แสดงเป็นตัวเลขรวมรายอำเภอเท่านั้น ไม่มีชื่อ/พิกัดแปลง/LINE userId
  - ไฟล์ส่งหน่วยงาน (export): อำเภอที่มีเกษตรกรน้อยกว่า SUPPRESS_BELOW คน ซ่อนตัวเลขเป็น "<3"
    กันการระบุตัวบุคคลจากตัวเลขเล็ก ๆ (หน้าแดชบอร์ดของผู้ดูแลแสดงตัวเลขจริง)
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import areas
import boundary
import crops

SUPPRESS_BELOW = 3
OUTSIDE = "นอกพื้นที่ที่กำหนด"
MAX_PLAUSIBLE_RAI = 1000   # เพดานเดียวกับแอป (PLOT_MAX_AREA_RAI) — ใหญ่กว่านี้คือขอบเขตวาดผิด ไม่นับพื้นที่


def plot_area_rai(p: dict) -> float:
    if p.get("area_rai"):
        return float(p["area_rai"])
    return boundary.polygon_area_rai(p.get("polygon")) or 0.0


def counted_area(p: dict) -> float | None:
    """พื้นที่ที่นับในสถิติ — None = ใหญ่ผิดปกติ (วาดผิด) ไม่นับ"""
    a = plot_area_rai(p)
    return None if a > MAX_PLAUSIBLE_RAI else a


def district_summary(plots: list[dict], labels: dict[int, str | None] | None = None) -> dict:
    """
    plots = แปลงที่ใช้งานอยู่ (มี area_code, user_id, crop_ids, plot_type, polygon/area_rai)
    labels = {plot_id: ป้ายสถานะเทียบปกติของแปลงเอง เช่น "ชื้นกว่าปกติ"} (ไม่บังคับ)
    """
    labels = labels or {}
    by = defaultdict(lambda: {"plots": 0, "farmers": set(), "area_rai": 0.0, "crops": Counter(),
                              "plot_types": Counter(), "status": Counter(), "with_boundary": 0})
    skipped_area = 0
    for p in plots:
        key = p.get("area_code") or OUTSIDE
        b = by[key]
        b["plots"] += 1
        b["farmers"].add(p.get("user_id"))
        area = counted_area(p)
        if area is None:
            skipped_area += 1
        else:
            b["area_rai"] += area
        b["with_boundary"] += 1 if p.get("polygon") and len(p["polygon"]) >= 3 else 0
        for c in p.get("crop_ids") or ["(ยังไม่ระบุ)"]:
            b["crops"][c] += 1
        b["plot_types"][p.get("plot_type") or "(ยังไม่ระบุ)"] += 1
        b["status"][labels.get(p["id"]) or "ยังไม่มีข้อมูล"] += 1
    crop_names = crops.by_id()
    rows = []
    for d in areas.districts():
        rows.append(_row(d["code"], d["name_th"], by.pop(d["code"], None), crop_names, d))
    if OUTSIDE in by:
        rows.append(_row(None, OUTSIDE, by.pop(OUTSIDE), crop_names, None))
    totals = {"plots": sum(r["plots"] for r in rows), "farmers": len({p.get("user_id") for p in plots}),
              "area_rai": round(sum(r["area_rai"] for r in rows), 1),
              "crops": dict(sum((Counter(r["crops"]) for r in rows), Counter()).most_common()),
              "status": dict(sum((Counter(r["status"]) for r in rows), Counter())),
              "plots_area_not_counted": skipped_area}
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "districts": rows, "totals": totals}


def _row(code, name_th, b, crop_names, d) -> dict:
    b = b or {"plots": 0, "farmers": set(), "area_rai": 0.0, "crops": Counter(), "plot_types": Counter(),
              "status": Counter(), "with_boundary": 0}
    return {"code": code, "name_th": name_th, "plots": b["plots"], "farmers": len(b["farmers"]),
            "area_rai": round(b["area_rai"], 1), "with_boundary": b["with_boundary"],
            "crops": {crop_names.get(k, {}).get("name_th", k): v for k, v in b["crops"].most_common()},
            "plot_types": {crops.PLOT_TYPE_LABELS_TH.get(k, k): v for k, v in b["plot_types"].most_common()},
            "status": dict(b["status"]),
            "lat": d.get("lat") if d else None, "lng": d.get("lng") if d else None}


def suppressed(rows: list[dict]) -> list[dict]:
    """สำหรับส่งหน่วยงาน: อำเภอที่มีเกษตรกร 1–2 คน ซ่อนตัวเลขทุกช่อง (0 คนแสดง 0 ได้)"""
    out = []
    for r in rows:
        if 0 < r["farmers"] < SUPPRESS_BELOW:
            hide = f"<{SUPPRESS_BELOW}"
            out.append({**r, "plots": hide, "farmers": hide, "area_rai": hide, "with_boundary": hide,
                        "crops": {}, "plot_types": {}, "status": {}, "suppressed": True})
        else:
            out.append({**r, "suppressed": False})
    return out


def _week_start(ts: str) -> str:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=7))).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def weekly_usage(events: list[dict], users: list[dict], weeks: int, today: datetime | None = None) -> list[dict]:
    """
    สถิติรายสัปดาห์ (จันทร์–อาทิตย์ เวลาไทย): ผู้ใช้ใหม่ / ผู้ใช้ที่ใช้งาน / ผู้ใช้ประจำ (ใช้ ≥ 2 สัปดาห์ใน 4 สัปดาห์ล่าสุด)
    และจำนวนเหตุการณ์หลัก — events = [{"user_id","name","created_at"}], users = [{"user_id","created_at"}]
    """
    today = today or datetime.now(timezone.utc)
    start = (today.astimezone(timezone(timedelta(hours=7))).date())
    start = start - timedelta(days=start.weekday()) - timedelta(weeks=weeks - 1)
    keys = [(start + timedelta(weeks=i)).isoformat() for i in range(weeks)]
    active: dict[str, set] = defaultdict(set)
    counts: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        wk = _week_start(e["created_at"])
        if e.get("user_id"):
            active[wk].add(e["user_id"])
        counts[wk][e["name"]] += 1
    new = Counter(_week_start(u["created_at"]) for u in users if u.get("created_at"))
    out = []
    for i, wk in enumerate(keys):
        window = keys[max(0, i - 3): i + 1]
        seen = Counter(u for w in window for u in active.get(w, set()))
        out.append({"week_start": wk, "new_users": new.get(wk, 0), "active_users": len(active.get(wk, set())),
                    "returning_users": sum(1 for u in active.get(wk, set()) if seen[u] >= 2),
                    "plots_created": counts[wk].get("plot_create", 0), "analyses": counts[wk].get("analyze", 0),
                    "recommendation_views": counts[wk].get("view_recommendation", 0),
                    "reports": counts[wk].get("create_report", 0), "photos": counts[wk].get("photo_upload", 0),
                    "validations": counts[wk].get("field_validation", 0), "app_opens": counts[wk].get("app_open", 0)})
    return out
