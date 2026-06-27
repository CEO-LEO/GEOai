# 🚀 GEOai Quick Start — Production Deploy in 1 Hour

**Goal:** Get GEOai live on Railway + LINE Bot working  
**Prerequisites:** GitHub account, Railway account, LINE account  
**Time:** ~60 minutes

---

## 📋 Checklist (Copy & Print)

```
CREDENTIALS (Collect these):
  [ ] GitHub username & repo URL
  [ ] GEE Service Account email
  [ ] GEE Key JSON (from Google Cloud)
  [ ] Supabase Project URL
  [ ] Supabase Service Role Key
  [ ] LINE Channel Access Token
  [ ] LINE Channel Secret

SETUP (Do these):
  [ ] Create backend/.env file
  [ ] Test backend locally (curl http://localhost:8000/health)
  [ ] Run bash deploy.sh
  [ ] Connect Railway to GitHub
  [ ] Add environment variables in Railway
  [ ] Wait for deployment (2-3 min)
  [ ] Test production URL

FINAL (After live):
  [ ] Register LIFF on LINE Developers
  [ ] Test LIFF in LINE app
  [ ] Invite farmers to test
```

---

## 🔑 Step 1: Collect Credentials (30 min)

### A. GitHub
1. Go to https://github.com/new
2. Create repo: `GEOai`
3. **Copy URL:** `https://github.com/YOUR_USERNAME/GEOai.git`

### B. Google Earth Engine
1. Go to https://console.cloud.google.com
2. Create new project: `geoai-durian`
3. Enable APIs:
   - Earth Engine API
   - Compute Engine API
4. IAM → Service Accounts → Create:
   - Name: `geoai-ee`
5. Create Key → JSON → Download
6. Open file, copy:
   - **`client_email`** → `GEE_SERVICE_ACCOUNT`
   - **Entire JSON** → `GEE_KEY_JSON`
7. Authorize email at https://earthengine.google.com/signup/

### C. Supabase
1. Go to https://supabase.com
2. Create project: `geoai-db` (Region: Singapore)
3. Settings → API:
   - **Copy Project URL** → `SUPABASE_URL`
   - **Copy `service_role` secret** → `SUPABASE_SERVICE_ROLE_KEY`
4. SQL Editor → New Query
5. Paste from `supabase/schema.sql` → Run

### D. LINE Developers
1. Go to https://developers.line.biz
2. Create Provider: `GEOai`
3. Create Channel → Messaging API:
   - Name: `GEOai Durian Bot`
4. Settings → Basic Settings:
   - **Copy Channel Access Token** → `LINE_CHANNEL_ACCESS_TOKEN`
   - **Copy Channel Secret** → `LINE_CHANNEL_SECRET`

### E. Railway
1. Go to https://railway.app
2. Sign up with GitHub

---

## ⚙️ Step 2: Setup Environment (5 min)

Create `backend/.env`:

```bash
# Paste this into PowerShell:
$env_content = @"
LINE_CHANNEL_ACCESS_TOKEN=your_token_here
LINE_CHANNEL_SECRET=your_secret_here
GEE_SERVICE_ACCOUNT=your_service_account@project.iam.gserviceaccount.com
GEE_KEY_JSON={"type":"service_account",...}
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_key_here
LIFF_URL=https://liff.line.me/your_liff_id
ADMIN_API_KEY=will_generate
API_BASE_URL=
SPHERE_KEY=
"@

Set-Content -Path backend/.env -Value $env_content
```

Or manually:
1. Open `backend/.env.example`
2. Copy to `backend/.env`
3. Fill in your credentials

---

## 🧪 Step 3: Test Locally (5 min)

```bash
# In PowerShell:
cd backend
python main.py
# Wait for: "Uvicorn running on http://127.0.0.1:8000"

# In another terminal:
curl http://127.0.0.1:8000/health
# Should see: {"status":"ok","service":"GEOai",...}
```

✅ If you see health response, backend is working!

---

## 🚀 Step 4: Deploy to Railway (10 min)

### Option A: Using Script (Recommended)
```bash
# In PowerShell:
bash deploy.sh
# Follows prompts, tests everything, pushes to GitHub
```

### Option B: Manual Steps
```bash
# 1. Add & commit
git remote add origin https://github.com/YOUR_USERNAME/GEOai.git
git add -A
git commit -m "Add production credentials + deployment"
git push -u origin main

# 2. Go to https://railway.app
# 3. New Project → GitHub Repo → Select GEOai
# 4. Railway will auto-detect main branch
```

---

## 📊 Step 5: Configure Railway (5 min)

1. **Railway Dashboard** → Services → Variables
2. **Add these variables:**
   ```
   LINE_CHANNEL_ACCESS_TOKEN = ...
   LINE_CHANNEL_SECRET = ...
   GEE_SERVICE_ACCOUNT = ...
   GEE_KEY_JSON = {"type":"service_account",...}
   SUPABASE_URL = https://....supabase.co
   SUPABASE_SERVICE_ROLE_KEY = ...
   ADMIN_API_KEY = <generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
   LIFF_URL = https://liff.line.me/... (after LIFF setup)
   ```

3. **Wait for deployment:**
   - Railway builds automatically (2-3 min)
   - Logs show: `Uvicorn running on 0.0.0.0:...`

---

## ✅ Step 6: Verify Deployment (5 min)

Get your Railway URL from dashboard:
```
Your app: geoai-[random-name].up.railway.app
```

Test endpoints:
```bash
# Health
curl https://geoai-DOMAIN/health

# Dashboard
curl https://geoai-DOMAIN/dashboard/

# Try analyze
curl -X POST https://geoai-DOMAIN/analyze ^
  -H "Content-Type: application/json" ^
  -d "{\"lat\":12.615,\"lng\":102.105,\"user_id\":\"test\"}"
```

Expected:
- ✅ Health: `{"status":"ok","service":"GEOai"}`
- ✅ Dashboard: HTML page loads
- ✅ Analyze: Returns `{data: {...}}`

---

## 📱 Step 7: Setup LIFF (After Deployment)

1. Go to https://developers.line.biz → Your Channel → LIFF
2. Add LIFF App:
   - Name: `GEOai Analyzer`
   - Endpoint: `https://geoai-YOUR-DOMAIN/liff/`
   - Scopes: `profile`
   - Click Create
3. **Copy LIFF ID** → Add to `.env`:
   ```
   LIFF_URL=https://liff.line.me/YOUR_LIFF_ID
   ```
4. Push to GitHub:
   ```bash
   git add backend/.env
   git commit -m "Add LIFF URL"
   git push
   ```
5. Railway redeploys automatically

---

## 🧑‍🌾 Step 8: Test with LINE (5 min)

1. **Add bot as LINE friend:**
   - Go to https://line.me
   - Search bot by Channel ID from Developers Console
   - Add as friend

2. **Test LIFF:**
   - Type message to bot
   - Bot should respond
   - Click "วิเคราะห์แปลง" button
   - Should open LIFF in web view

3. **Test Analysis:**
   - Allow GPS permission
   - Select your location (12.615, 102.105)
   - Click "Analyze"
   - Should show NDVI, yield, recommendations

---

## 🎯 Success Criteria

✅ All 8 steps complete = **GEOai is LIVE**

| Component | Status | How to Test |
|-----------|--------|------------|
| Backend API | ✅ Live | `curl /health` → 200 |
| Dashboard | ✅ Live | Visit `/dashboard/` → Admin Key prompt |
| LIFF | ✅ Live | Open from LINE → allows GPS |
| Database | ✅ Connected | Analyze → Results saved to Supabase |
| ML Model | ✅ Working | Analyze → Returns yield forecast |
| GEE | ✅ Working | Dashboard → Shows satellite tiles |

---

## 🆘 Troubleshooting

**Backend won't start:**
```bash
# Check logs in Railway Dashboard → Service Logs
# Common issues:
# - GEE_KEY_JSON invalid JSON
# - SUPABASE_URL / key wrong
# - Missing environment variable
```

**LIFF won't open in LINE:**
```bash
# 1. Check LIFF endpoint is correct (includes domain)
# 2. Check LIFF app scope includes 'profile'
# 3. Check browser console (F12) for errors
```

**Analyze returns error:**
```bash
# Check Railway logs for Python errors
# Likely issues:
# - GEE Service Account not authorized
# - Supabase tables not created
# - GEE_KEY_JSON has parsing error
```

---

## 📚 Full Docs

- [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) — Detailed step-by-step
- [DEPLOYMENT.md](DEPLOYMENT.md) — Original deployment guide
- [BACKTEST_RESULTS.md](BACKTEST_RESULTS.md) — Test results

---

## 📞 Need Help?

Check logs:
1. Railway Dashboard → Service Logs
2. Browser Console (F12) for frontend errors
3. LINE Developers Console for webhook errors

Common errors & fixes in PRODUCTION_CHECKLIST.md → Troubleshooting

---

**Estimated Total Time:** 60 minutes  
**Status:** Ready to deploy  
**Next:** Collect credentials & run deploy.sh

Good luck! 🌾
