-- rollback_v25.sql — ย้อน migrate_v25.sql (สถิติ EECU ที่เก็บไว้หาย — ส่งออกก่อนถ้าต้องการ)
BEGIN;
DROP TABLE IF EXISTS ee_usage_log;
COMMIT;
