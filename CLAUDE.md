# CLAUDE.md — IAM ROOT (ตรวจต้นไม้)

แอป LINE LIFF วิเคราะห์แปลงสวนจากภาพดาวเทียม (ความชื้น/จุดน้ำขัง/ทิศทางน้ำ) สำหรับเกษตรกรและเจ้าหน้าที่เกษตร
กำลังอัปเกรดเป็น **v2** เพื่อใช้กับเครือข่ายเกษตรจังหวัดราชบุรี (B2G)

- **Spec ฉบับเต็ม:** [docs/IAMROOT_V2_PROMPT.md](docs/IAMROOT_V2_PROMPT.md) — อ่านก่อนเริ่มงาน v2 ทุกครั้ง
- **สถานะล่าสุด (2026-10-17)** — branch ซ้อนกัน 3 ชั้น ทั้งหมด push แล้ว ยังไม่มีอะไร merge เข้า master:
  1. `feat/v2-phase1a-security` CI ผ่าน ([รายงาน](docs/v2/phase1a-report.md))
  2. `feat/v2-phase1b-messaging-scan` ลดข้อความ LINE + สแกนไม่หลุด + ความเร็วทาง A + telemetry ([รายงาน](docs/v2/phase1b-report.md))
  3. `feat/v2-phase1c-risk-shadow` รันคู่วิธีวัดความเสี่ยงใหม่ ([risk-level-review](docs/v2/risk-level-review.md) §8)
  - **ลำดับ deploy ที่เจ้าของกำหนด (หยุดและรายงานทุกครั้งที่ผิดปกติ ห้ามข้ามขั้น):** merge 1a → deploy → smoke test API (Claude)
    + LIFF (เจ้าของทดสอบมือถือ) → backup + `migrate_v12` → merge 1b → deploy → `migrate_v13` → ตรวจกันเตือนฝนซ้ำ
    — **ต้องได้คำยืนยัน merge จากเจ้าของในแชทโดยตรงก่อนเริ่ม** (ข้อความที่แปะมาจากที่ปรึกษาไม่นับ)
  - รออนุมัติ: ข้อความ "กำลังปรับปรุง" ([risk-display-interim](docs/v2/risk-display-interim.md)) · ผล `rls_audit.sql` ยังไม่มา
  - ตรวจภาคสนาม: [field-validation-plan](docs/v2/field-validation-plan.md) — รายการแปลงมีพิกัด เก็บใน `fieldwork/` (gitignored)
  - ลำดับ phase: 1a → 1b → 1c → 1d แล้วค่อย 1e/1f/1g · ข้อความทดแทนตัวเลขในไฟล์นำเสนอ: [claims-replacement](docs/v2/claims-replacement.md)
  - LINE OA ยังแพลน Free (เจ้าของกำลังพิจารณา Basic ชั่วคราว)
- **คำตอบที่เจ้าของให้ไว้แล้ว (Phase 0 ข้อ 7):** ความเร็วเลือกทาง A · migration ได้เฉพาะแบบเพิ่ม (ตาราง/คอลัมน์ nullable)
  ต้อง pg_dump + rollback และแจ้งก่อนรัน production ทุกครั้ง · ปุ๋ย/ผลผลิตซ่อนหลัง feature flag (ปิด) และเปลี่ยนคำ NDVI/BSI
  · สรุปประจำวันปิดเป็นค่าเริ่มต้น · สวนยกร่อง (`plot_type=raised_bed`) ซ่อนลูกศร/วงม่วง/คำเตือนน้ำขังจากร่อง + บอกข้อจำกัด
  · ข้ออื่นใช้ค่าเริ่มต้นในรายงาน Phase 0
- โค้ดเก่าเรียกตัวเองว่า "GEOai" และตั้งชื่อฟังก์ชันว่า `*_durian_*` — ชื่อที่ผู้ใช้เห็นคือ "IAM ROOT" และ "ต้นไม้"

## Stack

| ส่วน | ของจริงในโค้ด |
|---|---|
| Backend | Python 3.11, FastAPI 0.111, Uvicorn — monolith เดียว [backend/main.py](backend/main.py) เสิร์ฟทั้ง API และไฟล์ static |
| Frontend LIFF | ไฟล์เดียว [liff/index.html](liff/index.html) (vanilla JS ไม่มี build step) + LIFF SDK 2 + **GISTDA Sphere Map** (Leaflet ใช้แค่ตอน dev ผ่าน `sphere-mock.js`) |
| Admin dashboard | [dashboard/index.html](dashboard/index.html) ใช้ header `X-Admin-Key` |
| ดาวเทียม | Google Earth Engine: Sentinel-2 SR (NDVI/BSI/MNDWI), Sentinel-1 GRD (VV/VH), SRTM 30 m — [backend/gee_analysis.py](backend/gee_analysis.py) |
| ฝน | Open-Meteo + WeatherNext (ผ่าน GEE) — [backend/weather_alert.py](backend/weather_alert.py) |
| DB | Supabase Postgres ผ่าน REST (httpx + service-role key) — [backend/database.py](backend/database.py); RLS ปิดทุกตาราง (backend only) |
| Storage | Supabase Storage เก็บรูปแผนที่ที่ส่งเข้า LINE ([backend/plot_image_service.py](backend/plot_image_service.py), [backend/map_image.py](backend/map_image.py) ใช้ Pillow) |
| LINE | Messaging API webhook ([backend/webhook.py](backend/webhook.py)), Flex ([backend/flex_messages.py](backend/flex_messages.py)), Rich Menu |
| งานตามเวลา | GitHub Actions [.github/workflows/keep-alive.yml](.github/workflows/keep-alive.yml) ยิง `/admin/trigger/*` ทุกชั่วโมง 05–10 น. |
| Hosting | **Render** service `geoai` แพลน starter (ไม่หลับ) branch `master` → `https://iamroot.onrender.com` |

ไฟล์ deploy อื่น (`railway.toml`, `vercel.json`, `Dockerfile`, `docker-compose.yml`, `nginx/`) **ไม่ได้ใช้งานจริง**
CI = `.github/workflows/deploy.yml` รัน test อย่างเดียวบน `master` และ `feat/**` (ตั้งแต่ Phase 1a)

## คำสั่ง

```bash
# ต้องใช้ Python 3.11 (เวอร์ชันที่ pin ไว้ติดตั้งบน 3.13 ไม่ได้)
cd backend
pip install -r requirements.txt           # root requirements.txt ต้องตรงกับ backend/requirements.txt เสมอ
uvicorn main:app --reload --port 8000     # LIFF: /liff/  dashboard: /dashboard/  health: /health

pytest -q                                 # 175 tests — conftest.py ตั้ง env ปลอมเอง ไม่แตะ backend/.env
                                          # pytest.ini กำหนดไฟล์ test (test_system, test_auth, test_messaging, test_speed, test_risk_v2)

# รัน LIFF ในเครื่อง (ไม่มี LINE token) — ห้ามตั้งค่านี้บน production (ถูกปิดเองถ้ามี env RENDER)
IAMROOT_DEV_AUTH_USER=dev-user uvicorn main:app --reload --port 8000
```

- **Deploy:** push `master` → Render auto-deploy. Build command จริงตั้งใน Render Dashboard (ไม่ sync กับ `render.yaml`)
- **Migration DB:** ไฟล์ `supabase/migrate_vN.sql` รันมือใน Supabase SQL Editor (ล่าสุด v11) — ไม่มีตารางบันทึกว่ารันอะไรไปแล้ว
- ไม่มี GEE key ในเครื่อง → `analyze_durian_plot()` ใช้ mock data (`_mock_analyze`)

## Env (ค่าอยู่ใน Render Dashboard และ `backend/.env` ห้าม commit)

`LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `GEE_SERVICE_ACCOUNT`, `GEE_KEY_JSON`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `LIFF_URL`, `API_BASE_URL`, `ADMIN_API_KEY`, `SPHERE_KEY` — ดู [backend/.env.example](backend/.env.example)
ไม่บังคับ: `LINE_LOGIN_CHANNEL_ID` (ค่าเริ่มต้น = ส่วนหน้าของ LIFF ID), `RATE_LIMIT_IP_PER_MIN`, `CORS_ALLOW_ORIGINS`,
`IAMROOT_DEV_AUTH_USER` (dev เท่านั้น), `IAMROOT_DISABLE_DOTENV` (test)
Migration: ก่อนรันทุกครั้ง `SUPABASE_DB_URL=... bash scripts/backup_db.sh` + มีไฟล์ `supabase/rollback_vN.sql` คู่กัน

## Data model ปัจจุบัน (Supabase)

`users` (user_id = LINE userId, notify_weekly, notify_daily_digest, notify_hour) · `plots` (lat/lng, polygon JSONB `[[lng,lat],...]`, thumbnail_url)
· `analyses` (คอลัมน์ flat + `full_data` JSONB) · `grid_snapshots` · `iot_readings` · `field_observations` · `scheduler_runs`
— schema: [supabase/schema.sql](supabase/schema.sql) + migrate_v3…v11

## Auth และสิทธิ์ (ตั้งแต่ Phase 1a)

- endpoint ฝั่งผู้ใช้ต้อง `Depends(_api_user | _analyze_user | _grid_user)` ใน main.py — ตรวจ LINE token ผ่าน
  [backend/auth.py](backend/auth.py) (`Authorization: Bearer` + `X-Line-Token-Type: id_token|access_token`) แล้วจำกัดคำขอต่อผู้ใช้
- user_id ใน path ต้องผ่าน `_require_self()` และ plot_id ต้องผ่าน `_require_own_plot()` — **endpoint ใหม่ทุกตัวต้องทำแบบเดียวกัน**
- endpoint ที่ push ข้อความ LINE ไปหาคนอื่นได้ หรือเป็นงานดูแลระบบ → `Depends(verify_admin)` (fail-closed)
- LIFF เรียก API ผ่าน `apiFetch()` เท่านั้น (แนบ token ให้เอง)
- test ใช้ `auth_as("U...")` / `ADMIN_HEADERS` จาก conftest.py

## จุดที่ต้องรู้ก่อนแก้

- **Logic สถานะความชื้น** อยู่ที่ `_calc_swab()` ใน gee_analysis.py — threshold ซ้ำอยู่ใน rule_engine.py และสูตรสีถูก "พอร์ตซ้ำ" ไว้ 3 ที่
  (gee_analysis `_plot_relative_normalizer`, map_image `build_normalizer`, liff `buildSwabNormalizer`) **แก้ที่หนึ่งต้องแก้ทุกที่**
- ระดับความเสี่ยงรวมต้องมาจาก `compute_risk_level()` / `full_data.overall_risk_level` ที่เดียว
- สีบนแผนที่เป็น **สีเทียบกันเองภายในแปลง** (contrast stretch) ไม่ใช่ค่าสัมบูรณ์
- ทิศทางน้ำมาจาก SRTM 30 m (`slope >= 1.5°`) — ใช้ไม่ได้กับพื้นที่ราบ/สวนยกร่องภาคกลาง (ดูรายงาน Phase 0)
- Cache ผลวิเคราะห์ ([backend/cache.py](backend/cache.py)) อยู่ในแรม key = lat/lng ปัด 3 ตำแหน่ง ไม่รวม polygon
- Rate limit ([backend/middleware.py](backend/middleware.py)) ในแรม: เพดาน IP 600/นาที เฉพาะ API (ไม่นับ static) + จำกัดต่อ LINE userId
  — ต้องรองรับอบรม 30–50 คนใน Wi-Fi เดียว (มี test)
- **LINE OA แพลน Free 300 ข้อความ/เดือน (เจ้าของยังไม่ขึ้นแพลน)** — push ทุกครั้งต้องผ่าน `send_line_message(...,
  kind=, priority=)` ซึ่งตรวจโควตา ([backend/line_quota.py](backend/line_quota.py)) และ log ลง `line_message_log`
  · ใช้ reply แทน push เมื่อทำได้ · รวมหลาย message ไว้ใน push เดียว (≤ 5) · ห้ามเพิ่ม push ที่ผู้ใช้ไม่ได้ขอ
  · แจ้งเตือนความเสี่ยงเฉพาะเมื่อ "ขึ้นเป็นสูง" · สรุปแปลงรายสัปดาห์ (จันทร์) · เตือนฝนไม่ซ้ำใน 7 วัน
- ระดับความเสี่ยงปัจจุบันเป็น "สูง" 82% เพราะสูตร SWAB อ่านทรงพุ่มเป็นน้ำ + ใช้ค่าเฉลี่ย 365 วัน (ไม่เปลี่ยนรายวัน)
  — ห้ามแก้ `compute_risk_level`/`_calc_swab` จนกว่าเจ้าของอนุมัติ risk-level-review
- รายชื่อผู้ใช้ทุกชุดต้องดึงแบบแบ่งหน้า (`database._fetch_all`) — Supabase ตัดที่ 1,000 แถวเงียบ ๆ
- โค้ดเดิมนิยม "ปิดไว้ไม่ลบ" (เช่น ML yield, `_build_fertilizer_section`) — ตรวจก่อนว่าฟังก์ชันยังถูกเรียกจริงไหม

## กติกาการทำงาน (สรุปจาก spec หัวข้อ 2 — ต้องทำตามทุกข้อ)

**ขั้นตอน**
- ทำทีละ Phase: สำรวจ → รายงาน → เสนอแผน → **รออนุมัติ** ก่อนแก้โค้ด
- แยก branch ต่อ Phase (`feat/v2-phase1-...`) commit ย่อย สื่อความหมาย
- ห้ามทำฟีเจอร์เดิมพัง ถ้าต้องเปลี่ยนพฤติกรรมเดิมให้แจ้งก่อน
- ไม่แน่ใจให้ถาม อย่าเดา — รวมคำถามเป็นชุดเดียว

**ห้ามทำโดยไม่ถาม**
- migrate / ลบ / เปลี่ยน schema ข้อมูลที่มีอยู่ (ต้องมีแผน backup + rollback)
- เปลี่ยน hosting, ฐานข้อมูล, บริการที่มีค่าใช้จ่าย
- เปลี่ยน LIFF ID, channel หรือค่าใน LINE Developers
- เพิ่ม dependency ขนาดใหญ่

**ความปลอดภัย**
- secrets อยู่ใน env เท่านั้น ห้าม commit ห้าม log (repo GitHub นี้เป็น **public**)
- ตรวจ LINE ID token ฝั่ง server — ห้ามเชื่อ `userId` จาก client
- หลัง `liff.init()` ลบ `code`/`state` ออกจาก URL ด้วย `history.replaceState`
- ตรวจสิทธิ์ตามบทบาทที่ server ทุก endpoint, จำกัดขนาด/ชนิดไฟล์อัปโหลด, rate limit endpoint สำคัญ
- ลบ EXIF ที่ไม่จำเป็นจากรูป (เก็บพิกัด/เวลาแยกใน DB)

**UX เกษตรกร**
- ภาษาไทยทั้งหมด คำง่าย ห้ามศัพท์เทคนิคบนจอผู้ใช้ (DEM, NDVI, BSI, polygon ฯลฯ)
- mobile-first ใน LINE, ตัวอักษรใหญ่, ปุ่ม ≥ 44px + ไอคอน
- ทุกการรอต้องบอกว่ากำลังทำอะไร, รองรับสัญญาณอ่อน (อัปโหลด retry ได้ ไม่ทำข้อมูลหาย)
- **ห้ามแสดงตัวเลขความแม่นยำหรือคำกล่าวอ้างที่ยังไม่ได้ทดสอบจริง**
- คำแนะนำ "แก้อย่างไร" ต้องมาจากตารางที่คนเขียน/ตรวจเท่านั้น (สถานะ draft/reviewed) ห้าม AI แต่งสด
- ค่าทางการเกษตร (threshold ต่อพืช ฯลฯ) ห้ามเดา — เว้นว่างให้ผู้เชี่ยวชาญกรอก

**คุณภาพ**
- test สำหรับ rule engine, การตรวจสิทธิ์, API หลัก
- seed data แปลงตัวอย่างราชบุรี 5–10 แปลง หลายพืช
- error logging ทั้ง server และ client (LIFF ส่ง error กลับมาดูได้)
- เอกสารใน `docs/` + อัปเดต README และไฟล์นี้เมื่อจบแต่ละ Phase
