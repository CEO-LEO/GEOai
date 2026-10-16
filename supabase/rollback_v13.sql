-- rollback_v13.sql — ย้อน migrate_v13.sql (ลบตาราง line_message_log ที่เพิ่มใหม่)
-- ⚠️ ลบบันทึกการส่งข้อความทั้งหมดในตารางนี้ — export ก่อนถ้าต้องการเก็บไว้
-- โค้ดยังทำงานได้หลังลบ (ปิดการบันทึก/กันส่งซ้ำเตือนฝนเอง)

BEGIN;
DROP TABLE IF EXISTS line_message_log;
COMMIT;
