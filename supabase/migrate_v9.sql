-- migrate_v9.sql
-- เพิ่มคอลัมน์ thumbnail_url ให้ตาราง plots — เก็บ URL รูปย่อดาวเทียมของแปลง
-- (ครอปจากภาพถ่ายดาวเทียมที่ระบบดึงมาทำแผนที่ความชื้นอยู่แล้ว ไม่เรียก GEE เพิ่ม)
-- ใช้แสดงในหน้า "แปลงของฉัน" (LIFF) ให้แยกแต่ละแปลงออกจากกันง่ายขึ้น
-- (ผู้ใช้ขอ — การ์ดแปลงเดิมมีแค่ชื่อ+พิกัด หน้าตาคล้ายกันเกินไปเวลามีหลายแปลง)
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE plots
    ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'plots'
  AND column_name = 'thumbnail_url';
