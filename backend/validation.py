"""
validation.py — การยืนยันภาคสนาม (Phase 2d): สรุป "ระบบบอก vs ในแปลงจริง" — ฟังก์ชันล้วน ทดสอบได้

กติกาการรายงาน (field-validation-plan.md ข้อ 4): บอกขนาดตัวอย่าง (n) เสมอ และไม่เรียกว่า "ความแม่นยำของระบบ"
ถ้าตัวอย่างยังน้อย — ใช้คำว่า "ตรงกับในแปลง x ใน n ครั้ง"
"""

from collections import defaultdict

import recommendations

ANSWERS = ("true", "false", "unsure")
ANSWER_TH = {"true": "จริง", "false": "ไม่จริง", "unsure": "ไม่แน่ใจ"}
MIN_N_FOR_RATE = 10     # น้อยกว่านี้ไม่แสดงเป็นเปอร์เซ็นต์ (ตัวเลขแกว่งเกินไป)


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
    return {"total": len(rows), "plots": len(plots - {None}), "by_problem": out}
