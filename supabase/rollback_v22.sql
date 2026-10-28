-- rollback_v22.sql — ย้อน migrate_v22.sql
-- ⚠️ หมุดต้นไม้และบันทึกต่อต้นหลัง v22 จะหาย (export tree_pins, tree_logs ก่อนรัน) · รูปยังอยู่ แค่หลุดจากต้น
BEGIN;
ALTER TABLE photos DROP COLUMN IF EXISTS tree_id;
DROP TABLE IF EXISTS tree_logs;
DROP TABLE IF EXISTS tree_pins;
COMMIT;
