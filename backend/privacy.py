"""
privacy.py — PDPA (Phase 2b): ฉบับนโยบาย + รายการขอความยินยอม — แหล่งเดียวที่ทั้ง API และหน้าแอปใช้

เปลี่ยนเนื้อหาที่มีผลต่อสิทธิ์ผู้ใช้ → เปลี่ยน PRIVACY_VERSION แล้วแอปจะถามความยินยอมใหม่ทุกคน
นโยบายฉบับเต็ม: liff/privacy.html (ต้องตรงกับฉบับนี้)
"""

PRIVACY_VERSION = "2026-10-25"
POLICY_URL = "/liff/privacy.html"
DELETION_GRACE_DAYS = 30     # ยกเลิกคำขอลบได้ภายในช่วงนี้ หลังจากนั้นผู้ดูแลลบถาวร

REQUIRED_TH = [
    "ตำแหน่ง ขอบเขต ชื่อแปลง ชนิดพืช และผลวิเคราะห์จากดาวเทียม — ใช้แสดงผลและคำแนะนำให้คุณ",
    "LINE userId และชื่อที่แสดงใน LINE — ใช้ยืนยันตัวตนและส่งข้อความที่คุณเลือกรับ",
    "สถิติการใช้งานแอป (เช่น เปิดแอป ตรวจแปลง) — ใช้ปรับปรุงระบบและรายงานภาพรวมต่อหน่วยงานรัฐโดยไม่ระบุตัวบุคคล",
]

OPTIONAL = [
    {"key": "share_with_officers",
     "title_th": "แบ่งปันแปลงกับเจ้าหน้าที่เกษตรในอำเภอของแปลง",
     "body_th": "เจ้าหน้าที่ของสำนักงานเกษตรที่ได้รับสิทธิ์ในอำเภอนั้นเห็นแปลง ผลวิเคราะห์ และรูปที่แนบ "
                "เพื่อช่วยแนะนำและตรวจในแปลง — ทุกครั้งที่เจ้าหน้าที่เปิดดูจะถูกบันทึกไว้ และคุณดูได้"},
    {"key": "photos_research",
     "title_th": "ใช้รูปถ่ายและการยืนยันในแปลงเพื่อปรับปรุงระบบ",
     "body_th": "เช่น ใช้ตรวจความแม่นยำ และสอนระบบให้จำแนกชนิดพืชได้ — ไม่เผยแพร่รูปหรือพิกัดของคุณต่อสาธารณะ"},
]


def consent_payload(user: dict | None) -> dict:
    """สถานะความยินยอมของผู้ใช้สำหรับหน้าแอป — needs_consent = ยังไม่ยอมรับฉบับปัจจุบัน"""
    u = user or {}
    return {
        "version": PRIVACY_VERSION,
        "policy_url": POLICY_URL,
        "accepted_version": u.get("consent_version"),
        "needs_consent": u.get("consent_version") != PRIVACY_VERSION,
        "choices": {"share_with_officers": bool(u.get("share_with_officers")),
                    "photos_research": bool(u.get("consent_photos_research"))},
        "deletion_requested_at": u.get("deletion_requested_at"),
        "required_th": REQUIRED_TH,
        "optional": OPTIONAL,
    }
