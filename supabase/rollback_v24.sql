-- rollback_v24.sql — ย้อน migrate_v24.sql (บันทึกการสุ่มหาย คำตอบเดิมอยู่ แต่ไม่มีป้าย random/user)
BEGIN;
ALTER TABLE field_validations DROP COLUMN IF EXISTS sample_id;
ALTER TABLE field_validations DROP CONSTRAINT IF EXISTS field_validations_selection_check;
ALTER TABLE field_validations DROP COLUMN IF EXISTS selection;
DROP TABLE IF EXISTS validation_samples;
COMMIT;
