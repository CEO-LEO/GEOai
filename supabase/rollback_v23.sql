-- rollback_v23.sql — ย้อน migrate_v23.sql (ลบเฉพาะแถวที่ระบบตั้งให้ source='default')
BEGIN;
ALTER TABLE users DROP COLUMN IF EXISTS consent_volunteer_test;
DELETE FROM plot_crops WHERE source = 'default';
ALTER TABLE plot_crops DROP CONSTRAINT IF EXISTS plot_crops_source_check;
ALTER TABLE plot_crops ADD CONSTRAINT plot_crops_source_check CHECK (source IN ('user', 'officer', 'model'));
COMMIT;
