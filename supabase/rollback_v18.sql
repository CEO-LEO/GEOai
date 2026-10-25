-- rollback_v18.sql — ย้อน migrate_v18.sql
-- ⚠️ รายการรูปที่อัปโหลดหลัง v18 จะหาย (ไฟล์ใน bucket plot-photos ยังอยู่ — export ตาราง photos ก่อนรันเพื่อจับคู่ไฟล์)
BEGIN;
DROP TABLE IF EXISTS photos;
COMMIT;
