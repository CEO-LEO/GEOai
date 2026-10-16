#!/usr/bin/env bash
# backup_db.sh — สำรองฐานข้อมูล Supabase ด้วย pg_dump ก่อนรัน migration ทุกครั้ง
#
# ต้องมี:
#   - pg_dump เวอร์ชันเดียวกับ Postgres ของ Supabase หรือใหม่กว่า
#     (ดูเวอร์ชันได้ที่ Dashboard → Settings → Infrastructure)
#   - SUPABASE_DB_URL = connection string จาก Dashboard → Connect → "Session pooler"
#     เช่น postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
#     ห้าม commit ค่านี้ ใส่เป็น env ตอนรันเท่านั้น
#
# วิธีใช้:
#   SUPABASE_DB_URL='postgresql://...' bash scripts/backup_db.sh
#
# ผลลัพธ์: backups/<วันเวลา>_schema.sql และ backups/<วันเวลา>_data.dump
#   (backups/ อยู่ใน .gitignore — ไฟล์มีข้อมูลส่วนบุคคลของผู้ใช้ ห้ามขึ้น git)
#
# กู้คืน (เฉพาะกรณีจำเป็น และควรลองกับโปรเจกต์ทดสอบก่อน):
#   pg_restore --data-only --no-owner -d "$SUPABASE_DB_URL" backups/<วันเวลา>_data.dump

set -euo pipefail

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
  echo "ต้องตั้ง SUPABASE_DB_URL ก่อน (ดูคำอธิบายบนสุดของไฟล์)" >&2
  exit 1
fi
command -v pg_dump >/dev/null || { echo "ไม่พบ pg_dump" >&2; exit 1; }

cd "$(dirname "$0")/.."
mkdir -p backups
stamp="$(date +%Y%m%d_%H%M%S)"

pg_dump "$SUPABASE_DB_URL" --schema=public --schema-only --no-owner --no-privileges \
  -f "backups/${stamp}_schema.sql"
pg_dump "$SUPABASE_DB_URL" --schema=public --data-only --format=custom \
  -f "backups/${stamp}_data.dump"

echo "สำรองเสร็จ:"
ls -lh "backups/${stamp}_schema.sql" "backups/${stamp}_data.dump"
