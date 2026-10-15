# CLAUDE.md — IAM ROOT (ตรวจต้นไม้)

แอป LINE LIFF วิเคราะห์แปลงสวนจากภาพดาวเทียม (ความชื้น/จุดน้ำขัง/ทิศทางน้ำ) สำหรับเกษตรกรและเจ้าหน้าที่เกษตร
กำลังอัปเกรดเป็น **v2** เพื่อใช้กับเครือข่ายเกษตรจังหวัดราชบุรี (B2G)

- **Spec ฉบับเต็ม:** [docs/IAMROOT_V2_PROMPT.md](docs/IAMROOT_V2_PROMPT.md) — อ่านก่อนเริ่มงาน v2 ทุกครั้ง
- **สถานะล่าสุด:** Phase 0 เสร็จ (2026-10-15) รายงานอยู่ที่ [docs/v2/phase0-report.md](docs/v2/phase0-report.md)
  — **รอเจ้าของโปรเจกต์อนุมัติแผนก่อนแก้โค้ด**
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

ไฟล์ deploy อื่น (`railway.toml`, `vercel.json`, `Dockerfile`, `docker-compose.yml`, `nginx/`, `.github/workflows/deploy.yml`) **ไม่ได้ใช้งานจริง**
และ `deploy.yml` ทริกเกอร์บน branch `main` ซึ่งไม่มีอยู่ → CI ไม่เคยรัน

## คำสั่ง

```bash
# ต้องใช้ Python 3.11 (เวอร์ชันที่ pin ไว้ติดตั้งบน 3.13 ไม่ได้)
cd backend
pip install -r requirements.txt           # root requirements.txt ต้องตรงกับ backend/requirements.txt เสมอ
uvicorn main:app --reload --port 8000     # LIFF: /liff/  dashboard: /dashboard/  health: /health

# test — ใส่ env ปลอมเสมอ (main.py เรียก load_dotenv() จะดึง backend/.env ตัวจริงมาใช้ถ้าไม่ override)
ADMIN_API_KEY= LINE_CHANNEL_ACCESS_TOKEN=test LINE_CHANNEL_SECRET=test \
GEE_SERVICE_ACCOUNT=t@x.iam.gserviceaccount.com GEE_KEY_JSON='{"type":"service_account"}' \
SUPABASE_URL=https://example.supabase.co SUPABASE_SERVICE_ROLE_KEY=test \
LIFF_URL=https://liff.line.me/0000000000-AAAAAAAA \
pytest test_system.py -q                  # 80 tests (2026-10-15: ผ่านครบเมื่อ ADMIN_API_KEY ว่าง)
```

- **Deploy:** push `master` → Render auto-deploy. Build command จริงตั้งใน Render Dashboard (ไม่ sync กับ `render.yaml`)
- **Migration DB:** ไฟล์ `supabase/migrate_vN.sql` รันมือใน Supabase SQL Editor (ล่าสุด v11) — ไม่มีตารางบันทึกว่ารันอะไรไปแล้ว
- ไม่มี GEE key ในเครื่อง → `analyze_durian_plot()` ใช้ mock data (`_mock_analyze`)

## Env (ค่าอยู่ใน Render Dashboard และ `backend/.env` ห้าม commit)

`LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `GEE_SERVICE_ACCOUNT`, `GEE_KEY_JSON`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `LIFF_URL`, `API_BASE_URL`, `ADMIN_API_KEY`, `SPHERE_KEY` — ดู [backend/.env.example](backend/.env.example)

## Data model ปัจจุบัน (Supabase)

`users` (user_id = LINE userId, notify_weekly, notify_daily_digest, notify_hour) · `plots` (lat/lng, polygon JSONB `[[lng,lat],...]`, thumbnail_url)
· `analyses` (คอลัมน์ flat + `full_data` JSONB) · `grid_snapshots` · `iot_readings` · `field_observations` · `scheduler_runs`
— schema: [supabase/schema.sql](supabase/schema.sql) + migrate_v3…v11

## จุดที่ต้องรู้ก่อนแก้

- **Logic สถานะความชื้น** อยู่ที่ `_calc_swab()` ใน gee_analysis.py — threshold ซ้ำอยู่ใน rule_engine.py และสูตรสีถูก "พอร์ตซ้ำ" ไว้ 3 ที่
  (gee_analysis `_plot_relative_normalizer`, map_image `build_normalizer`, liff `buildSwabNormalizer`) **แก้ที่หนึ่งต้องแก้ทุกที่**
- ระดับความเสี่ยงรวมต้องมาจาก `compute_risk_level()` / `full_data.overall_risk_level` ที่เดียว
- สีบนแผนที่เป็น **สีเทียบกันเองภายในแปลง** (contrast stretch) ไม่ใช่ค่าสัมบูรณ์
- ทิศทางน้ำมาจาก SRTM 30 m (`slope >= 1.5°`) — ใช้ไม่ได้กับพื้นที่ราบ/สวนยกร่องภาคกลาง (ดูรายงาน Phase 0)
- Cache ผลวิเคราะห์ ([backend/cache.py](backend/cache.py)) อยู่ในแรม key = lat/lng ปัด 3 ตำแหน่ง ไม่รวม polygon
- Rate limit ([backend/middleware.py](backend/middleware.py)) ในแรม 10 req/นาที/IP **รวมไฟล์ static ด้วย**
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
