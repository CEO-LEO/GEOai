-- migrate_v7.sql
-- เพิ่มคอลัมน์ notify_daily_digest — เปิดรับ "สรุปแปลงประจำวัน 07:00" (ทุกแปลง
-- ทุกเช้า ไม่ว่าจะเสี่ยงหรือไม่) แยกต่างหากจาก notify_weekly ที่แจ้งเฉพาะตอน
-- เสี่ยงสูงเท่านั้น — ผู้ใช้ขอฟีเจอร์นี้เพิ่ม ไม่ต้องการรวมกับ notify_weekly
-- เพราะไม่อยากให้ผู้ใช้คนอื่นที่เปิด notify_weekly ไว้แล้วโดนสรุปทุกวันไปด้วย
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS notify_daily_digest BOOLEAN NOT NULL DEFAULT FALSE;

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'users'
  AND column_name = 'notify_daily_digest';
