"""
conftest.py — ตั้ง env ปลอมให้ test ทุกไฟล์ ก่อน import โมดูลใดๆ ของแอป

ทำไมต้องมี: main.py เรียก load_dotenv() ทำให้ backend/.env ตัวจริงในเครื่องนักพัฒนา
ถูกโหลดเข้ามาระหว่างรัน test — ADMIN_API_KEY จริงทำให้ test admin พัง 6 ตัว
(พบ 2026-10-15) และเสี่ยงเรียก Supabase/LINE จริงด้วย key จริง
IAMROOT_DISABLE_DOTENV=1 ปิด load_dotenv() ใน main.py และค่าด้านล่างเขียนทับ env เดิม
ของเครื่องเสมอ (ไม่ใช่ setdefault) ผลจึงเหมือนกันทุกเครื่องและบน CI
"""

import os

TEST_ADMIN_KEY = "test-admin-key"
TEST_CHANNEL_ID = "1234567890"

_TEST_ENV = {
    "IAMROOT_DISABLE_DOTENV":    "1",
    "ADMIN_API_KEY":             TEST_ADMIN_KEY,
    "LINE_CHANNEL_ACCESS_TOKEN": "test_token",
    "LINE_CHANNEL_SECRET":       "test_secret",
    "LINE_LOGIN_CHANNEL_ID":     TEST_CHANNEL_ID,
    "LIFF_URL":                  f"https://liff.line.me/{TEST_CHANNEL_ID}-TestLiff",
    "GEE_SERVICE_ACCOUNT":       "test@test.iam.gserviceaccount.com",
    "GEE_KEY_JSON":              '{"type": "service_account"}',
    "SUPABASE_URL":              "",
    "SUPABASE_SERVICE_ROLE_KEY": "",
    "SPHERE_KEY":                "",
    "API_BASE_URL":              "",
    "CORS_ALLOW_ORIGINS":        "",
}
os.environ.update(_TEST_ENV)
for _var in ("IAMROOT_DEV_AUTH_USER", "RENDER"):
    os.environ.pop(_var, None)


ADMIN_HEADERS = {"X-Admin-Key": TEST_ADMIN_KEY}


def auth_as(user_id: str) -> dict:
    """header ของผู้ใช้ที่ยืนยันตัวตนแล้ว — ใช้คู่กับ fake_verify_line_token"""
    return {"Authorization": f"Bearer tok-{user_id}", "X-Line-Token-Type": "id_token"}


async def fake_verify_line_token(token: str, token_type: str) -> str | None:
    """แทน auth.verify_line_token ใน test: token รูปแบบ "tok-<userId>" ถือว่าถูกต้อง"""
    return token[len("tok-"):] if token.startswith("tok-") else None
