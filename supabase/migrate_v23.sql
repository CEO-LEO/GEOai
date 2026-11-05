-- migrate_v23.sql — พืชเริ่มต้น "ทุเรียน" (แผนทุน TED Fund โฟกัสทุเรียน — docs/v2/decisions.md 2026-11-05)
--   plot_crops.source เพิ่มค่า 'default' = ระบบตั้งให้ ผู้ใช้ไม่ได้เลือก (ไม่ใช้เป็น label ฝึกโมเดล)
--   users.consent_volunteer_test = ยินยอมเข้าร่วมทดสอบเป็นเกษตรกรอาสา (ตอบยืนยันภาคสนามเพื่อวัดความแม่นยำ)
--   แปลงที่ยังไม่มีพืชเลย → ใส่ทุเรียน source 'default' (เพิ่มอย่างเดียว ไม่แตะแถวที่ผู้ใช้เลือกไว้)
-- rollback: rollback_v23.sql
BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_volunteer_test BOOLEAN NOT NULL DEFAULT FALSE;  -- เกษตรกรอาสาร่วมทดสอบ (ไม่บังคับ ค่าเริ่มต้นปิด)
ALTER TABLE plot_crops DROP CONSTRAINT IF EXISTS plot_crops_source_check;
ALTER TABLE plot_crops ADD CONSTRAINT plot_crops_source_check CHECK (source IN ('user', 'officer', 'model', 'default'));
INSERT INTO plot_crops (plot_id, crop_id, source)
SELECT p.id, 'durian', 'default' FROM plots p
WHERE NOT EXISTS (SELECT 1 FROM plot_crops c WHERE c.plot_id = p.id);
COMMIT;
