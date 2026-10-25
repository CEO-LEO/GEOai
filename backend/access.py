"""
access.py — สิทธิ์ตามบทบาท (Phase 2a) — ตรวจฝั่ง server ทุกครั้ง

  farmer  : เห็น/แก้เฉพาะแปลงตัวเอง
  officer : แปลงที่ตัวเองวาด (แทนเกษตรกร) + แปลงในอำเภอที่รับผิดชอบ **เฉพาะเจ้าของที่ยินยอมแบ่งปันกับเจ้าหน้าที่**
            (users.share_with_officers — ตั้งในหน้ายินยอม 2b) · แก้ชนิดพืช/ประเภทสวน/ขอบเขต ตรวจซ้ำ แนบรูป ยืนยันข้อมูลได้
            ลบแปลง/รวมประวัติ ไม่ได้
  admin   : ทุกแปลง (ทุกการเข้าดูแปลงของคนอื่นถูกบันทึกใน audit_logs)

ฟังก์ชันในไฟล์นี้เป็นฟังก์ชันล้วน (ไม่เรียก DB) — ผู้เรียกส่งข้อมูลสิทธิ์และแปลงมาให้
"""

import time

ROLES = ("farmer", "officer", "admin")
ROLE_TH = {"farmer": "เกษตรกร", "officer": "เจ้าหน้าที่", "admin": "ผู้ดูแลระบบ"}
VIEW, EDIT, OWNER_ONLY = "view", "edit", "owner_only"


def plot_access_reason(access: dict, user_id: str, plot: dict, owner_shares: bool, action: str = VIEW) -> str | None:
    """
    เหตุผลที่เข้าถึงได้ ("owner" / "admin" / "officer_created" / "officer_area") หรือ None = ไม่มีสิทธิ์
    access = {"role", "areas"} ของผู้เรียก · owner_shares = เจ้าของแปลงยินยอมแบ่งปันกับเจ้าหน้าที่
    """
    if plot.get("user_id") == user_id:
        return "owner"
    role = (access or {}).get("role", "farmer")
    if role == "admin":
        return "admin"
    if role != "officer" or action == OWNER_ONLY:
        return None
    if plot.get("created_by") == user_id:
        return "officer_created"
    if owner_shares and plot.get("area_code") and plot["area_code"] in ((access or {}).get("areas") or []):
        return "officer_area"
    return None


def can_upgrade(current_role: str, new_role: str) -> bool:
    """รหัสเชิญเพิ่มสิทธิ์ได้อย่างเดียว ไม่ลดสิทธิ์ (admin ใช้รหัสเชิญ officer แล้วไม่กลายเป็น officer)"""
    return ROLES.index(new_role) > ROLES.index(current_role if current_role in ROLES else "farmer")


# ── แคชสิทธิ์ในหน่วยความจำ (เปลี่ยนบทบาทแล้วเรียก invalidate) ──
_TTL_S = 60
_cache: dict[str, tuple[float, dict]] = {}


def cached(user_id: str) -> dict | None:
    hit = _cache.get(user_id)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    return None


def remember(user_id: str, access: dict) -> dict:
    _cache[user_id] = (time.monotonic(), access)
    return access


def invalidate(user_id: str | None = None) -> None:
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)
