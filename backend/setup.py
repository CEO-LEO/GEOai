#!/usr/bin/env python3
"""
setup.py — ตรวจสอบ environment และเตรียมระบบก่อน deploy
วิธีใช้: python setup.py
"""

import os
import sys

REQUIRED_VARS = [
    ("LINE_CHANNEL_ACCESS_TOKEN", "LINE Developers → Messaging API → Channel access token"),
    ("LINE_CHANNEL_SECRET",       "LINE Developers → Messaging API → Channel secret"),
    ("GEE_SERVICE_ACCOUNT",       "Google Cloud → IAM → Service Account email"),
    ("GEE_KEY_JSON",              "JSON string ของ Service Account key (ดูวิธีใน .env.example)"),
    ("SUPABASE_URL",              "Supabase project → Settings → API → Project URL"),
    ("SUPABASE_SERVICE_ROLE_KEY", "Supabase project → Settings → API → service_role key"),
    ("LIFF_URL",                  "LINE Developers → LIFF → LIFF URL (https://liff.line.me/xxxx)"),
]

def check():
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 55)
    print("  GEOai — Environment Check")
    print("=" * 55)

    missing = []
    for var, hint in REQUIRED_VARS:
        val = os.environ.get(var, "")
        if val:
            masked = val[:6] + "..." + val[-4:] if len(val) > 12 else "****"
            print(f"  ✅  {var:<35} {masked}")
        else:
            print(f"  ❌  {var:<35} ← {hint}")
            missing.append(var)

    print("=" * 55)

    if missing:
        print(f"\n⚠️  ขาด {len(missing)} ค่า กรุณาเพิ่มใน .env แล้วรันใหม่\n")
        sys.exit(1)
    else:
        print("\n✅  ทุก env var พร้อม — รัน server ได้เลย\n")
        print("  uvicorn main:app --host 0.0.0.0 --port 8000 --reload\n")


def check_gee():
    """ทดสอบ GEE connection"""
    try:
        import json, tempfile, ee
        from dotenv import load_dotenv
        load_dotenv()
        sa       = os.environ["GEE_SERVICE_ACCOUNT"]
        key_json = os.environ["GEE_KEY_JSON"]
        key_data = json.loads(key_json)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(key_data, tmp)
            tmp_path = tmp.name
        ee.Initialize(ee.ServiceAccountCredentials(sa, tmp_path))
        os.unlink(tmp_path)
        img = ee.Image("USGS/SRTMGL1_003")
        val = img.sample(ee.Geometry.Point([102.1, 12.6]), 30).first().get("elevation").getInfo()
        print(f"  ✅  GEE เชื่อมต่อสำเร็จ — elevation test: {val} ม.")
    except Exception as e:
        print(f"  ❌  GEE error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    check()
    print("กำลังทดสอบ Google Earth Engine...")
    check_gee()
    print("\n🌿 พร้อม deploy!\n")
