# 🌿 IAM ROOT — ตรวจต้นไม้และแปลงสวนจากภาพดาวเทียม

แอป LINE (LIFF) ให้เกษตรกรวาดขอบเขตแปลง แล้วดูว่าส่วนไหนของแปลงชื้นหรือแห้งกว่าส่วนอื่น จุดที่น้ำไหลมารวม
และคำแนะนำว่าควรทำอะไร — พัฒนาร่วมกับ**สำนักงานเกษตรจังหวัดราชบุรี** (เวอร์ชัน v2)
ชื่อในโค้ดเดิมบางส่วนยังเป็น "GEOai" และฟังก์ชัน `*_durian_*` (ยุคที่เน้นสวนทุเรียนจันทบุรี)

> **สถานะ (ต.ค. 2569):** Phase 1 (1a–1g) อยู่บน production แล้ว — รายละเอียดและการตัดสินใจทุกข้อ:
> [docs/v2/decisions.md](docs/v2/decisions.md) · แผนทั้งหมด: [docs/IAMROOT_V2_PROMPT.md](docs/IAMROOT_V2_PROMPT.md)
> · ระดับความเสี่ยงอยู่ในโหมด **"กำลังปรับปรุง"** ระหว่างตรวจภาคสนาม ([docs/v2/risk-display-interim.md](docs/v2/risk-display-interim.md))

## ทำอะไรได้บ้าง

| ส่วน | รายละเอียด |
|---|---|
| แปลง | วาดขอบเขต/ปักจุด · เลือกพืชได้หลายชนิด · ประเภทสวน (แปลงเรียบ / สวนยกร่อง / ยกแปลงผัก) · แก้ขอบเขตโดยประวัติไม่หาย · รวมประวัติจากแปลงซ้ำ |
| คำเตือนขอบเขต | ตอนบันทึก บอกถ้าขอบเขตมีบ้าน/ถนนปน ปักจุดเดียว หรือเล็กมาก (ไม่บล็อก กด "บันทึกตามนี้" ได้) — เกณฑ์ใน [backend/config/boundary_rules.json](backend/config/boundary_rules.json) |
| แผนที่ความชื้นในแปลง | ตาราง 10×10 ม. จาก Sentinel-1 สีเทียบกันเองในแปลง + ประโยคสรุป · ลูกศรทางน้ำ/จุดน้ำรวม (ซ่อนสำหรับสวนยกร่อง) |
| คำแนะนำ "แก้อย่างไร" | จากตาราง พืช × ปัญหา × ความรุนแรง ที่คนเขียน/ตรวจ (ไม่ใช้ AI แต่ง) ทุกข้อมีป้าย "ร่าง" หรือ "ตรวจแล้วโดย…" · แก้/อนุมัติที่ `/dashboard/recommendations.html` |
| เทียบกับปกติของแปลงเอง | ชื้น/แห้งกว่าปกติของช่วงเดียวกันในปีก่อน ๆ (รันคู่กับวิธีเดิม — [docs/v2/risk-level-review.md](docs/v2/risk-level-review.md)) |
| แชร์ | รายงาน PDF (ลิงก์ 7 วัน) + แชร์การ์ดใน LINE (`liff.shareTargetPicker`) |
| LINE OA | ปุ่มเมนูดูผล (reply ไม่กินโควตา) · เตือนฝนจากพยากรณ์ · สรุปรายสัปดาห์ (ผู้ใช้เลือกเปิด) · คุมโควตาข้อความ |

**ไม่แสดงกับเกษตรกร (ปิดไว้):** ปุ๋ย กก./ต้น, ผลผลิต กก./ไร่, ค่า NDVI/BSI ดิบ — เป็นสูตรทุเรียนที่ยังไม่ได้สอบเทียบ
(เปิดได้ด้วย `SHOW_UNVERIFIED_AGRONOMY=true`)

## สถาปัตยกรรม

```
เกษตรกร ── LINE OA ── LIFF (liff/index.html, GISTDA Sphere Map)
              │              │  LINE ID token (ตรวจที่ server)
          Webhook        REST API
              └── FastAPI (backend/main.py) ── Render (srv IAMROOT, auto-deploy จาก master)
                     │
     ┌───────────────┼──────────────────┬──────────────────┐
 Google Earth     Supabase           Supabase          Open-Meteo
 Engine (S1/S2,   Postgres (REST,    Storage (รูปแผนที่  (พยากรณ์ฝน)
 SRTM, Dynamic    RLS backend only)  public / PDF private)
 World, HAND)
```

งานตามเวลา: GitHub Actions [keep-alive.yml](.github/workflows/keep-alive.yml) เรียก `/admin/trigger/*` (สแกนรายวัน, เตือนฝน)

## เริ่มใช้งานในเครื่อง

```bash
# Python 3.11 (เวอร์ชันที่ pin ไว้ติดตั้งบน 3.13 ไม่ได้)
cd backend
pip install -r requirements.txt          # root requirements.txt ต้องตรงกับ backend/requirements.txt
cp .env.example .env                     # แล้วกรอกค่า — ห้าม commit .env
uvicorn main:app --reload --port 8000    # /liff/  /dashboard/  /health

# เปิด LIFF ในเครื่องโดยไม่มี LINE token (dev เท่านั้น — ปิดเองถ้ามี env RENDER)
IAMROOT_DEV_AUTH_USER=dev-user uvicorn main:app --reload --port 8000
```

ไม่มี Supabase → ใช้ข้อมูลตัวอย่างในหน่วยความจำ (แปลงสมมติราชบุรี 8 แปลง [backend/config/demo_plots_ratchaburi.json](backend/config/demo_plots_ratchaburi.json))
· ไม่มี GEE key → ผลวิเคราะห์เป็น mock

### Env

จำเป็น: `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `GEE_SERVICE_ACCOUNT`, `GEE_KEY_JSON`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `LIFF_URL`, `ADMIN_API_KEY`, `SPHERE_KEY` — ดู [backend/.env.example](backend/.env.example)

ปรับพฤติกรรม: `RISK_DISPLAY_MODE` (interim/legacy), `RISK_ALERTS_ENABLED`, `SHOW_UNVERIFIED_AGRONOMY`,
`RATE_LIMIT_IP_PER_MIN`, `CORS_ALLOW_ORIGINS`, `LINE_LOGIN_CHANNEL_ID`

## Tests

```bash
cd backend
pytest -q     # ไฟล์ test กำหนดใน pytest.ini — ไม่ต้องมี key จริง (conftest.py ตั้งค่าปลอม ไม่อ่าน .env)
```

CI ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)) รัน test บน `master` และ `feat/**` — Render deploy เองเมื่อ push `master`

## ฐานข้อมูล

- schema: [supabase/schema.sql](supabase/schema.sql) + `supabase/migrate_v3…v15.sql` (มี `rollback_vN.sql` คู่กันตั้งแต่ v12)
- ก่อน migration ทุกครั้ง: backup ทุกตาราง + มีไฟล์ rollback · บันทึกใน [docs/v2/decisions.md](docs/v2/decisions.md)
- ทุกตารางเปิด RLS ("backend only") และ role anon/authenticated ไม่มีสิทธิ์ — backend ใช้ service-role ผ่าน REST
- [supabase/seed_demo_ratchaburi.sql](supabase/seed_demo_ratchaburi.sql) = แปลงตัวอย่างสำหรับฐานข้อมูลทดสอบ **ห้ามรันบน production**

## ค่าที่แก้ได้โดยไม่แก้โค้ด

| ไฟล์ | ใช้ทำอะไร |
|---|---|
| [backend/config/crops.json](backend/config/crops.json) | รายชื่อพืช + ค่าทางการเกษตร (ว่างไว้ให้ผู้เชี่ยวชาญกรอก) |
| [backend/config/boundary_rules.json](backend/config/boundary_rules.json) | เกณฑ์คำเตือนขอบเขต |
| ตาราง `recommendation_rules` (หน้า `/dashboard/recommendations.html`) | คำแนะนำ — ร่างตั้งต้นใน [backend/config/recommendation_rules_seed.json](backend/config/recommendation_rules_seed.json) |

## ความปลอดภัย

- repo นี้เป็น **public** — secret อยู่ใน env เท่านั้น (Render / `backend/.env`) ห้าม commit/log
- endpoint ฝั่งผู้ใช้ตรวจ LINE token ที่ server และตรวจว่าแปลงเป็นของผู้ใช้ ([backend/auth.py](backend/auth.py))
- `/admin/*` ใช้ header `X-Admin-Key` (fail-closed)
- รายการแปลงสำหรับตรวจภาคสนามมีพิกัด เก็บใน `fieldwork/` (gitignored)

## เอกสาร

[docs/v2/](docs/v2/) — รายงานแต่ละ Phase, การตัดสินใจ, แผนตรวจภาคสนาม, คำเตือนขอบเขต, ทบทวนระดับความเสี่ยง,
ข้อความทดแทนตัวเลขในสไลด์ ([claims-replacement.md](docs/v2/claims-replacement.md)), ไฟล์คำแนะนำสำหรับผู้ตรวจ
([recommendations-for-review.xlsx](docs/v2/recommendations-for-review.xlsx)) · คู่มือสำหรับนักพัฒนา/AI: [CLAUDE.md](CLAUDE.md)

## ไฟล์ที่ไม่ได้ใช้งานแล้ว

`railway.toml`, `vercel.json`, `Dockerfile`, `docker-compose.yml`, `nginx/`, `pages-deploy/`, `rebuild.py` — ระบบจริงอยู่บน
Render · เอกสารเก่า `STATUS.md`, `BACKTEST_RESULTS.md` มีตัวเลขที่ยังไม่ได้ทดสอบกับข้อมูลจริง (ห้ามนำไปอ้าง)
