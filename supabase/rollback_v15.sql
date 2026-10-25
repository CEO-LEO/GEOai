-- rollback_v15.sql — ย้อน migrate_v15.sql
-- ⚠️ การแก้ไข/อนุมัติคำแนะนำที่ทำหลัง v15 จะหาย — export จากหน้า admin (CSV) ก่อนรัน
BEGIN;
DROP TABLE IF EXISTS recommendation_rule_history;
DROP TABLE IF EXISTS recommendation_rules;
COMMIT;
