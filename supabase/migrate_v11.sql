-- migrate_v11.sql
-- เปลี่ยนค่าเริ่มต้นของ notify_daily_digest เป็น TRUE — ผู้ใช้ต้องการให้ทุกคน
-- (รวมผู้ใช้ใหม่ที่จะสมัครในอนาคต) ได้รับสรุปแปลงประจำวันเป็นค่าเริ่มต้น เพิ่มเติม
-- จากการแจ้งเตือนเฉพาะตอนเสี่ยงเดิม (ไม่ได้แทนที่ — notify_weekly ยังทำงานเหมือนเดิม)
--
-- ผู้ใช้จริง 8 คนที่มีอยู่แล้วตอนนี้ (2026-08-24) ถูกอัปเดตเป็น notify_daily_digest
-- = true ไปแล้วโดยตรงผ่าน Supabase REST API (ไม่ต้องรัน UPDATE ซ้ำในนี้) — ไฟล์นี้
-- แก้แค่ DEFAULT ของคอลัมน์ให้ผู้ใช้ใหม่ในอนาคตได้ค่านี้อัตโนมัติโดยไม่ต้องกดเปิดเอง
--
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE users
    ALTER COLUMN notify_daily_digest SET DEFAULT TRUE;

-- ตรวจสอบผลลัพธ์
SELECT column_name, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'users'
  AND column_name = 'notify_daily_digest';
