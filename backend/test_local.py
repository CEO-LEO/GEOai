"""
Local test script — ทดสอบ GEE analysis โดยไม่ต้องรัน full server
วิธีใช้:
  1. ตั้งค่า .env (ใช้ setup_wizard.py หรือสร้างเอง)
  2. รัน: python test_local.py

พิกัดตัวอย่าง: สวนทุเรียนจันทบุรี
"""

import os
import json
import tempfile
from dotenv import load_dotenv
load_dotenv()

import ee
from gee_analysis import analyze_durian_plot
from rule_engine import format_message

# Initialize GEE
service_account = os.environ["GEE_SERVICE_ACCOUNT"]
key_json = os.environ["GEE_KEY_JSON"]
key_data = json.loads(key_json)
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
    json.dump(key_data, tmp)
    tmp_path = tmp.name
ee.Initialize(ee.ServiceAccountCredentials(service_account, tmp_path))
os.unlink(tmp_path)

# พิกัดทดสอบ — สวนทุเรียน จันทบุรี
TEST_LAT = 12.6011
TEST_LNG = 102.1042

print(f"กำลังดึงข้อมูลดาวเทียมสำหรับ ({TEST_LAT}, {TEST_LNG})...")
data = analyze_durian_plot(TEST_LAT, TEST_LNG)

print("\n=== Raw Data ===")
for k, v in data.items():
    if isinstance(v, dict):
        print(f"  {k}:")
        for k2, v2 in v.items():
            print(f"    {k2}: {v2}")
    else:
        print(f"  {k}: {v}")

print("\n=== ข้อความที่จะส่งให้เกษตรกร ===")
print(format_message(data, TEST_LAT, TEST_LNG))

# v2 summary
disp = data.get("displacement", {})
fert = data.get("fertilizer", {})
yld = data.get("yield_estimate", {})
impact = data.get("land_impact", {})

print("\n=== v2 Summary ===")
print(f"  🌍 Land displacement: {disp.get('change_level', '-')} (stability: {disp.get('surface_stability', '-')})")
print(f"  🧪 Fertilizer: N={fert.get('n', '-')} P={fert.get('p', '-')} K={fert.get('k', '-')} ({fert.get('level', '-')})")
print(f"  📊 Yield: {yld.get('estimated_kg_per_rai', '-')} kg/rai ({yld.get('quality', '-')})")
print(f"  ⚠️  Impact: {impact.get('severity', '-')} (score: {impact.get('risk_score', '-')})")
