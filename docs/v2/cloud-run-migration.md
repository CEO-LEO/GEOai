# ย้าย IAM ROOT จาก Render ไป Google Cloud Run (30 ต.ค. 2569)

ผู้อ่าน: เจ้าของโปรเจกต์ · เหตุผล: Render ระงับบริการเรื่องค่าใช้จ่าย (29 ต.ค.) · เจ้าของเลือก Cloud Run

## สรุป

- **ค่าใช้จ่ายคาดว่า ~$0/เดือน** สำหรับผู้ใช้ระดับตอนนี้ (อยู่ในโควตาฟรีของ Cloud Run: 2 ล้านคำขอ/เดือน + เวลาประมวลผลฟรี)
  มีค่าเล็กน้อยได้จากที่เก็บ image (Artifact Registry) — **ตั้ง budget alert ไว้ $5** (ขั้นที่ 2)
- **แลกกับ:** ไม่มีคนใช้สักพัก service จะหลับ → คนแรกที่เปิดแอปรอเพิ่มหลายวินาที (Render starter ไม่หลับ)
- ใช้โปรเจกต์ Google Cloud **เดียวกับ Earth Engine** (เรื่องไลเซนส์ Earth Engine เชิงพาณิชย์ยังรอตอบเหมือนเดิม)
- ฐานข้อมูล Supabase ไม่ย้าย · ภูมิภาค **Tokyo (asia-northeast1)** ใกล้ฐานข้อมูลที่โตเกียวและเป็นราคากลุ่มถูกกว่าสิงคโปร์
- ระหว่างย้าย แอปยังใช้ไม่ได้เหมือนตอนนี้ (Render ระงับอยู่แล้ว) — ไม่มีช่วงที่แย่ลงกว่าเดิม

## ส่วนที่เจ้าของต้องทำ (Claude ทำแทนไม่ได้: ต้องล็อกอินบัญชี Google และผูกบัตร) — ~15–20 นาที

1. เปิด https://console.cloud.google.com → เลือก**โปรเจกต์ที่ใช้กับ Earth Engine**
   (ชื่อโปรเจกต์คือส่วนท้ายของอีเมล service account ที่ใช้กับ Earth Engine `…@<ชื่อโปรเจกต์>.iam.gserviceaccount.com`)
2. **Billing** → ผูกบัญชีเรียกเก็บเงิน (Cloud Run ต้องมี แม้ใช้ไม่เกินโควตาฟรี) →
   **Budgets & alerts** → สร้างงบ **$5/เดือน** แจ้งเตือนที่ 50% / 90% / 100%
3. **APIs & Services → Enable APIs** เปิด 3 ตัว: **Cloud Run Admin API**, **Cloud Build API**, **Artifact Registry API**
4. **IAM & Admin → Service Accounts → Create service account** ชื่อ `iamroot-deployer` แล้วให้บทบาท:
   Cloud Run Admin · Cloud Build Editor · Artifact Registry Administrator · Service Account User ·
   Storage Admin · Service Usage Consumer
5. เปิด service account นั้น → **Keys → Add key → Create new key → JSON** → บันทึกไฟล์ที่
   `C:\GEOai-20261009T064220Z-1-001\GEOai\backend\gcp-deployer.json`
   (ไฟล์นี้ถูกกันไม่ให้ขึ้น git และไม่เข้า image แล้ว) — **ห้ามส่งเนื้อไฟล์ในแชท**
6. พิมพ์บอก Claude ว่า "**ตั้ง Cloud Run เสร็จแล้ว**"

## ส่วนที่ Claude ทำต่อ (หลังขั้นที่ 6)

1. ตั้ง GitHub secret `GCP_SA_KEY` จากไฟล์ในขั้นที่ 5 (ไม่พิมพ์ค่า) + variable `GCP_PROJECT_ID`, `CLOUD_RUN_ENABLED=true`
2. สั่ง deploy ผ่าน GitHub Actions (job "Deploy to Cloud Run" ใน CI) → ได้ URL `https://iamroot-….run.app`
3. คัดลอกค่า env ทั้งหมดจาก Render ไป Cloud Run ผ่าน API (ไม่พิมพ์ค่า secret)
4. smoke test ทุกข้อกับ URL ใหม่ + ตรวจภาษาไทยในรูปแผนที่/รายงาน PDF + ทดลองงานตามเวลาแบบรอจบ

## ส่วนที่เจ้าของต้องทำหลัง Claude ตรวจผ่าน (LINE Developers) — ~5 นาที

1. https://developers.line.biz → channel **LINE Login** → แท็บ LIFF → แอป IAM ROOT → **Endpoint URL** = `https://<URL ใหม่>/liff/`
2. channel **Messaging API** → **Webhook URL** = `https://<URL ใหม่>/webhook` → กด **Verify** (ต้องขึ้น Success) · Use webhook = เปิด
3. พิมพ์บอก Claude ว่า "**เปลี่ยน URL ใน LINE แล้ว**" → Claude ตั้ง `APP_BASE_URL` ให้งานตามเวลา (สแกนรายวัน/เตือนฝน) ยิงไปที่ใหม่ แล้วตรวจรอบแรก

ลิงก์ LIFF ที่ผู้ใช้กด (`https://liff.line.me/…`) และ Rich Menu **ไม่ต้องเปลี่ยน**

## หลังย้าย 1 สัปดาห์

- ถ้าทุกอย่างปกติ: ลบ service บน Render (หรือปล่อยไว้ในสถานะระงับ ไม่มีค่าใช้จ่ายเพิ่ม) และลบ `RENDER_API_KEY`
- ตรวจหน้า Billing ของ Google Cloud ว่ายอดเป็นไปตามคาด

## สิ่งที่เปลี่ยนในโค้ด (branch `feat/cloud-run`)

| ไฟล์ | เปลี่ยน |
|---|---|
| `Dockerfile` | ใช้ `$PORT` ของ Cloud Run · ติดตั้ง libfribidi (สระ/วรรณยุกต์ไทยในรูปและ PDF) · รันด้วยผู้ใช้ที่ไม่ใช่ root |
| `.dockerignore`, `.gcloudignore` | กัน `.env`, ไฟล์ key, backup, ข้อมูลภาคสนาม ไม่ให้เข้า image |
| `backend/auth.py` | ปิดผู้ใช้จำลอง (dev) บน Cloud Run ด้วย (env `K_SERVICE`) เหมือนบน Render |
| `backend/main.py` | `/admin/trigger/*?wait=true` รองานจบในคำขอเดียว — Cloud Run ลด CPU หลังตอบคำขอ งานเบื้องหลังจะไม่จบ |
| `.github/workflows/deploy.yml` | job deploy ไป Cloud Run (ทำงานเมื่อตั้ง `CLOUD_RUN_ENABLED=true`) |
| `.github/workflows/keep-alive.yml` | ที่อยู่เซิร์ฟเวอร์จาก variable `APP_BASE_URL` + `wait=true` + รอได้ 15 นาที |

## ความเสี่ยงที่รู้

- **Cold start:** หลับแล้วตื่นใช้เวลาหลายวินาที (โหลด Earth Engine ตอนเริ่ม) — แชทบอท LINE ยังตอบได้เพราะ LINE รอ webhook ได้
- ตัวจำกัดคำขอ/แคชอยู่ในหน่วยความจำต่อ instance — ถ้า Cloud Run เปิดหลาย instance พร้อมกัน ค่าจะแยกกัน (จำกัด max 3)
- สถิติการใช้งาน (events) บันทึกหลังตอบคำขอ บน Cloud Run อาจบันทึกช้า/หายได้บ้างเมื่อ instance หลับทันที — ไม่กระทบการใช้งาน
