-- rollback_v21.sql — ย้อน migrate_v21.sql
-- ⚠️ feature ที่คำนวณไว้ โมเดลที่เทรน ผลทาย คำตอบยืนยันพืช และแปลงอ้างอิงที่นำเข้าหลัง v21 จะหาย
--    (export ตาราง crop_label_feedback และ reference_labels ก่อนรัน — เป็น label ที่หาใหม่ยาก)
BEGIN;
DROP TABLE IF EXISTS crop_label_feedback;
DROP TABLE IF EXISTS crop_predictions;
DROP TABLE IF EXISTS crop_models;
DROP TABLE IF EXISTS crop_features;
DROP TABLE IF EXISTS reference_labels;
COMMIT;
