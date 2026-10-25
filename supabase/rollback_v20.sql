-- rollback_v20.sql — ย้อน migrate_v20.sql
-- ⚠️ การตั้งความถี่เตือนฝนของผู้ใช้และการแก้ไข/อนุมัติคำแนะนำ heavy_rain_coming หลัง v20 จะหาย
BEGIN;
DELETE FROM recommendation_rules WHERE problem = 'heavy_rain_coming';
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_rain_alert_freq_check;
ALTER TABLE users DROP COLUMN IF EXISTS rain_alert_freq;
COMMIT;
