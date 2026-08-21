-- migrate_v8.sql
-- เพิ่มคอลัมน์ notify_hour — เวลาที่ผู้ใช้เลือกเองสำหรับรับการแจ้งเตือนทั้งสอง
-- แบบ (แจ้งเตือนเฉพาะตอนเสี่ยง + สรุปแปลงประจำวัน) เดิม fix ไว้ตายตัว 07:00 น.
-- ผู้ใช้ขอให้เลือกเวลาเองได้ — จำกัดเป็นตัวเลือกที่ตั้งไว้ล่วงหน้า (05:00-10:00)
-- ไม่ใช่พิมพ์เวลาเอง กันพิมพ์ผิดรูปแบบ
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS notify_hour SMALLINT NOT NULL DEFAULT 7
    CHECK (notify_hour IN (5, 6, 7, 8, 9, 10));

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'users'
  AND column_name = 'notify_hour';
