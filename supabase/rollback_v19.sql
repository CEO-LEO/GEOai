-- rollback_v19.sql — ย้อน migrate_v19.sql
-- ⚠️ คำตอบยืนยันภาคสนามที่เก็บหลัง v19 จะหาย (เป็น label สำคัญของ Phase 4) — export ก่อนรันเสมอ
BEGIN;
DROP TABLE IF EXISTS field_validations;
COMMIT;
