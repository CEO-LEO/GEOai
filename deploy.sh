#!/bin/bash
# GEOai Production Deployment Script
# Usage: bash deploy.sh

set -e

echo "========================================"
echo "GEOai Production Deployment"
echo "========================================"
echo ""

# Check prerequisites
echo "1️⃣  Checking prerequisites..."
if ! command -v git &> /dev/null; then
  echo "❌ Git not found. Install Git first."
  exit 1
fi

if ! command -v python3 &> /dev/null; then
  echo "❌ Python3 not found. Install Python3 first."
  exit 1
fi

echo "✅ Git and Python3 found"
echo ""

# Check .env
echo "2️⃣  Checking environment setup..."
if [ ! -f "backend/.env" ]; then
  echo "⚠️  .env file not found at backend/.env"
  echo ""
  echo "Please create backend/.env with credentials:"
  echo "  - LINE_CHANNEL_ACCESS_TOKEN"
  echo "  - LINE_CHANNEL_SECRET"
  echo "  - GEE_SERVICE_ACCOUNT"
  echo "  - GEE_KEY_JSON"
  echo "  - SUPABASE_URL"
  echo "  - SUPABASE_SERVICE_ROLE_KEY"
  echo ""
  echo "See backend/.env.example or PRODUCTION_CHECKLIST.md"
  echo ""
  read -p "Continue without .env? (y/N): " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Deployment cancelled. Create .env first."
    exit 1
  fi
else
  echo "✅ .env file exists"
fi
echo ""

# Test backend
echo "3️⃣  Testing backend locally..."
cd backend
timeout 5 python main.py &
PID=$!
sleep 2

if curl -s http://127.0.0.1:8000/health > /dev/null; then
  echo "✅ Backend health check passed"
  kill $PID 2>/dev/null || true
else
  echo "❌ Backend health check failed"
  kill $PID 2>/dev/null || true
  exit 1
fi
cd ..
echo ""

# Git setup
echo "4️⃣  Git setup..."
if ! git remote | grep -q "origin"; then
  echo "⚠️  No 'origin' remote found"
  read -p "Enter GitHub repo URL (e.g., https://github.com/user/GEOai.git): " GIT_URL
  git remote add origin "$GIT_URL"
fi

REMOTE_URL=$(git remote get-url origin)
echo "✅ Remote: $REMOTE_URL"
echo ""

# Git push
echo "5️⃣  Pushing to GitHub..."
git add -A
git commit -m "🚀 Deploy production: Full credentials setup + backtest validation" || true
git branch -M main 2>/dev/null || true
git push -u origin main || {
  echo "❌ Git push failed. Check credentials or internet."
  exit 1
}
echo "✅ Code pushed to GitHub"
echo ""

# Railway setup
echo "6️⃣  Railway deployment instructions..."
echo ""
echo "To complete deployment:"
echo ""
echo "1. Go to https://railway.app"
echo "2. New Project → GitHub Repo"
echo "3. Select: $(basename "$REMOTE_URL" .git)"
echo "4. Railway Dashboard → Variables"
echo "5. Add these from backend/.env:"
echo "   - LINE_CHANNEL_ACCESS_TOKEN"
echo "   - LINE_CHANNEL_SECRET"
echo "   - GEE_SERVICE_ACCOUNT"
echo "   - GEE_KEY_JSON"
echo "   - SUPABASE_URL"
echo "   - SUPABASE_SERVICE_ROLE_KEY"
echo "   - LIFF_URL (after LIFF registration)"
echo "   - ADMIN_API_KEY"
echo ""
echo "6. Deploy (auto-triggered by git push)"
echo "7. Wait 2-3 minutes for build + startup"
echo ""
echo "Your deployment URL: https://geoai-[random].up.railway.app"
echo ""

# Summary
echo "========================================"
echo "✅ Deployment prepared!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Go to Railway.app → connect GitHub"
echo "2. Add environment variables"
echo "3. Deployment will start automatically"
echo ""
echo "Test after deployment:"
echo "  curl https://geoai-DOMAIN/health"
echo ""
echo "Docs: See PRODUCTION_CHECKLIST.md for full guide"
echo ""
