-- migrate_v12.sql — Phase 1a (2026-10)
-- ⚠️ ห้ามรันบน production จนกว่า: (1) ทำ backup ด้วย scripts/backup_db.sh แล้ว
--    (2) แจ้งเจ้าของโปรเจกต์และได้รับอนุมัติ — rollback: supabase/rollback_v12.sql
--
-- ทำ 2 อย่าง ไม่ลบหรือเปลี่ยนชนิดคอลัมน์ใดๆ:
--
-- 1) เปิด RLS + policy "backend only" ให้ scheduler_runs
--    migrate_v10 สร้างตารางนี้โดยไม่ได้เปิด RLS — ตารางใน public ที่ไม่เปิด RLS
--    อ่าน/เขียนได้ด้วย anon key ผ่าน REST API ของ Supabase (backend ใช้ service_role
--    ซึ่งข้าม RLS อยู่แล้ว จึงไม่กระทบการทำงานของแอป)
--
-- 2) เปลี่ยนค่าเริ่มต้นของ users.notify_daily_digest เป็น FALSE
--    ผู้ใช้ใหม่ต้องเลือกเปิดสรุปประจำวันเอง (ตัดสินใจ 2026-10-15) — ไม่แตะค่าของ
--    ผู้ใช้เดิม โค้ด (database.upsert_user) ใส่ FALSE ให้ผู้ใช้ใหม่อยู่แล้วตั้งแต่ Phase 1a
--    ข้อนี้ทำให้ค่าเริ่มต้นใน DB ตรงกับโค้ด เผื่อมีทางอื่นสร้างแถว users ในอนาคต

BEGIN;

ALTER TABLE scheduler_runs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'scheduler_runs') THEN
        CREATE POLICY "backend only" ON scheduler_runs FOR ALL USING (FALSE);
    END IF;
END $$;

ALTER TABLE users
    ALTER COLUMN notify_daily_digest SET DEFAULT FALSE;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'scheduler_runs';

SELECT column_name, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'notify_daily_digest';
