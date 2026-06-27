# 🚀 GEOai Production Deployment Checklist

**Objective:** Deploy GEOai to production (Railway) with full credentials  
**Status:** Ready to execute  
**Time Estimate:** 30-45 minutes (credentials prep) + 10 minutes (deployment)

---

## Phase 1: Prepare Credentials (User Action Required)

### ✋ Step 1: GitHub Repository
**What:** Create GitHub repo to host code  
**Time:** 2 min

```bash
# User action:
1. Go to https://github.com/new
2. Create repo: "GEOai"
3. Copy HTTPS URL: https://github.com/YOUR_USERNAME/GEOai.git

# Then locally:
cd c:\GEOai
git remote add origin https://github.com/YOUR_USERNAME/GEOai.git
git branch -M main
git push -u origin main
```

**Status:** Waiting for user  
**Next Step:** After GitHub URL ready, run git push

---

### ✋ Step 2: Google Earth Engine (GEE)

**What:** Get satellite imagery API access  
**Time:** 5 min (if account exists) or 15 min (new signup)

1. **Create Google Cloud Project:**
   - Go to https://console.cloud.google.com
   - Create new project: "geoai-durian"
   - Enable APIs:
     - Earth Engine API
     - Compute Engine API

2. **Create Service Account:**
   - IAM & Admin → Service Accounts
   - Create Service Account: "geoai-ee"
   - Grant Roles: 
     - Editor (temporary, for testing)
   - Create Key → JSON → Download as `gee-key.json`

3. **Verify key:**
   ```bash
   cat gee-key.json | jq .
   # Copy the entire JSON output
   ```

4. **Authorize Service Account:**
   - Copy service account email: `geoai-ee@PROJECT-ID.iam.gserviceaccount.com`
   - Go to https://earthengine.google.com/signup/
   - Sign in with same Google account
   - Paste email address in "Service Account" field
   - Authorize

**Credentials Needed:**
- `GEE_SERVICE_ACCOUNT` = (from key.json, field: "client_email")
- `GEE_KEY_JSON` = (entire key.json as JSON string)

**Status:** Waiting for user  

---

### ✋ Step 3: Supabase (Database)

**What:** PostgreSQL database for storing analysis results  
**Time:** 3 min

1. **Create Supabase Project:**
   - Go to https://supabase.com
   - Sign up / Log in
   - Create new project: "geoai-db"
   - Region: Singapore (closest to Thailand)
   - Password: auto-generate

2. **Get Credentials:**
   - Settings → API
   - Copy:
     - `Project URL` → `SUPABASE_URL`
     - `service_role` secret → `SUPABASE_SERVICE_ROLE_KEY`

3. **Create Tables:**
   - SQL Editor → New Query
   - Paste from: `supabase/schema.sql`
   - Run

**Credentials Needed:**
- `SUPABASE_URL` = https://xxxxx.supabase.co
- `SUPABASE_SERVICE_ROLE_KEY` = eyJhbGc...

**Status:** Waiting for user  

---

### ✋ Step 4: LINE Developers (Bot Channel)

**What:** Register LINE Bot for farmer messaging  
**Time:** 5 min

1. **Create LINE Developer Account:**
   - Go to https://developers.line.biz
   - Sign up with LINE account
   - Create Provider: "GEOai"

2. **Create Messaging API Channel:**
   - Channels → Create new channel
   - Type: Messaging API
   - Channel name: "GEOai Durian"
   - Category: Agriculture
   - Click Create

3. **Get Credentials:**
   - Settings → Basic settings
   - Copy:
     - `Channel ID` (not needed for env, for reference)
     - `Channel Access Token` → `LINE_CHANNEL_ACCESS_TOKEN`
     - `Channel Secret` → `LINE_CHANNEL_SECRET`

4. **Get LIFF ID (later, after Railway deployment):**
   - Messaging API → LIFF → Add
   - Name: "GEOai Analyzer"
   - Endpoint: `https://geoai-YOUR-DOMAIN.up.railway.app/liff/`
   - Scopes: `profile`
   - After creation → copy LIFF ID → `LIFF_URL=https://liff.line.me/LIFF_ID`

**Credentials Needed (now):**
- `LINE_CHANNEL_ACCESS_TOKEN` = xxx...
- `LINE_CHANNEL_SECRET` = xxx...

**Status:** Waiting for user  

---

### ✋ Step 5: Railway Account

**What:** Deployment platform  
**Time:** 1 min

1. Go to https://railway.app
2. Sign up with GitHub
3. Create new project (will do in next phase)

**Status:** Waiting for user  

---

## Phase 2: Environment Setup (Automated)

Once all credentials from Phase 1 are collected, create `.env` file:

```bash
# Run this after collecting all credentials:
cat > backend/.env << 'EOF'
# ─── LINE ───────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN=YOUR_TOKEN_HERE
LINE_CHANNEL_SECRET=YOUR_SECRET_HERE

# ─── Google Earth Engine ─────────────────────────
GEE_SERVICE_ACCOUNT=your-service-account@your-project.iam.gserviceaccount.com
GEE_KEY_JSON={"type":"service_account","project_id":"...","...":"..."}

# ─── Supabase ────────────────────────────────────
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGc...

# ─── LIFF ────────────────────────────────────────
LIFF_URL=https://liff.line.me/YOUR_LIFF_ID

# ─── Admin ───────────────────────────────────────
ADMIN_API_KEY=<will-generate>
SPHERE_KEY=optional
EOF
```

---

## Phase 3: GitHub Push (Automated)

```bash
cd c:\GEOai

# Add untracked files (optional docs, exclude .env)
git add -A
git commit -m "🚀 Production-ready: Full credential setup + deployment guide"
git push origin main
```

---

## Phase 4: Railway Deployment (Automated)

1. **Connect GitHub to Railway:**
   - Go to Railway.app
   - New Project → GitHub Repo
   - Select: YOUR_USERNAME/GEOai
   - Branch: main

2. **Add Environment Variables:**
   - Railway Dashboard → Variables
   - Paste all from `.env` file above

3. **Deploy:**
   - Railway auto-builds from `railway.toml`
   - Generates URL: `https://geoai-xxxxx.up.railway.app`

4. **Verify Deployment:**
   ```bash
   # Test health
   curl https://geoai-DOMAIN/health
   
   # Test dashboard
   curl https://geoai-DOMAIN/dashboard/
   
   # Test LIFF
   curl https://geoai-DOMAIN/liff/config.js
   ```

---

## Phase 5: LINE LIFF Setup

After Railway URL is live:

1. **Update LIFF Endpoint:**
   - LINE Developers → LIFF
   - Edit LIFF app
   - Endpoint: `https://geoai-YOUR-DOMAIN/liff/`
   - Save

2. **Copy LIFF ID & Update `.env`:**
   ```bash
   LIFF_URL=https://liff.line.me/YOUR_LIFF_ID
   git commit -am "Add LIFF URL to env"
   git push
   ```

3. **Test in LINE:**
   - Add bot as friend: `@geoai-durian` (or search by Channel ID)
   - Click "วิเคราะห์แปลง" button
   - Should open LIFF in browser

---

## Phase 6: Post-Deployment Testing

### Health Checks
```bash
# 1. Backend health
curl https://geoai-DOMAIN/health

# 2. Admin dashboard
https://geoai-DOMAIN/dashboard/
# (Enter ADMIN_API_KEY in prompt)

# 3. Analyze endpoint
curl -X POST https://geoai-DOMAIN/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "lat": 12.615,
    "lng": 102.105,
    "user_id": "test_farmer",
    "display_name": "Test Farmer",
    "plot_name": "Backtest Plot"
  }'

# 4. LIFF from LINE
# Add bot → Click button → Should load LIFF
```

### Expected Responses
- ✅ Health: `{"status":"ok","service":"GEOai",...}`
- ✅ Dashboard: 200 OK + HTML with map
- ✅ Analyze: 200 OK + `{data: {ndvi_now, yield_estimate, ...}}`
- ✅ LIFF: 200 OK + loads in LINE

---

## Timeline

| Phase | Action | Duration | Owner |
|-------|--------|----------|-------|
| 1 | Collect credentials | 30 min | User |
| 2 | Create .env | 5 min | Me (automated script) |
| 3 | Git push | 2 min | Me (bash) |
| 4 | Railway deploy | 10 min | Railway (auto) |
| 5 | LIFF setup | 3 min | User |
| 6 | Testing | 5 min | Me (test script) |
| **Total** | | **55 minutes** | |

---

## Credentials Checklist

Print this and fill in as you collect:

```
GitHub
  [ ] Repository URL: https://github.com/___/GEOai

GEE
  [ ] GEE_SERVICE_ACCOUNT: ___@___.iam.gserviceaccount.com
  [ ] GEE_KEY_JSON: (entire JSON object)

Supabase
  [ ] SUPABASE_URL: https://_____.supabase.co
  [ ] SUPABASE_SERVICE_ROLE_KEY: eyJhbGc...

LINE
  [ ] LINE_CHANNEL_ACCESS_TOKEN: xxx...
  [ ] LINE_CHANNEL_SECRET: xxx...
  [ ] (LIFF_URL: after Railway deploy)

Railway
  [ ] Account ready: https://railway.app

Admin
  [ ] ADMIN_API_KEY: (will generate)
```

---

## Troubleshooting

**Backend won't start on Railway:**
- Check logs: Railway Dashboard → Service Logs
- Verify `GEE_KEY_JSON` is valid JSON (not string)
- Verify `SUPABASE_URL` and key are correct

**LIFF won't open:**
- Check LIFF endpoint in LINE Developers matches Railway URL
- Check LIFF app scope includes `profile`
- Check browser console for errors (F12)

**Analyze returns error 500:**
- Check GEE_KEY_JSON is authorized for Earth Engine
- Check Supabase tables created (run schema.sql)
- Check LINE credentials in webhook verification

---

## What's Next (After Deployment)

1. **Real Farmer Testing:** Invite 3-5 farmers from Nai Yai Yam to test LIFF
2. **GPS Collection:** Use LIFF to collect real plot coordinates
3. **Monthly Analysis:** Run backtest monthly with new satellite data
4. **Harvest Validation:** Compare yield forecasts vs actual harvest
5. **Model Retraining:** Retrain ML model with actual data (quarterly)

---

**Status:** 🟡 Awaiting Phase 1 credentials  
**Next Action:** Collect credentials from user → Execute Phase 2-6 automatically

