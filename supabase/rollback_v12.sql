-- rollback_v12.sql — ย้อน migrate_v12.sql กลับเป็นสถานะหลัง migrate_v11
-- ใช้เมื่อ migrate_v12 ทำให้มีปัญหาเท่านั้น

BEGIN;

DROP POLICY IF EXISTS "backend only" ON scheduler_runs;
ALTER TABLE scheduler_runs DISABLE ROW LEVEL SECURITY;

ALTER TABLE users
    ALTER COLUMN notify_daily_digest SET DEFAULT TRUE;   -- ค่าเดิมจาก migrate_v11

COMMIT;
