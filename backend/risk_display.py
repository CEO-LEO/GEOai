"""
risk_display.py — โหมดแสดงระดับความเสี่ยงระหว่างปรับปรุงวิธี (อนุมัติ 2026-10-17)

ระดับความเสี่ยงวิธีเดิมเป็น "สูง" 82% ของผลวิเคราะห์ เพราะสูตรความชื้นอ่านทรงพุ่มเป็นน้ำ
(docs/v2/risk-level-review.md) — ระหว่างรันคู่วิธีใหม่ หน้าแอปและการ์ด LINE จึงไม่แสดงป้าย
"เสี่ยงสูง / ปานกลาง / ปกติ" และสถานะความชื้นแบบเดิม แต่แสดงข้อความ "กำลังปรับปรุง" แทน
(ข้อความตาม docs/v2/risk-display-interim.md) และหยุดคำเตือน "ความเสี่ยงเพิ่มขึ้น" ชั่วคราว
เตือนฝนจากพยากรณ์ยังส่งตามปกติ

env:
  RISK_DISPLAY_MODE = interim (ค่าเริ่มต้น) | legacy — legacy = แสดงแบบเดิมทุกอย่าง
  RISK_ALERTS_ENABLED = false (ค่าเริ่มต้นเมื่อ interim) | true
ข้อมูลเดิมยังคำนวณและบันทึกครบ (ใช้เทียบใน /admin/risk-shadow และ dashboard ผู้ดูแล)
"""

import os

INTERIM_LABEL = "ระดับความเสี่ยง: กำลังปรับปรุง"
INTERIM_SHORT = "กำลังปรับปรุง"
INTERIM_ICON = "🛠️"
INTERIM_NOTE = ("เรากำลังปรับวิธีประเมินให้แม่นขึ้นร่วมกับสำนักงานเกษตรจังหวัดราชบุรี "
                "ระหว่างนี้ดูจุดที่ชื้นหรือแห้งกว่าส่วนอื่นของแปลงได้จากแผนที่")
INTERIM_COLORS = {"header": "#546E7A", "badge": "#78909C", "icon": INTERIM_ICON, "label": INTERIM_SHORT}
INTERIM_DIGEST_LINE = ("ระดับความเสี่ยงกำลังปรับปรุงร่วมกับเกษตรจังหวัด "
                       "ดูจุดชื้น/แห้งในแปลงได้ในแอป")


def interim() -> bool:
    return os.environ.get("RISK_DISPLAY_MODE", "interim").strip().lower() != "legacy"


def risk_alerts_enabled() -> bool:
    """คำเตือน "ความเสี่ยงเพิ่มขึ้น" — ปิดเป็นค่าเริ่มต้นระหว่างโหมด interim"""
    default = "false" if interim() else "true"
    return os.environ.get("RISK_ALERTS_ENABLED", default).strip().lower() == "true"


def agronomy_numbers() -> bool:
    """
    ปุ๋ย กก./ต้น และผลผลิต กก./ไร่ (สูตรของทุเรียน ยังไม่ได้สอบเทียบ — phase0-report ข้อ 2.4/คำถามข้อ 8)
    ซ่อนจากเกษตรกรทั้งหมดเป็นค่าเริ่มต้น จนกว่าผู้เชี่ยวชาญจะตรวจ · env SHOW_UNVERIFIED_AGRONOMY=true เพื่อแสดง
    """
    return os.environ.get("SHOW_UNVERIFIED_AGRONOMY", "false").strip().lower() == "true"


def relative_label(status: str | None) -> str:
    """ป้ายจุดในแปลงแบบเทียบกันเอง (ไม่ใช้คำ "น้ำขังวิกฤต/แล้งวิกฤต" จากสูตรเดิม)"""
    if status in ("waterlogged", "wet"):
        return "ชื้นกว่าส่วนอื่นของแปลง"
    if status in ("dry", "drought"):
        return "แห้งกว่าส่วนอื่นของแปลง"
    return "ใกล้เคียงส่วนอื่นของแปลง"
