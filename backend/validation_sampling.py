"""
validation_sampling.py — สุ่มจุด "ที่ระบบไม่ได้เตือนน้ำขัง" ให้เกษตรกรอาสาไปตรวจ (แผนพิสูจน์ความแม่นยำ ข้อ 5.4)

ทำไมต้องสุ่ม: recall (จุดที่มีน้ำขังจริง ระบบชี้ได้กี่ส่วน) วัดได้ถูกต้องเมื่อจุดที่ "ระบบไม่ชี้" ถูกเลือกมาตรวจแบบสุ่ม
ไม่ใช่เลือกเฉพาะจุดที่อาสาเห็นว่าน่าสงสัย — จุดที่ผู้ใช้เลือกเองจึงไม่ถูกนำไปคำนวณ recall

วิธีสุ่ม (ฟังก์ชันล้วน ทำซ้ำได้จาก seed + รายการจุดตั้งต้น — ทั้งสองเก็บไว้ใน validation_samples):
  1. จุดตั้งต้น = จุดในตารางความชื้นของแปลงที่ระบบไม่ได้ชี้ว่า "ชื้น/น้ำรวม" (รวมจุดที่ชี้ว่าแห้ง)
  2. เรียงจุดตามตารางช่อง g×g ของแปลงแบบ "งูไล่" (แถวคู่ซ้าย→ขวา แถวคี่ขวา→ซ้าย) ให้จุดใกล้กันอยู่ติดกัน
  3. แบ่งรายการที่เรียงแล้วเป็น n กลุ่มขนาดเท่ากัน (ชั้น/stratum — แต่ละกลุ่มคือบริเวณหนึ่งของแปลง)
  4. สุ่มจุดละ 1 จุดจากแต่ละกลุ่มด้วย random.Random(seed) → ครอบคลุมทั้งแปลง ไม่กระจุก
"""

import hashlib
import json
import math
import random

METHOD = "stratified_serpentine_v1"
DEFAULT_N = 5
MAX_N = 10
MIN_CANDIDATES = 5          # ตัวตั้งต้นน้อยกว่านี้ (แปลงเล็กมาก) ไม่สุ่ม
MAX_CANDIDATES = 5000


def _round(p: dict) -> dict:
    return {"lat": round(float(p["lat"]), 6), "lng": round(float(p["lng"]), 6), "flag": p.get("flag") or "none"}


def normalize_candidates(points: list[dict]) -> list[dict]:
    """เรียงแบบกำหนดแน่นอน (ไม่ขึ้นกับลำดับที่ client ส่งมา) และตัดจุดซ้ำ"""
    seen, out = set(), []
    for p in sorted((_round(p) for p in points), key=lambda q: (q["lat"], q["lng"])):
        k = (p["lat"], p["lng"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def candidates_hash(cands: list[dict]) -> str:
    raw = json.dumps([[c["lat"], c["lng"], c["flag"]] for c in cands], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def serpentine_order(cands: list[dict]) -> list[dict]:
    """เรียงตามช่องตาราง g×g (g = ceil(sqrt(N))) แบบงูไล่ — จุดในช่องเดียวกันเรียงตามเส้นทางงู"""
    n = len(cands)
    g = max(1, math.ceil(math.sqrt(n)))
    lats, lngs = [c["lat"] for c in cands], [c["lng"] for c in cands]
    lat0, lat1, lng0, lng1 = min(lats), max(lats), min(lngs), max(lngs)
    dl, dn = (lat1 - lat0) or 1e-9, (lng1 - lng0) or 1e-9

    def key(c):
        row = min(g - 1, int((c["lat"] - lat0) / dl * g))
        col = min(g - 1, int((c["lng"] - lng0) / dn * g))
        return (row, col if row % 2 == 0 else g - 1 - col, c["lat"], c["lng"])
    return sorted(cands, key=key)


def draw(cands: list[dict], seed: int, n: int = DEFAULT_N) -> list[dict]:
    """คืนจุดที่สุ่มได้ ≤ n จุด พร้อมลำดับชั้น (stratum 1..n) — cands ต้องผ่าน normalize_candidates แล้ว"""
    n = max(1, min(n, MAX_N, len(cands)))
    ordered = serpentine_order(cands)
    rng = random.Random(seed)
    size = len(ordered) / n
    out = []
    for i in range(n):
        lo, hi = round(i * size), round((i + 1) * size)
        block = ordered[lo:hi] or ordered[lo:lo + 1]
        pick = block[rng.randrange(len(block))]
        out.append({**pick, "stratum": i + 1})
    return out


def same_point(a_lat: float, a_lng: float, b: dict, tol: float = 2e-6) -> bool:
    return abs(a_lat - b["lat"]) <= tol and abs(a_lng - b["lng"]) <= tol
