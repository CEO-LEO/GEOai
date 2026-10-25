-- rollback_v17.sql — ย้อน migrate_v17.sql
-- ⚠️ ความยินยอมและคำขอลบข้อมูลที่บันทึกหลัง v17 จะหาย — export consent_logs ก่อนรัน (หลักฐานความยินยอมตาม PDPA)
-- แปลงที่ถูกซ่อนเพราะคำขอลบจะยังซ่อนอยู่ (is_active = FALSE) — ตรวจก่อนว่าต้องคืนหรือไม่
BEGIN;
DROP TABLE IF EXISTS consent_logs;
ALTER TABLE plots DROP COLUMN IF EXISTS deactivated_reason;
ALTER TABLE users DROP COLUMN IF EXISTS deletion_requested_at;
ALTER TABLE users DROP COLUMN IF EXISTS consent_photos_research;
ALTER TABLE users DROP COLUMN IF EXISTS consent_at;
ALTER TABLE users DROP COLUMN IF EXISTS consent_version;
COMMIT;
