"""
references.py — นำเข้าแปลงอ้างอิงจากไฟล์ CSV (เช่น ทะเบียนเกษตรกรที่ได้ผ่านเกษตรจังหวัด — spec 7.7) — ฟังก์ชันล้วน

คอลัมน์: ref (รหัสแถวในแหล่งเดิม), crop (ชื่อไทยหรือรหัสพืช — หลายชนิดคั่นด้วย |), lat, lng,
          district (ไม่บังคับ: รหัสอำเภอ เช่น 7004 หรือชื่อไทย)
ใช้เทรนเท่านั้น ไม่แสดงในแอป · ต้องระบุที่มาทางกฎหมายของข้อมูล (consent_basis) ทุกครั้งที่นำเข้า
"""

import csv
import io

import areas
import crops as crops_mod

MAX_ROWS = 5000
TH_BBOX = (5.5, 20.6, 97.3, 105.7)      # lat_min, lat_max, lng_min, lng_max (ประเทศไทย)


def parse_csv(text: str, source: str, consent_basis: str, imported_by: str) -> tuple[list[dict], list[str]]:
    """คืน (แถวที่นำเข้าได้, ข้อผิดพลาดรายแถว) — แถวผิดไม่หยุดทั้งไฟล์"""
    known = crops_mod.by_id()
    by_name = {c["name_th"]: c["id"] for c in known.values()}
    dist_by_name = {d["name_th"]: d["code"] for d in areas.districts()}
    dist_codes = set(areas.by_code())
    rows, errors, seen = [], [], set()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    need = {"ref", "crop", "lat", "lng"}
    if not reader.fieldnames or not need <= {h.strip() for h in reader.fieldnames}:
        return [], ["ต้องมีคอลัมน์ ref, crop, lat, lng"]
    for i, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            errors.append(f"เกิน {MAX_ROWS} แถว — แถวที่เหลือไม่ได้นำเข้า")
            break
        r = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        ref = r.get("ref", "")
        if not ref or ref in seen:
            errors.append(f"แถว {i}: ไม่มีรหัส ref หรือซ้ำ")
            continue
        try:
            lat, lng = float(r["lat"]), float(r["lng"])
        except ValueError:
            errors.append(f"แถว {i}: พิกัดไม่ใช่ตัวเลข")
            continue
        if not (TH_BBOX[0] <= lat <= TH_BBOX[1] and TH_BBOX[2] <= lng <= TH_BBOX[3]):
            errors.append(f"แถว {i}: พิกัดอยู่นอกประเทศไทย (ตรวจว่าสลับ lat/lng หรือไม่)")
            continue
        ids, bad = [], []
        for c in (x.strip() for x in r["crop"].split("|") if x.strip()):
            cid = c if c in known else by_name.get(c)
            (ids if cid else bad).append(cid or c)
        if bad or not ids:
            errors.append(f"แถว {i}: ไม่รู้จักพืช {', '.join(bad) or '(ว่าง)'}")
            continue
        d = r.get("district", "")
        code = d if d in dist_codes else dist_by_name.get(d)
        seen.add(ref)
        rows.append({"source": source, "source_ref": ref, "crop_ids": ids, "lat": lat, "lng": lng, "polygon": None,
                     "area_code": code, "consent_basis": consent_basis, "imported_by": imported_by})
    return rows, errors
