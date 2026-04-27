# 🌿 GEOai v2 — ระบบตรวจวิเคราะห์สวนทุเรียนด้วยดาวเทียม

ระบบ Deep Tech สำหรับเกษตรกรชาวสวนทุเรียนภาคตะวันออก (จันทบุรี/ตราด)  
ใช้ข้อมูลดาวเทียม Sentinel-1/2 วิเคราะห์แปลงอัตโนมัติ 6 ด้าน:  
🌿 ความสมบูรณ์พืช | 💧 ความชื้นดิน | ⛰️ ระดับพื้นที่ | 🌍 การเคลื่อนตัวพื้นดิน | 🧪 คำแนะนำปุ๋ย N-P-K | 📊 ประเมินผลผลิต  
ส่งผลวิเคราะห์ + แจ้งเตือนความเสี่ยงผ่าน LINE OA อัตโนมัติ

## สถาปัตยกรรมระบบ

```
เกษตรกร ─── LINE OA ─── LIFF (Map UI)
                │                │
           Webhook          POST /analyze
                │                │
                └──── FastAPI ───┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         Google EE   Supabase   Open-Meteo
        (ดาวเทียม)    (DB)      (พยากรณ์ฝน)
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | LINE LIFF 2.x + GISTDA Sphere SDK |
| Backend | Python 3.11, FastAPI 0.111.0, Uvicorn |
| Satellite | Google Earth Engine (Sentinel-1 GRD, Sentinel-2 SR, SRTM DEM) |
| Weather | Open-Meteo API (ฟรี ไม่มี key) |
| Database | Supabase (PostgreSQL via REST API) |
| Messaging | LINE Messaging API (Push, Reply, Flex Message, Rich Menu) |
| Deploy | Railway (primary) / Docker + Nginx (VPS) |
| CI/CD | GitHub Actions |

## ⚡ Quick Start

### 1. ติดตั้ง Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. สร้างไฟล์ `.env` (แนะนำ: ใช้ Setup Wizard)

```bash
cd backend
python setup_wizard.py
```

Wizard จะพาตั้งค่าทีละขั้น + ทดสอบ connection + สร้างตาราง Supabase อัตโนมัติ

หรือสร้างเองด้วย:
```bash
cp .env.example .env
# แก้ไขค่าต่าง ๆ ตามคำแนะนำในไฟล์
```

ตัวแปรที่จำเป็น:

| ตัวแปร | วิธีได้มา |
|--------|---------|
| `LINE_CHANNEL_ACCESS_TOKEN` | [LINE Developers Console](https://developers.line.biz/) → Messaging API |
| `LINE_CHANNEL_SECRET` | LINE Developers → Basic settings |
| `GEE_SERVICE_ACCOUNT` | [Google Cloud Console](https://console.cloud.google.com/) → IAM → Service Accounts |
| `GEE_KEY_JSON` | Download key.json → `python -c "import json; print(json.dumps(json.load(open('key.json'))))"` |
| `SUPABASE_URL` | [Supabase Dashboard](https://supabase.com/dashboard) → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → service_role (secret) |
| `LIFF_URL` | LINE Developers → LIFF → endpoint URL |
| `SPHERE_KEY` | GISTDA Sphere API Key |

### 3. สร้างตาราง Supabase

เปิด Supabase Dashboard → SQL Editor → New query  
วางเนื้อหาจากไฟล์ `supabase/schema.sql` → Run

### 4. รันเซิร์ฟเวอร์

```bash
cd backend
uvicorn main:app --reload --port 8000
```

เปิด:
- API: http://localhost:8000/health
- LIFF: http://localhost:8000/liff/
- Dashboard: http://localhost:8000/dashboard/

### 5. ตั้งค่า LINE Webhook

LINE Developers Console → Messaging API → Webhook URL:
```
https://YOUR_DOMAIN/webhook
```

### 6. ตั้งค่า LIFF

LINE Developers → LIFF → เพิ่ม LIFF app:
- Endpoint URL: `https://YOUR_DOMAIN/liff/`
- Scope: `profile`

Dashboard และ LIFF จะโหลดค่าจาก backend อัตโนมัติผ่าน `config.js` ดังนั้นหลังจากตั้งค่า `.env` แล้วไม่ต้องแก้ค่าในไฟล์ `liff/index.html` ด้วยตัวเอง

### 7. สร้าง Rich Menu (optional)

```bash
cd backend
python rich_menu.py
# ต้องเตรียมไฟล์ rich_menu_image.png (2500×843px)
```

---

## 🚀 Deploy

### Railway (แนะนำ)

1. Push code ไป GitHub
2. เชื่อม repo กับ [Railway](https://railway.app/)
3. ตั้ง environment variables ทั้ง 8 ตัวใน Railway Dashboard
4. Railway จะ deploy อัตโนมัติจาก `railway.toml`

### Docker (VPS)

```bash
# สร้าง .env จาก .env.example ก่อน
docker compose up -d --build
```

ต้องมี:
- SSL certificate ใน `nginx/certs/`
- จุด DNS ไปที่ IP ของ server

### CI/CD (GitHub Actions)

Push ไป `main` → auto test → auto deploy Railway  
ต้องตั้ง GitHub Secret: `RAILWAY_TOKEN`

---

## 📡 API Endpoints

| Method | Path | คำอธิบาย |
|--------|------|---------|
| `GET` | `/health` | สถานะเซิร์ฟเวอร์ + cache stats |
| `POST` | `/analyze` | วิเคราะห์แปลง (lat, lng, user_id) |
| `GET` | `/plots/{user_id}` | ดึงแปลงทั้งหมดของ user |
| `GET` | `/plots/{user_id}/{plot_id}/history` | ประวัติวิเคราะห์รายแปลง |
| `DELETE` | `/plots/{user_id}/{plot_id}` | ลบแปลง |
| `PATCH` | `/user/{user_id}/notify?enabled=true` | เปิด/ปิดแจ้งเตือน |
| `GET` | `/admin/stats` | สถิติรวม (จำนวน, ความเสี่ยง, ดินไม่เสถียร, ผลผลิตเฉลี่ย, NDVI เฉลี่ย) |
| `GET` | `/admin/reports?limit=100` | รายงานทั้งหมด |
| `GET` | `/admin/reports/export.csv` | ดาวน์โหลด CSV |
| `GET` | `/admin/logs?level=ERROR&limit=50` | Log entries ล่าสุด (monitoring) |
| `POST` | `/webhook` | LINE webhook endpoint |

> **หมายเหตุ:** endpoint ที่ขึ้นต้นด้วย `/admin/*` ต้องส่ง header `X-Admin-Key` (ตั้งค่าผ่าน `ADMIN_API_KEY` env var)

---

## 🧪 Tests

```bash
cd backend
pytest test_system.py -v
```

ทดสอบ: Rule Engine, Flex Messages, Cache, API endpoints, Weather Alert, Signature Verification,  
GEE Retry, Duplicate Plot Detection, Log Buffer, Admin Logs  
ทุก test ใช้ mock — ไม่ต้องมี GEE key / LINE token จริง

---

## 📁 โครงสร้างโปรเจค

```
GEOai/
├── backend/
│   ├── main.py              # FastAPI app + endpoints
│   ├── gee_analysis.py      # Google Earth Engine pipeline
│   ├── rule_engine.py       # กฎวิเคราะห์ความเสี่ยง
│   ├── flex_messages.py     # LINE Flex Message builder
│   ├── weather_alert.py     # พยากรณ์ฝน Open-Meteo
│   ├── webhook.py           # LINE webhook handler
│   ├── database.py          # Supabase REST API layer
│   ├── scheduler.py         # APScheduler (weekly scan + rain alert)
│   ├── line_sender.py       # LINE Push Message sender
│   ├── cache.py             # In-memory LRU cache
│   ├── middleware.py        # Rate limiting
│   ├── log_buffer.py        # In-memory ring buffer (monitoring)
│   ├── rich_menu.py         # สร้าง LINE Rich Menu
│   ├── i18n.py              # Internationalization (TH/EN)
│   ├── setup_wizard.py      # Interactive setup wizard
│   ├── test_system.py       # Automated tests (35 tests)
│   ├── test_local.py        # GEE integration test
│   ├── requirements.txt
│   └── .env.example
├── liff/
│   └── index.html           # LIFF frontend (map + plot management)
├── dashboard/
│   └── index.html           # Admin dashboard
├── supabase/
│   ├── schema.sql           # Database schema (v3)
│   └── migrate_v3.sql       # v2→v3 migration (displacement, fertilizer, yield, impact)
├── nginx/
│   └── geoai.conf           # Nginx config (VPS deploy)
├── .github/workflows/
│   └── deploy.yml           # CI/CD pipeline
├── Dockerfile
├── docker-compose.yml
├── railway.toml
└── .gitignore
```

---

## 🔔 Scheduled Jobs

| Job | เวลา | ทำอะไร |
|-----|------|--------|
| Weekly Scan | จันทร์ 07:00 | สแกนทุกแปลง → แจ้งเตือนถ้าเสี่ยงสูง |
| Rain Alert | จันทร์ 07:30 | เช็คพยากรณ์ฝน 7 วัน → เตือนก่อนฝนหนัก |

เฉพาะ user ที่เปิด `notify_weekly = true` เท่านั้นที่จะได้รับ push

---

## �️ Production Features

| Feature | รายละเอียด |
|---------|----------|
| **Admin Auth** | `X-Admin-Key` header สำหรับ /admin/* endpoints |
| **GEE Retry** | Exponential backoff (2s, 4s, 8s) สำหรับ GEE API calls |
| **Duplicate Detection** | ตรวจซ้ำอัตโนมัติ — แปลงใกล้กัน ±111m คืน plot เดิม |
| **Monitoring Logs** | In-memory ring buffer 200 entries, ดูผ่าน /admin/logs |
| **Rate Limiting** | 3 req/min สำหรับ /analyze, 10 req/min ทั่วไป |
| **Cache** | LRU 500 entries, TTL 6 ชม., precision 3 ทศนิยม (~111m) |
| **NDVI Trend Chart** | Chart.js กราฟแนวโน้ม NDVI 30 วันใน Dashboard |

---

## �📊 Satellite Data ที่ใช้

| Data Source | ข้อมูล | ใช้ทำอะไร |
|-------------|-------|---------|
| Sentinel-2 SR Harmonized | NDVI (ปัจจุบัน vs ปีก่อน) | วัดความสมบูรณ์ใบ + ประเมินผลผลิต + แนะนำปุ๋ย |
| Sentinel-1 GRD (IW, VV/VH) | Soil moisture + SAR backscatter | ตรวจจับน้ำขัง + วัดการเปลี่ยนแปลงพื้นดิน (displacement) |
| USGS SRTM 30m | Elevation + diff | ระบุแปลงต่ำ (เสี่ยงน้ำท่วม) + ปรับค่าปุ๋ย/ผลผลิตตามพื้นที่ |

---

## License

Private — Internal use only
