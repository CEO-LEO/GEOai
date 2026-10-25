-- rollback_v16.sql — ย้อน migrate_v16.sql
-- ⚠️ บทบาท/พื้นที่เจ้าหน้าที่, รหัสเชิญ, audit log และสถิติการใช้งานที่เกิดหลัง v16 จะหาย — export ก่อนรัน
BEGIN;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS invite_redemptions;
DROP TABLE IF EXISTS invites;
DROP TABLE IF EXISTS user_areas;
DROP INDEX IF EXISTS idx_plots_area_code;
ALTER TABLE plots DROP COLUMN IF EXISTS owner_label;
ALTER TABLE plots DROP COLUMN IF EXISTS created_by;
ALTER TABLE plots DROP COLUMN IF EXISTS area_name;
ALTER TABLE plots DROP COLUMN IF EXISTS area_code;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users DROP COLUMN IF EXISTS share_with_officers;
ALTER TABLE users DROP COLUMN IF EXISTS role;
COMMIT;
