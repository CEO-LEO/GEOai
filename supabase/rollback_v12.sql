-- rollback_v12.sql — ย้อน migrate_v12.sql กลับเป็นสถานะก่อนรัน (ตามที่ตรวจบน production 2026-10-24)
-- ใช้เมื่อ migrate_v12 ทำให้มีปัญหาเท่านั้น
-- หมายเหตุ: ก่อนรัน v12 บน production ตาราง scheduler_runs "เปิด RLS อยู่แล้ว" (แต่ไม่มี policy)
-- จึงย้อนแค่ลบ policy — ไม่ปิด RLS (ปิดจะทำให้ปลอดภัยน้อยกว่าสถานะเดิม)

BEGIN;

DROP POLICY IF EXISTS "backend only" ON scheduler_runs;

ALTER TABLE users
    ALTER COLUMN notify_daily_digest SET DEFAULT TRUE;   -- ค่าเดิมจาก migrate_v11

-- สิทธิ์ค่าเริ่มต้นของ Supabase ก่อนถอน (ALL ให้ anon / authenticated)
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated;

COMMIT;
