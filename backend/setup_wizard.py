#!/usr/bin/env python3
"""
GEOai v2 — Interactive Setup Wizard
สร้าง .env + ทดสอบ credentials + สร้างตาราง Supabase

วิธีใช้:
  cd backend
  python setup_wizard.py
"""

import os
import sys
import json
import textwrap

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# ──────────────────────────────────────────────────────────
STEPS = [
    {
        "key": "GEE_SERVICE_ACCOUNT",
        "title": "Google Earth Engine — Service Account Email",
        "help": textwrap.dedent("""\
            วิธีสร้าง:
              1. ไปที่ https://console.cloud.google.com/
              2. สร้าง Project ใหม่ (หรือใช้ที่มี)
              3. เปิด API: Earth Engine API
                 → APIs & Services → Library → ค้นหา "Earth Engine" → Enable
              4. สร้าง Service Account:
                 → IAM & Admin → Service Accounts → Create
                 → ชื่ออะไรก็ได้ เช่น geoai-bot
                 → Role: ไม่ต้องเลือก → Done
              5. สมัคร Earth Engine ให้ service account:
                 → https://signup.earthengine.google.com/
                 → ใช้ email ของ service account ที่สร้าง
              6. Copy email เช่น geoai-bot@my-project.iam.gserviceaccount.com
        """),
        "validate": lambda v: "@" in v and ".iam.gserviceaccount.com" in v,
        "error": "ต้องเป็น email ลงท้ายด้วย .iam.gserviceaccount.com",
    },
    {
        "key": "GEE_KEY_JSON",
        "title": "Google Earth Engine — Service Account Key (JSON)",
        "help": textwrap.dedent("""\
            วิธีได้มา:
              1. Google Cloud Console → IAM → Service Accounts
              2. คลิกที่ service account ที่สร้างไว้
              3. Keys → Add Key → Create new key → JSON → Create
              4. ไฟล์ key.json จะถูกดาวน์โหลดอัตโนมัติ
              5. รันคำสั่งนี้เพื่อแปลงเป็น string:
                 python -c "import json; print(json.dumps(json.load(open('key.json'))))"
              6. Copy ผลลัพธ์ทั้งบรรทัดมาวาง

            หรือพิมพ์ path ไปยังไฟล์ key.json แทนก็ได้ เช่น:
              C:\\Users\\me\\Downloads\\key.json
        """),
        "validate": None,  # custom
        "error": "ไม่ใช่ JSON ที่ถูกต้อง และไม่ใช่ path ไปยังไฟล์ที่มีอยู่",
    },
    {
        "key": "LINE_CHANNEL_SECRET",
        "title": "LINE Bot — Channel Secret",
        "help": textwrap.dedent("""\
            วิธีสร้าง LINE Bot:
              1. ไปที่ https://developers.line.biz/console/
              2. สร้าง Provider → สร้าง Channel → เลือก Messaging API
              3. ตั้งชื่อ bot เช่น "GEOai"
              4. Basic settings → Channel secret → Copy
        """),
        "validate": lambda v: len(v) == 32 and v.isalnum(),
        "error": "Channel Secret ต้องเป็นตัวอักษร/ตัวเลข 32 ตัว",
    },
    {
        "key": "LINE_CHANNEL_ACCESS_TOKEN",
        "title": "LINE Bot — Channel Access Token",
        "help": textwrap.dedent("""\
            ในหน้า LINE Developers Console:
              1. ไปที่ Messaging API tab
              2. Channel access token (long-lived) → Issue
              3. Copy token ทั้งหมด
        """),
        "validate": lambda v: len(v) > 100,
        "error": "Access Token ต้องยาวกว่า 100 ตัวอักษร",
    },
    {
        "key": "SUPABASE_URL",
        "title": "Supabase — Project URL",
        "help": textwrap.dedent("""\
            วิธีสร้าง Supabase:
              1. ไปที่ https://supabase.com/ → Start your project (ฟรี)
              2. สร้าง Organization → สร้าง Project
              3. จด Database Password ไว้
              4. Settings → API → Project URL → Copy
              (รูปแบบ: https://xxxxxxxxxxxx.supabase.co)
        """),
        "validate": lambda v: v.startswith("https://") and ".supabase.co" in v,
        "error": "ต้องเป็น URL รูปแบบ https://xxxx.supabase.co",
    },
    {
        "key": "SUPABASE_SERVICE_ROLE_KEY",
        "title": "Supabase — Service Role Key (secret)",
        "help": textwrap.dedent("""\
            ในหน้า Supabase Dashboard:
              1. Settings → API
              2. Project API keys → service_role (secret) → Copy
              ⚠️ อย่าเอา anon key มา — ต้องใช้ service_role key
        """),
        "validate": lambda v: v.startswith("eyJ") and len(v) > 100,
        "error": "Service Role Key ต้องขึ้นต้นด้วย eyJ... และยาวกว่า 100 ตัวอักษร",
    },
    {
        "key": "LIFF_URL",
        "title": "LINE LIFF — LIFF URL",
        "help": textwrap.dedent("""\
            สร้าง LIFF App:
              1. LINE Developers → Provider → Channel
              2. แท็บ LIFF → Add
              3. Size: Full, Endpoint URL: https://YOUR_DOMAIN/liff/
              4. Scope: เลือก profile
              5. Copy LIFF URL (รูปแบบ: https://liff.line.me/xxxx-xxxx)

            💡 ถ้ายังไม่มี domain ให้กด Enter ข้ามไปก่อน
        """),
        "validate": lambda v: v == "" or "liff.line.me" in v,
        "error": "ต้องเป็น URL รูปแบบ https://liff.line.me/xxxx หรือปล่อยว่าง",
        "optional": True,
    },
    {
        "key": "SPHERE_KEY",
        "title": "GISTDA Sphere — Map API Key (optional)",
        "help": textwrap.dedent("""\
            Sphere Map ใช้แสดงแผนที่ไทยใน LIFF:
              1. สมัครที่ https://sphere.gistda.or.th/
              2. สร้าง API Key → Copy

            💡 ถ้ายังไม่มีกด Enter ข้ามไปก่อน (แผนที่จะไม่แสดง)
        """),
        "validate": lambda v: True,
        "error": "",
        "optional": True,
    },
]

OPTIONAL_VARS = {
    "ADMIN_API_KEY": None,  # auto-generate
    "API_BASE_URL": "",
}


# ──────────────────────────────────────────────────────────
def banner():
    print("\n" + "=" * 60)
    print("  🌿  GEOai v2 — Setup Wizard")
    print("  ระบบจะพาตั้งค่าทีละขั้นตอน")
    print("=" * 60)


def load_existing_env():
    """โหลดค่าเดิมจาก .env (ถ้ามี)"""
    existing = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
    return existing


def ask(step, existing_val=""):
    """ถามค่าจาก user ทีละตัว"""
    key = step["key"]
    optional = step.get("optional", False)
    skip_hint = " (Enter = ข้าม)" if optional else ""
    keep_hint = ""
    if existing_val:
        masked = existing_val[:8] + "..." if len(existing_val) > 12 else existing_val
        keep_hint = f" [Enter = ใช้ค่าเดิม: {masked}]"

    print(f"\n{'─' * 60}")
    print(f"  📌  {step['title']}{skip_hint}")
    print(f"{'─' * 60}")
    print(step["help"])

    while True:
        val = input(f"  → {key}{keep_hint}: ").strip()

        # Keep existing
        if val == "" and existing_val:
            return existing_val
        # Skip optional
        if val == "" and optional:
            return ""
        # Must have value for required
        if val == "" and not optional:
            print("  ⚠️  ค่านี้จำเป็น กรุณาใส่ค่า")
            continue

        # Special: GEE_KEY_JSON — accept file path
        if key == "GEE_KEY_JSON":
            val = _resolve_gee_key(val)
            if val is None:
                print(f"  ❌  {step['error']}")
                continue
            return val

        # Validate
        if step.get("validate") and not step["validate"](val):
            print(f"  ❌  {step['error']}")
            continue

        return val


def _resolve_gee_key(val):
    """GEE key: accept JSON string or file path"""
    # Try as file path first
    path_candidates = [val, os.path.expanduser(val)]
    for p in path_candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "client_email" in data and "private_key" in data:
                    print(f"  ✅  อ่านจากไฟล์: {p}")
                    return json.dumps(data)
                else:
                    print("  ❌  ไฟล์ JSON ไม่มี client_email หรือ private_key")
                    return None
            except json.JSONDecodeError:
                print(f"  ❌  ไฟล์ {p} ไม่ใช่ JSON ที่ถูกต้อง")
                return None

    # Try as JSON string
    try:
        data = json.loads(val)
        if "client_email" in data and "private_key" in data:
            return val
        else:
            print("  ❌  JSON ไม่มี client_email หรือ private_key")
            return None
    except json.JSONDecodeError:
        return None


def write_env(values):
    """เขียน .env"""
    lines = [
        "# ─── GEOai v2 .env ─── auto-generated by setup_wizard.py\n",
        f"GEE_SERVICE_ACCOUNT={values.get('GEE_SERVICE_ACCOUNT', '')}\n",
        f"GEE_KEY_JSON={values.get('GEE_KEY_JSON', '')}\n",
        f"LINE_CHANNEL_ACCESS_TOKEN={values.get('LINE_CHANNEL_ACCESS_TOKEN', '')}\n",
        f"LINE_CHANNEL_SECRET={values.get('LINE_CHANNEL_SECRET', '')}\n",
        f"SUPABASE_URL={values.get('SUPABASE_URL', '')}\n",
        f"SUPABASE_SERVICE_ROLE_KEY={values.get('SUPABASE_SERVICE_ROLE_KEY', '')}\n",
        f"LIFF_URL={values.get('LIFF_URL', '')}\n",
        f"API_BASE_URL={values.get('API_BASE_URL', '')}\n",
        f"ADMIN_API_KEY={values.get('ADMIN_API_KEY', '')}\n",
        f"SPHERE_KEY={values.get('SPHERE_KEY', '')}\n",
    ]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\n  💾  บันทึก .env เรียบร้อย → {ENV_PATH}")


# ──────────────────────────────────────────────────────────
def test_gee(values):
    """ทดสอบ GEE connection"""
    print("\n🛰️  ทดสอบ Google Earth Engine...")
    try:
        import tempfile
        import ee
        sa = values["GEE_SERVICE_ACCOUNT"]
        key_data = json.loads(values["GEE_KEY_JSON"])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(key_data, tmp)
            tmp_path = tmp.name
        ee.Initialize(ee.ServiceAccountCredentials(sa, tmp_path))
        os.unlink(tmp_path)
        # Quick test: get elevation at a point in Chanthaburi
        pt = ee.Geometry.Point([102.1, 12.6])
        elev = ee.Image("USGS/SRTMGL1_003").sample(pt, 30).first().get("elevation").getInfo()
        print(f"  ✅  GEE OK — elevation test = {elev} m (จันทบุรี)")
        return True
    except Exception as e:
        print(f"  ❌  GEE ผิดพลาด: {e}")
        return False


def test_supabase(values):
    """ทดสอบ Supabase connection"""
    print("\n🗄️  ทดสอบ Supabase...")
    url = values.get("SUPABASE_URL", "")
    key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("  ⏭️  ข้าม — ไม่มี credentials")
        return False
    try:
        import httpx
        resp = httpx.get(
            f"{url}/rest/v1/",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"  ✅  Supabase เชื่อมต่อสำเร็จ")
            return True
        else:
            print(f"  ❌  Supabase ตอบ HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ❌  Supabase error: {e}")
        return False


def setup_supabase_tables(values):
    """สร้างตาราง Supabase จาก schema.sql"""
    url = values.get("SUPABASE_URL", "")
    key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return False

    schema_path = os.path.join(os.path.dirname(__file__), "..", "supabase", "schema.sql")
    migrate_path = os.path.join(os.path.dirname(__file__), "..", "supabase", "migrate_v3.sql")

    if not os.path.exists(schema_path):
        print("  ❌  ไม่พบ supabase/schema.sql")
        return False

    ans = input("\n  สร้างตาราง Supabase อัตโนมัติ? (y/n): ").strip().lower()
    if ans != "y":
        print("  ⏭️  ข้าม — คุณต้องรัน SQL เองใน Supabase Dashboard")
        return False

    import httpx

    # Run schema.sql
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    print("  📋  รัน schema.sql...")
    ok1 = _run_supabase_sql(url, key, schema_sql)

    # Run migration if exists
    ok2 = True
    if os.path.exists(migrate_path):
        with open(migrate_path, "r", encoding="utf-8") as f:
            migrate_sql = f.read()
        print("  📋  รัน migrate_v3.sql...")
        ok2 = _run_supabase_sql(url, key, migrate_sql)

    return ok1 and ok2


def _run_supabase_sql(url, key, sql):
    """Execute SQL via Supabase REST (pg_query)"""
    import httpx
    try:
        resp = httpx.post(
            f"{url}/rest/v1/rpc/",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            content=sql.encode("utf-8"),
            timeout=30,
        )
        # Supabase RPC may not work for DDL; fallback message
        if resp.status_code in (200, 201, 204):
            print(f"  ✅  SQL executed OK")
            return True
        else:
            print(f"  ⚠️  HTTP {resp.status_code} — อาจต้องรัน SQL เองใน Dashboard")
            print(f"       Supabase Dashboard → SQL Editor → New query → วาง SQL → Run")
            return False
    except Exception as e:
        print(f"  ❌  Error: {e}")
        return False


def test_line(values):
    """ทดสอบ LINE Bot"""
    print("\n💬  ทดสอบ LINE Bot...")
    token = values.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("  ⏭️  ข้าม — ไม่มี token")
        return False
    try:
        import httpx
        resp = httpx.get(
            "https://api.line.me/v2/bot/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            info = resp.json()
            name = info.get("displayName", "?")
            print(f"  ✅  LINE Bot: {name}")
            return True
        else:
            print(f"  ❌  LINE API ตอบ HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ❌  LINE error: {e}")
        return False


# ──────────────────────────────────────────────────────────
def main():
    banner()
    existing = load_existing_env()

    if existing:
        print(f"\n  📂  พบ .env เดิม ({len(existing)} ค่า)")
        print("  → ค่าเดิมจะเป็น default — กด Enter เพื่อใช้ค่าเดิม\n")

    values = {}

    # Collect credentials
    for step in STEPS:
        values[step["key"]] = ask(step, existing.get(step["key"], ""))

    # Auto-generate admin key
    import secrets
    if existing.get("ADMIN_API_KEY"):
        values["ADMIN_API_KEY"] = existing["ADMIN_API_KEY"]
    else:
        values["ADMIN_API_KEY"] = secrets.token_urlsafe(32)
        print(f"\n  🔑  สร้าง ADMIN_API_KEY อัตโนมัติ: {values['ADMIN_API_KEY'][:12]}...")

    values["API_BASE_URL"] = existing.get("API_BASE_URL", "")

    # Write .env
    write_env(values)

    # Test connections
    print("\n" + "=" * 60)
    print("  🧪  ทดสอบการเชื่อมต่อ")
    print("=" * 60)

    results = {}
    results["gee"] = test_gee(values)
    results["supabase"] = test_supabase(values)
    results["line"] = test_line(values)

    # Setup Supabase tables
    if results["supabase"]:
        setup_supabase_tables(values)

    # Summary
    print("\n" + "=" * 60)
    print("  📊  สรุปผล")
    print("=" * 60)

    all_ok = True
    checks = [
        ("Google Earth Engine", results["gee"]),
        ("Supabase Database", results["supabase"]),
        ("LINE Bot", results["line"]),
    ]
    for name, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {name}")
        if not ok:
            all_ok = False

    if all_ok:
        print(f"\n  🎉  ทุกอย่างพร้อม! เริ่มใช้งาน:\n")
        print(f"    cd backend")
        print(f"    uvicorn main:app --reload --port 8000\n")
        print(f"  📍 Health: http://localhost:8000/health")
        print(f"  📍 Dashboard: http://localhost:8000/dashboard/")
        print(f"  📍 LIFF: http://localhost:8000/liff/\n")
        print(f"  🔑 Admin Key: {values['ADMIN_API_KEY'][:16]}...")
        print(f"     ใส่ใน Dashboard หรือ header X-Admin-Key\n")
    else:
        print(f"\n  ⚠️  บาง service ยังเชื่อมต่อไม่ได้")
        print(f"  แก้ไขค่าใน .env แล้วรัน: python setup_wizard.py อีกครั้ง\n")
        print(f"  💡 หรือรัน server ในโหมด dev (GEE จะถูกข้าม):\n")
        print(f"    uvicorn main:app --reload --port 8000\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
