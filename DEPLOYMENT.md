# 🚀 GEOai Deployment Guide

ระบบ GEOai พร้อม deploy สำหรับ production (Railway recommended)

---

## 📋 เตรียมของ

### 1. GitHub Repository
```bash
git push origin master
```
ตรวจว่า branch `master` มี commit ล่าสุด (redesign commit)

### 2. สร้าง API Keys

#### A. Google Earth Engine (GEE)
1. เข้า [Google Cloud Console](https://console.cloud.google.com)
2. สร้าง Service Account (IAM → Service Accounts)
3. Download `.json` key file
4. รันคำสั่ง:
```bash
python -c "import json; key=json.load(open('path/to/key.json')); print(json.dumps(key))"
```
5. Copy ผลลัพธ์ → `GEE_KEY_JSON` env var

#### B. Supabase
1. เข้า [Supabase Dashboard](https://supabase.com/dashboard)
2. สร้าง project หรือ select existing
3. Settings → API → Copy:
   - `SUPABASE_URL` (e.g., `https://xxxx.supabase.co`)
   - `SUPABASE_SERVICE_ROLE_KEY` (secret key)
4. SQL Editor → paste `supabase/schema.sql` + Run (สร้าง tables)

#### C. LINE Developers
1. เข้า [LINE Developers Console](https://developers.line.biz)
2. Create Provider → Bot:
   - ได้ `LINE_CHANNEL_ACCESS_TOKEN`
   - ได้ `LINE_CHANNEL_SECRET`
3. Messaging API → LIFF:
   - Add LIFF app → Endpoint: `https://<YOUR_DOMAIN>/liff/`
   - Copy LIFF ID → `LIFF_URL=https://liff.line.me/<LIFF_ID>`
4. Webhook URL: `https://<YOUR_DOMAIN>/webhook`

#### D. GISTDA Sphere Map (Optional)
เข้า [https://sphere.gistda.or.th](https://sphere.gistda.or.th) ขอ API Key → `SPHERE_KEY`
(Dev mode สามารถ skip — ใช้ Leaflet mock แทน)

#### E. Admin API Key
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
→ `ADMIN_API_KEY`

---

## 🚢 Deploy to Railway

### Step 1: Connect Repository
1. เข้า [Railway.app](https://railway.app)
2. "Create New Project" → Connect GitHub repo (`your-username/GEOai`)
3. Select branch: `master`

### Step 2: Set Environment Variables
Railway Dashboard → Variables:
```
LINE_CHANNEL_ACCESS_TOKEN = [ค่าจาก LINE Developers]
LINE_CHANNEL_SECRET = [ค่าจาก LINE Developers]
GEE_SERVICE_ACCOUNT = your-service-account@project.iam.gserviceaccount.com
GEE_KEY_JSON = {"type":"service_account",...}
SUPABASE_URL = https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY = [ค่าจาก Supabase]
LIFF_URL = https://liff.line.me/YOUR_LIFF_ID
API_BASE_URL = https://geoai-[random].up.railway.app  (ถ้า frontend ต่างโดเมน)
ADMIN_API_KEY = [generated token_urlsafe]
SPHERE_KEY = [optional]
```

### Step 3: Deploy
Railway จะ auto-build จาก `railway.toml`:
- Build: NIXPACKS
- Start: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
- Domain: `https://geoai-[random].up.railway.app`

### Step 4: Verify
```bash
# Test health
curl https://geoai-[random].up.railway.app/health

# Test dashboard
curl https://geoai-[random].up.railway.app/dashboard/

# Test LIFF config
curl https://geoai-[random].up.railway.app/liff/config.js
```

---

## 📱 LINE Bot Setup

### 1. Webhook URL
```
LINE Developers → Messaging API → Webhook URL:
https://geoai-[random].up.railway.app/webhook
```

### 2. LIFF App
```
LINE Developers → LIFF → Endpoint:
https://geoai-[random].up.railway.app/liff/
```

### 3. Rich Menu (Optional)
```bash
cd backend
python rich_menu.py
# ต้องเตรียม rich_menu_image.png (2500×843px)
```

### 4. Test Bot
- เพิ่ม bot เป็น friend บน LINE
- ส่ง message → bot ตอบ
- กด button "วิเคราะห์แปลง" → เปิด LIFF

---

## 🔒 Security Checklist

- [ ] `ADMIN_API_KEY` set ≠ empty (dev mode → production mode)
- [ ] `GEE_KEY_JSON` ไม่ commit ลง git (ใช้ env var)
- [ ] `SUPABASE_SERVICE_ROLE_KEY` ≠ anon key
- [ ] `LINE_CHANNEL_SECRET` verify webhook signature (`webhook.py`)
- [ ] HTTPS enforced (Railway default)
- [ ] Database schema created (`supabase/schema.sql`)

---

## 📊 Database Setup

### Create Tables
```sql
-- Supabase → SQL Editor → Paste from supabase/schema.sql
-- v3 schema includes: users, plots, analyses, schema migrations
```

### Seed Demo Data (Optional)
```bash
cd backend
python -c "from database import seed_demo_data; seed_demo_data()"
```

---

## 🧪 Post-Deploy Test

### 1. Health Check
```bash
curl https://geoai-[DOMAIN]/health
# Expected: {"status":"ok","service":"GEOai",...}
```

### 2. Admin Dashboard
```
https://geoai-[DOMAIN]/dashboard/
[ใส่ ADMIN_API_KEY]
```
Expected: 8 KPI cards + map + report list

### 3. LIFF (from LINE)
- Add bot as friend
- Click "วิเคราะห์แปลง" button → opens LIFF URL
- Can use GPS, draw polygon, analyze

### 4. Analyze Endpoint (Test)
```bash
curl -X POST https://geoai-[DOMAIN]/analyze \
  -H "Content-Type: application/json" \
  -d '{"lat":12.61,"lng":102.10,"user_id":"test","display_name":"Test User"}'
```

---

## 🐛 Troubleshooting

### GEE Error: `ee.EEException`
- ✅ Check `GEE_KEY_JSON` (valid JSON?)
- ✅ Check service account has "Earth Engine API" enabled
- ✅ Check service account has Editor role on GEE project

### Supabase Error: 401 / 403
- ✅ Check `SUPABASE_URL` correct
- ✅ Check `SUPABASE_SERVICE_ROLE_KEY` (not anon key)
- ✅ Check tables created (run schema.sql)

### LINE Webhook: 401
- ✅ Check `LINE_CHANNEL_SECRET` correct
- ✅ Check webhook URL registered in Developers Console
- ✅ Check signature verification in `webhook.py`

### LIFF Blank / Not Loading
- ✅ Check `LIFF_URL` in Developers Console matches deployed domain
- ✅ Check `/liff/config.js` loads (browser console)
- ✅ Check LIFF app scope includes `profile`

---

## 📞 Support

หากมีปัญหา ตรวจ logs ใน Railway Dashboard:
- Deploy Logs → build errors
- Service Logs → runtime errors (check exception stack)

---

**Last Updated:** 2026-06-12  
**Status:** ✅ Ready to Deploy
