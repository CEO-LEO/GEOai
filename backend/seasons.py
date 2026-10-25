"""
seasons.py — ติดตามระยะยาว: เทียบหน้าฝน/หน้าแล้ง ของแปลงเดียวกัน (Phase 3c) — ฟังก์ชันล้วน ทดสอบได้

ข้อมูล: ค่าเรดาร์ Sentinel-1 VV ของแปลง (วงโคจรเดียว เหมือน risk_v2) รายเดือน (มัธยฐาน)
ฤดู (ประเทศไทย): หน้าฝน พ.ค.–ต.ค. · หน้าแล้ง พ.ย.–เม.ย. (หน้าแล้งตั้งชื่อตามปีที่สิ้นสุด เช่น พ.ย. 68–เม.ย. 69)
การเทียบ: ต่างกันน้อยกว่า SIMILAR_SD เท่าของความแปรปรวนรายเดือนของแปลงเอง = "ใกล้เคียง" — เป็นเกณฑ์ทางสถิติ
ไม่ใช่ค่าทางการเกษตร · ค่าเรดาร์สูง = ผิวชื้นกว่า (รวมทรงพุ่มด้วย จึงเทียบได้เฉพาะกับแปลงเดียวกัน)
"""

import statistics
from collections import Counter, defaultdict
from datetime import datetime

RAINY_MONTHS = {5, 6, 7, 8, 9, 10}
SIMILAR_SD = 0.5
MIN_IMAGES_PER_SEASON = 3
MIN_SD_DB = 0.5
MIN_IMAGES_PER_MONTH = 2      # เดือนที่มีภาพน้อยกว่านี้ไม่ใช้เลือก "เดือนที่ชื้น/แห้งที่สุด" (ค่าแกว่ง)
TH_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def season_key(t: datetime) -> tuple[str, int]:
    """("rainy", ปี) หรือ ("dry", ปีที่หน้าแล้งสิ้นสุด)"""
    if t.month in RAINY_MONTHS:
        return "rainy", t.year
    return "dry", t.year + 1 if t.month >= 11 else t.year


def season_label(kind: str, year: int) -> str:
    be = year + 543
    if kind == "rainy":
        return f"หน้าฝน {be} (พ.ค.–ต.ค.)"
    return f"หน้าแล้ง {be} (พ.ย. {str(be - 1)[-2:]}–เม.ย. {str(be)[-2:]})"


def summarize(series: list[tuple[datetime, float | None, int | None]], today: datetime) -> dict:
    """series = [(เวลา, VV dB, วงโคจร)] → รายเดือน + ค่าเฉลี่ยรายฤดู + ประโยคเทียบ"""
    obs = [(t, v, o) for t, v, o in series if v is not None and t <= today]
    if not obs:
        return {"status": "insufficient", "months": [], "seasons": [], "sentences": []}
    orbit = Counter(o for _, _, o in obs).most_common(1)[0][0]
    obs = [(t, v) for t, v, o in obs if o == orbit]

    by_month: dict[str, list[float]] = defaultdict(list)
    by_season: dict[tuple[str, int], list[float]] = defaultdict(list)
    for t, v in obs:
        by_month[t.strftime("%Y-%m")].append(v)
        by_season[season_key(t)].append(v)
    months = [{"month": m, "label": f"{TH_MONTHS[int(m[5:])]} {str(int(m[:4]) + 543)[-2:]}",
               "vv": round(statistics.median(vs), 2), "images": len(vs)} for m, vs in sorted(by_month.items())]
    month_values = [m["vv"] for m in months]
    # เพดานล่างเท่ากับ risk_v2.MIN_SD_DB — แปลงที่ค่าแทบไม่เปลี่ยนเลยไม่ให้ความต่างนิดเดียวกลายเป็น "ชื้นกว่า"
    sd = max(statistics.pstdev(month_values), MIN_SD_DB) if len(month_values) >= 3 else None
    lo, hi = (min(month_values), max(month_values)) if month_values else (0, 0)
    span = (hi - lo) or 1.0
    for m in months:   # 0–100 = แห้งสุด–ชื้นสุด "ของแปลงนี้เอง" ในช่วงที่ดู
        m["index"] = round((m["vv"] - lo) / span * 100)

    current = season_key(today)
    seasons = []
    for (kind, year), vs in sorted(by_season.items(), key=lambda kv: (kv[0][1], kv[0][0] == "rainy")):
        seasons.append({"kind": kind, "year": year, "label": season_label(kind, year),
                        "vv": round(statistics.mean(vs), 2), "images": len(vs),
                        "complete": (kind, year) != current, "enough": len(vs) >= MIN_IMAGES_PER_SEASON})

    sentences = []
    rainy = [s for s in seasons if s["kind"] == "rainy" and s["enough"]]
    dry = [s for s in seasons if s["kind"] == "dry" and s["enough"]]
    if sd and len(rainy) >= 2:
        sentences.append(_compare(rainy[-1], rainy[-2], sd))
    if sd and len(dry) >= 2:
        sentences.append(_compare(dry[-1], dry[-2], sd))
    if sd and rainy and dry:
        last_r, last_d = rainy[-1], dry[-1]
        diff = last_r["vv"] - last_d["vv"]
        if abs(diff) < SIMILAR_SD * sd:
            sentences.append(f"{_short(last_r)} กับ{_short(last_d)} ชื้นใกล้เคียงกัน — แปลงนี้ชื้นพอ ๆ กันทั้งปี")
        else:
            sentences.append(f"{_short(last_r)} {'ชื้นกว่า' if diff > 0 else 'แห้งกว่า'}{_short(last_d)}")
    solid = [m for m in months if m["images"] >= MIN_IMAGES_PER_MONTH]
    if len(solid) >= 3:
        peak = max(solid, key=lambda m: m["vv"])
        low = min(solid, key=lambda m: m["vv"])
        sentences.append(f"เดือนที่ชื้นที่สุดในช่วงนี้: {peak['label']} · แห้งที่สุด: {low['label']}")
    return {"status": "ok", "orbit": orbit, "months": months, "seasons": seasons, "sentences": sentences,
            "note_th": "ค่าเทียบกับแปลงนี้เองเท่านั้น (เรดาร์รวมทรงพุ่มด้วย) ไม่ได้บอกว่าดินแห้งหรือแฉะเกินไปจริง"}


def _short(s: dict) -> str:
    return s["label"].split(" (")[0]


def _compare(newer: dict, older: dict, sd: float) -> str:
    diff = newer["vv"] - older["vv"]
    partial = " (ยังไม่จบฤดู)" if not newer["complete"] else ""
    if abs(diff) < SIMILAR_SD * sd:
        return f"{_short(newer)}{partial} ชื้นใกล้เคียง{_short(older)}"
    return f"{_short(newer)}{partial} {'ชื้นกว่า' if diff > 0 else 'แห้งกว่า'}{_short(older)}"
