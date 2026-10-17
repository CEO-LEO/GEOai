"""
risk_v2.py — วิธีวัดความเสี่ยงแบบใหม่ (โหมดรันคู่ / shadow — ยังไม่แสดงผู้ใช้และไม่ใช้ส่งเตือน)

อนุมัติ 2026-10-17 ให้รันคู่กับระบบเดิม 2–4 สัปดาห์ (ดู docs/v2/risk-level-review.md)
ผลถูกเก็บใน analyses.full_data["risk_v2"] และ ["plot_profile"] (ไม่มีการเปลี่ยน schema)
เทียบกับระบบเดิมได้ที่ GET /admin/risk-shadow

1) สถานะตอนนี้ — "ชื้นกว่าปกติของแปลงนี้เองในช่วงเดียวกันของปี" (Sentinel-1 VV)
   - วงโคจร (relative orbit) เดียวกันทุกภาพ: มุมมองต่างกันทำให้ค่าแปลงเดียวกันต่างกันได้ถึง ±2 dB
     (ตรวจ 2026-10-17: S1C−S1A ต่างกันรายแปลง −1.5 ถึง +2.0 dB แต่ค่ากลางเกือบ 0)
   - ปัจจุบัน = เฉลี่ยภาพใน CURRENT_DAYS วันล่าสุด
   - ค่าปกติ = ภาพในช่วง ±DOY_WINDOW วันของวันเดียวกันในปีก่อน ๆ (≤ BASELINE_YEARS ปี)
     **ถ่วงน้ำหนักทุกปีเท่ากัน** — ตั้งแต่ เม.ย. 2025 มี Sentinel-1C ทำให้จำนวนภาพต่อปีเพิ่มเป็น
     ~2 เท่า ถ้าไม่ถ่วง ค่าปกติจะเอนไปทางปีล่าสุด
   - ต้องมีอย่างน้อย MIN_YEARS ปี ปีละ ≥ MIN_IMAGES_PER_YEAR ภาพ ไม่งั้น "ข้อมูลไม่พอ"
   - z ≥ 1 = ชื้นกว่าปกติ (medium), z ≥ 2 = ชื้นกว่าปกติมาก (high) — ค่าทางสถิติ รอปรับจากผลภาคสนาม
2) ลักษณะประจำแปลง (ไม่ใช้เตือน แสดงเป็นข้อมูลได้) — แก้จุดบอด "แปลงที่น้ำขังทุกปีจะถือว่าปกติ"
   - HAND (ความสูงเหนือทางน้ำใกล้สุด, MERIT Hydro 90 ม.) → ที่ลุ่มต่ำ
   - สัดส่วนต้นไม้ในขอบเขต (ESA WorldCover 2021) → บอกคุณภาพข้อมูล: ขอบเขตที่มีบ้าน/ถนนปน
     ทำให้ค่าเรดาร์สูงผิดปกติ (พบหลายแปลงที่ค่าชื้นมากแต่มีต้นไม้แค่ 2–5%)
   - ชื้นกว่าสวนต้นไม้รอบ 3 กม. ในหน้าฝน (percentile) — คำนวณเฉพาะแปลงที่เป็นต้นไม้ ≥ 60%
"""

import logging
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import gee_analysis

logger = logging.getLogger(__name__)

METHOD = "s1_anomaly_v1"
CURRENT_DAYS = 12
DOY_WINDOW = 30
BASELINE_YEARS = 3
MIN_YEARS = 2
MIN_IMAGES_PER_YEAR = 2
MIN_SD_DB = 0.5
Z_MEDIUM = 1.0
Z_HIGH = 2.0

PROFILE_METHOD = "profile_v1"
TREE_FRACTION_MIN = 0.6
LOW_LYING_VERY_M = 2.0       # HAND ≤ 2 ม. = ลุ่มต่ำมาก (ค่าเริ่มต้น รอยืนยันภาคสนาม)
LOW_LYING_M = 5.0            # HAND ≤ 5 ม. = ลุ่มต่ำ
NEIGHBOR_RADIUS_M = 3000
WETTER_THAN_NEIGHBORS_PCT = 0.9


def _doy_distance(a: datetime, b: datetime) -> int:
    d = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(d, 365 - d)


def compute_wetness_anomaly(series: list[tuple[datetime, float, int | None]],
                            today: datetime) -> dict:
    """
    series = [(เวลา, VV dB, relative orbit), ...] — ฟังก์ชันล้วน (ไม่เรียก GEE) ทดสอบได้
    คืน dict สถานะ: ok / medium / high / no_recent / insufficient
    """
    base = {"method": METHOD}
    obs = [(t, v, o) for t, v, o in series if v is not None and t <= today]
    if not obs:
        return {**base, "status": "insufficient", "reason": "no_images"}

    orbit = Counter(o for _, _, o in obs).most_common(1)[0][0]
    obs = sorted((t, v) for t, v, o in obs if o == orbit)
    last_image = obs[-1][0]

    current = [v for t, v in obs if today - timedelta(days=CURRENT_DAYS) < t <= today]
    by_year: dict[int, list[float]] = defaultdict(list)
    for t, v in obs:
        if t <= today - timedelta(days=300) and t >= today - timedelta(days=365 * BASELINE_YEARS + DOY_WINDOW) \
                and _doy_distance(t, today) <= DOY_WINDOW:
            by_year[t.year].append(v)
    years = {y: vs for y, vs in by_year.items() if len(vs) >= MIN_IMAGES_PER_YEAR}

    common = {**base, "orbit": orbit, "last_image": last_image.date().isoformat(),
              "baseline_years": sorted(years), "baseline_images": sum(len(v) for v in years.values())}
    if len(years) < MIN_YEARS:
        return {**common, "status": "insufficient", "reason": "baseline_years"}
    if not current:
        return {**common, "status": "no_recent"}

    # ถ่วงน้ำหนักปีละเท่ากัน: ค่ากลาง = เฉลี่ยของค่าเฉลี่ยรายปี, ส่วนเบี่ยงเบน = ถ่วงน้ำหนัก 1/n ต่อภาพ
    year_means = {y: statistics.fmean(vs) for y, vs in years.items()}
    mean = statistics.fmean(year_means.values())
    weights, sq = 0.0, 0.0
    for y, vs in years.items():
        w = 1.0 / len(vs)
        for v in vs:
            weights += w
            sq += w * (v - mean) ** 2
    sd = max(MIN_SD_DB, math.sqrt(sq / weights))

    cur = statistics.fmean(current)
    z = (cur - mean) / sd
    status = "high" if z >= Z_HIGH else ("medium" if z >= Z_MEDIUM else "ok")
    return {**common, "status": status, "z": round(z, 2), "current_vv_db": round(cur, 2),
            "baseline_mean_db": round(mean, 2), "baseline_sd_db": round(sd, 2),
            "current_images": len(current)}


def _geometry(lat: float, lng: float, polygon: list | None):
    ee = gee_analysis.ee
    if polygon and len(polygon) >= 3:
        return ee.Geometry.Polygon([polygon])
    return ee.Geometry.Point([lng, lat]).buffer(gee_analysis.PLOT_BUFFER_M)


def get_wetness_anomaly(lat: float, lng: float, polygon: list | None = None,
                        today: datetime | None = None) -> dict:
    """เรียก GEE 1 ครั้ง (อนุกรมเวลา VV ≈ 3 ปี) แล้วคำนวณสถานะ — sync ต้องห่อด้วย threadpool"""
    if not gee_analysis._gee_ready:
        return {"method": METHOD, "status": "unavailable"}
    ee = gee_analysis.ee
    today = today or datetime.now(timezone.utc)
    geom = _geometry(lat, lng, polygon)
    start = today - timedelta(days=365 * BASELINE_YEARS + DOY_WINDOW + 5)
    col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(geom)
           .filterDate(start.strftime("%Y-%m-%d"), (today + timedelta(days=1)).strftime("%Y-%m-%d"))
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")))
    fc = col.map(lambda img: ee.Feature(None, {
        "t": img.date().millis(),
        "vv": img.select("VV").reduceRegion(ee.Reducer.mean(), geom, 10).get("VV"),
        "orb": img.get("relativeOrbitNumber_start"),
    }))
    feats = gee_analysis._retry_gee(fc.getInfo).get("features", [])
    series = [(datetime.fromtimestamp(f["properties"]["t"] / 1000, timezone.utc),
               f["properties"].get("vv"), f["properties"].get("orb")) for f in feats]
    return compute_wetness_anomaly(series, today)


def classify_profile(hand_m: float | None, tree_fraction: float | None,
                     wet_rank: float | None) -> dict:
    """แปลงค่าดิบเป็นป้ายภาษาคน — ฟังก์ชันล้วน ทดสอบได้"""
    flags = []
    if hand_m is not None:
        if hand_m <= LOW_LYING_VERY_M:
            flags.append("low_lying_very")
        elif hand_m <= LOW_LYING_M:
            flags.append("low_lying")
    if tree_fraction is not None and tree_fraction < TREE_FRACTION_MIN:
        flags.append("mixed_land_cover")     # ค่าเรดาร์ของแปลงนี้เชื่อถือได้น้อยลง
    elif wet_rank is not None and wet_rank >= WETTER_THAN_NEIGHBORS_PCT:
        flags.append("wetter_than_neighbors")
    return {"method": PROFILE_METHOD, "hand_m": None if hand_m is None else round(hand_m, 1),
            "tree_fraction": None if tree_fraction is None else round(tree_fraction, 2),
            "wet_rank_vs_trees": None if wet_rank is None else round(wet_rank, 2),
            "flags": flags}


def get_plot_profile(lat: float, lng: float, polygon: list | None = None) -> dict:
    """ลักษณะประจำแปลง (GEE 2 ครั้ง) — ค่าไม่ค่อยเปลี่ยน คำนวณสัปดาห์ละครั้งพอ"""
    if not gee_analysis._gee_ready:
        return {"method": PROFILE_METHOD, "status": "unavailable"}
    ee = gee_analysis.ee
    geom = _geometry(lat, lng, polygon)
    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")).select("VV")
          .filterBounds(geom.buffer(NEIGHBOR_RADIUS_M)))
    year = datetime.now(timezone.utc).year
    wet_filter = ee.Filter.Or(*[ee.Filter.date(f"{y}-06-01", f"{y}-10-31") for y in (year - 2, year - 1)])
    wet = s1.filter(wet_filter).median()
    trees = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").eq(10)
    hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")
    r = gee_analysis._retry_gee(ee.Dictionary({
        "wet": wet.reduceRegion(ee.Reducer.mean(), geom, 10).get("VV"),
        "hand": hand.reduceRegion(ee.Reducer.mean(), geom, 90).get("hnd"),
        "tree": trees.reduceRegion(ee.Reducer.mean(), geom, 10).get("Map"),
    }).getInfo)
    wet_rank = None
    if r.get("wet") is not None and (r.get("tree") or 0) >= TREE_FRACTION_MIN:
        ring = geom.buffer(NEIGHBOR_RADIUS_M).difference(geom.buffer(50))
        wet_rank = gee_analysis._retry_gee(
            wet.lt(r["wet"]).updateMask(trees).reduceRegion(ee.Reducer.mean(), ring, 20, maxPixels=1e8)
            .get("VV").getInfo)
    return classify_profile(r.get("hand"), r.get("tree"), wet_rank)


# ─────────────────────────────────────────────────────────
# สรุปผลรันคู่ (ใช้โดย GET /admin/risk-shadow) — ฟังก์ชันล้วน ทดสอบได้
# rows: [{"plot_id", "created_at", "old": "high|medium|ok", "risk_v2": {...}|None, "profile": {...}|None}]
# ─────────────────────────────────────────────────────────
_LEVELS = ("ok", "medium", "high")


def summarize_shadow(rows: list[dict]) -> dict:
    old_counts, new_counts, cross = Counter(), Counter(), Counter()
    per_plot: dict = defaultdict(lambda: {"days": 0, "old": Counter(), "new": Counter(),
                                          "old_alerts": 0, "new_alerts": 0,
                                          "prev_old": None, "prev_new": None,
                                          "latest_z": None, "profile_flags": None})
    for r in sorted(rows, key=lambda r: (str(r.get("plot_id")), r.get("created_at") or "")):
        p = per_plot[r.get("plot_id")]
        p["days"] += 1
        old = r.get("old") or "unknown"
        new = (r.get("risk_v2") or {}).get("status", "missing")
        old_counts[old] += 1
        new_counts[new] += 1
        cross[f"{old}->{new}"] += 1
        p["old"][old] += 1
        p["new"][new] += 1
        if old == "high" and p["prev_old"] in ("ok", "medium"):
            p["old_alerts"] += 1
        if new == "high" and p["prev_new"] in ("ok", "medium"):
            p["new_alerts"] += 1
        if old in _LEVELS:
            p["prev_old"] = old
        if new in _LEVELS:
            p["prev_new"] = new
        if (r.get("risk_v2") or {}).get("z") is not None:
            p["latest_z"] = r["risk_v2"]["z"]
        if r.get("profile"):
            p["profile_flags"] = r["profile"].get("flags")
    plots = [{"plot_id": pid, "days": p["days"], "old": dict(p["old"]), "new": dict(p["new"]),
              "old_alert_events": p["old_alerts"], "new_alert_events": p["new_alerts"],
              "latest_z": p["latest_z"], "profile_flags": p["profile_flags"]}
             for pid, p in per_plot.items()]
    return {"rows": len(rows), "old_levels": dict(old_counts), "new_status": dict(new_counts),
            "old_to_new": dict(cross), "plots": plots}


def shadow_markdown(summary: dict, days: int) -> str:
    """ตารางสรุปรายสัปดาห์ (ภาษาไทย) สำหรับวางในรายงาน — ไม่มีพิกัด/ชื่อเจ้าของ"""
    o, n = summary["old_levels"], summary["new_status"]
    other = lambda d: sum(v for k, v in d.items() if k not in _LEVELS)
    lines = [f"# สรุปผลรันคู่ระดับความเสี่ยง — {days} วันล่าสุด", "",
             f"ผลวิเคราะห์ทั้งหมด {summary['rows']} รายการ", "",
             "| | ปกติ | ปานกลาง / ชื้นกว่าปกติ | สูง / ชื้นกว่าปกติมาก | อื่น ๆ |",
             "|---|---|---|---|---|",
             f"| วิธีเดิม | {o.get('ok', 0)} | {o.get('medium', 0)} | {o.get('high', 0)} | {other(o)} |",
             f"| วิธีใหม่ | {n.get('ok', 0)} | {n.get('medium', 0)} | {n.get('high', 0)} | {other(n)} |",
             "",
             "| แปลง | วัน | เดิม: วันที่สูง | ใหม่: ปกติ/ชื้นกว่า/ชื้นกว่ามาก/ไม่มีข้อมูล | เตือน เดิม/ใหม่ | z ล่าสุด | ลักษณะแปลง |",
             "|---|---|---|---|---|---|---|"]
    for p in sorted(summary["plots"], key=lambda p: str(p["plot_id"])):
        nw = p["new"]
        z = "—" if p["latest_z"] is None else p["latest_z"]
        flags = ", ".join(p["profile_flags"] or []) or "—"
        lines.append(f"| {p['plot_id']} | {p['days']} | {p['old'].get('high', 0)} | "
                     f"{nw.get('ok', 0)}/{nw.get('medium', 0)}/{nw.get('high', 0)}/{other(nw)} | "
                     f"{p['old_alert_events']}/{p['new_alert_events']} | {z} | {flags} |")
    return "\n".join(lines) + "\n"
