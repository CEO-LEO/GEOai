"""
validation.py — การยืนยันภาคสนาม (Phase 2d): สรุป "ระบบบอก vs ในแปลงจริง" — ฟังก์ชันล้วน ทดสอบได้

กติกาการรายงาน (field-validation-plan.md ข้อ 4): บอกขนาดตัวอย่าง (n) เสมอ และไม่เรียกว่า "ความแม่นยำของระบบ"
ถ้าตัวอย่างยังน้อย — ใช้คำว่า "ตรงกับในแปลง x ใน n ครั้ง"
"""

import math
from collections import defaultdict

import recommendations

ANSWERS = ("true", "false", "unsure")
ANSWER_TH = {"true": "จริง", "false": "ไม่จริง", "unsure": "ไม่แน่ใจ"}
MIN_N_FOR_RATE = 10     # น้อยกว่านี้ไม่แสดงเป็นเปอร์เซ็นต์ (ตัวเลขแกว่งเกินไป)
FLAGGED_WATER = ("wet_spot", "water_collect")     # ระบบชี้ว่าจุดนี้ชื้น/น้ำรวม → จริง = ตรง
UNFLAGGED_WATER = "water_check_unflagged"         # ระบบไม่ได้ชี้ → จริง = ระบบพลาด
SYMPTOM = "tree_symptom"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """ช่วงความเชื่อมั่น 95% ของสัดส่วน k/n (Wilson) — กรรมการทุนอ่านออกว่าตัวอย่างน้อยแค่ไหน"""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 2), round(min(1.0, c + h), 2)


def _rate(k: int, n: int) -> dict:
    ci = wilson(k, n)
    ok = n >= MIN_N_FOR_RATE
    return {"k": k, "n": n, "rate": round(k / n, 2) if ok else None, "ci95": list(ci) if ok and ci else None}


def accuracy(rows: list[dict]) -> dict:
    """
    ความแม่นยำจากคำตอบเกษตรกรอาสา/เจ้าหน้าที่ (ไม่นับ "ไม่แน่ใจ") — รายงานพร้อม n และช่วงความเชื่อมั่นเสมอ
      precision = ระบบชี้จุดชื้น/น้ำรวม แล้วในแปลงจริงใช่ · recall = จุดที่ระบบไม่ชี้ แต่จริงมีน้ำขัง (พลาด)
    recall ใช้ได้เมื่อสุ่มจุดที่ระบบไม่ชี้มาตรวจจริง (อาสาต้องตอบจุดที่ไม่ถูกชี้ด้วย) — ไม่งั้นเอนเอียง
    อาการต้นไม้: เทียบสัดส่วนที่มีอาการ ระหว่างจุดที่ระบบชี้ชื้น กับจุดที่ไม่ชี้
    """
    tp = fp = fn = tn = 0
    sym = {"flagged_wet": [0, 0], "not_flagged": [0, 0]}     # [มีอาการ, ตอบชัดทั้งหมด]
    users, photos, user_chosen_unflagged = set(), 0, 0
    for r in rows:
        if r.get("scope") != "point" or r["answer"] == "unsure":
            continue
        a, p = r["answer"] == "true", r["problem"]
        random_pt = r.get("selection") == "random"
        if p in FLAGGED_WATER:
            tp, fp = tp + a, fp + (not a)
        elif p == UNFLAGGED_WATER:
            if random_pt:                       # recall ใช้เฉพาะจุดที่ระบบสุ่มให้ (ผู้ใช้เลือกเองเอนเอียง)
                fn, tn = fn + a, tn + (not a)
            else:
                user_chosen_unflagged += 1
        elif p == SYMPTOM:
            flag = (r.get("context") or {}).get("flag")
            if flag in FLAGGED_WATER:
                g = sym["flagged_wet"]
            elif random_pt:                     # กลุ่มเทียบ "ไม่ได้ชี้" ใช้เฉพาะจุดสุ่มเช่นกัน
                g = sym["not_flagged"]
            else:
                continue
            g[0] += a
            g[1] += 1
    for r in rows:
        if r["problem"] in FLAGGED_WATER + (UNFLAGGED_WATER, SYMPTOM):
            users.add(r.get("user_id"))
            photos += 1 if r.get("photo_client_id") else 0
    return {
        "water_precision": _rate(tp, tp + fp),
        "water_recall": _rate(tp, tp + fn),
        "counts": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "symptom_in_flagged_wet": _rate(*sym["flagged_wet"]),
        "symptom_in_not_flagged": _rate(*sym["not_flagged"]),
        "respondents": len(users - {None}), "answers_with_photo": photos,
        "user_chosen_unflagged_excluded": user_chosen_unflagged,
        "note_th": ("ตัวเลขเบื้องต้นจากกลุ่มอาสา — ไม่ใช่ความแม่นยำของระบบทั้งประเทศ · แสดงเป็นเปอร์เซ็นต์เมื่อ n ≥ "
                    f"{MIN_N_FOR_RATE} · recall ใช้ได้เมื่อสุ่มจุดที่ระบบไม่ชี้มาตรวจด้วย"),
    }


def summarize(rows: list[dict]) -> dict:
    """
    แยกตามปัญหา: จำนวนคำตอบแต่ละแบบ, จำนวนที่ตอบชัด (จริง+ไม่จริง), สัดส่วนที่ตรง (เฉพาะเมื่อ n ≥ MIN_N_FOR_RATE)
    นับแยกเจ้าหน้าที่กับเกษตรกร (คำตอบเจ้าหน้าที่น่าเชื่อถือกว่าสำหรับวัดผล — spec 7.7)
    """
    by_problem: dict[str, dict] = defaultdict(lambda: {a: 0 for a in ANSWERS} | {"officer": {a: 0 for a in ANSWERS}})
    plots: set = set()
    for r in rows:
        b = by_problem[r["problem"]]
        b[r["answer"]] += 1
        if r.get("role") in ("officer", "admin"):
            b["officer"][r["answer"]] += 1
        plots.add(r.get("plot_id"))
    out = []
    for problem, b in sorted(by_problem.items()):
        decided = b["true"] + b["false"]
        out.append({
            "problem": problem, "problem_th": recommendations.PROBLEMS_TH.get(problem, problem),
            "true": b["true"], "false": b["false"], "unsure": b["unsure"], "decided": decided,
            "officer_true": b["officer"]["true"], "officer_false": b["officer"]["false"],
            "match_rate": round(b["true"] / decided, 2) if decided >= MIN_N_FOR_RATE else None,
            "text_th": f"ตรงกับในแปลง {b['true']} ใน {decided} ครั้งที่ตอบชัด" + (
                "" if decided >= MIN_N_FOR_RATE else f" (ตัวอย่างยังน้อย — ต้องการอย่างน้อย {MIN_N_FOR_RATE})"),
        })
    return {"total": len(rows), "plots": len(plots - {None}), "by_problem": out, "accuracy": accuracy(rows)}


def sampling_summary(samples: list[dict], rows: list[dict]) -> dict:
    """สรุปการสุ่มสำหรับหน้า admin — วิธีสุ่ม, จำนวนครั้ง/จุดที่สุ่ม, จุดที่ตอบแล้ว (ตอบได้หลายคำถามต่อจุด นับจุดไม่ซ้ำ)"""
    import validation_sampling as vs
    drawn = sum(len(s.get("points") or []) for s in samples)
    answered = {(r.get("sample_id"), round(r["lat"], 6), round(r["lng"], 6)) for r in rows
                if r.get("selection") == "random" and r.get("lat") is not None and r["problem"] == UNFLAGGED_WATER}
    n_per = max([len(s.get("points") or []) for s in samples], default=vs.DEFAULT_N)
    return {
        "method": vs.METHOD, "samples": len(samples), "points_drawn": drawn, "points_answered": len(answered),
        "plots": len({s["plot_id"] for s in samples}), "n_per_draw": n_per,
        "seeds_recorded": all(s.get("seed") is not None for s in samples),
        "method_th": (f"จุดตั้งต้น = จุดในแปลงที่ระบบไม่ได้ชี้ว่าชื้น/น้ำรวม (รวมจุดที่ชี้ว่าแห้ง) → เรียงตามตำแหน่งในแปลงแบบงูไล่ → "
                      f"แบ่งเป็น {vs.DEFAULT_N} กลุ่มเท่า ๆ กัน (ครอบคลุมทั้งแปลง) → สุ่ม 1 จุดต่อกลุ่มด้วยเลขสุ่ม (seed) ที่ระบบบันทึกไว้ทุกครั้ง "
                      f"พร้อมรายการจุดตั้งต้นทั้งหมด จึงตรวจย้อนและทำซ้ำได้ · สุ่มชุดเดิมคืนให้ภายใน 14 วันเพื่อกันเลือกจุดที่ชอบ · "
                      f"สวนยกร่องไม่ถูกนำมาวัด recall (ระบบไม่ชี้น้ำขังในสวนยกร่อง)"),
    }
