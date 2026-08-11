-- migrate_v5.sql
-- แก้บั๊ก: ปุ่ม "ดูผลวิเคราะห์ล่าสุด" (action=history) ในไลน์ แสดงข้อมูลไม่ตรงกับ
-- ผลที่เพิ่งวิเคราะห์สดๆ เพราะตาราง analyses เก็บแค่คอลัมน์ flat บางส่วน
-- (ไม่มีคอลัมน์สำหรับ swab, bsi ใช้ชื่อคอลัมน์ต่างจาก field จริง ฯลฯ)
-- ทำให้ build_result_flex() ที่ต้องการ dict รูปแบบเดิม (nested) จากผลวิเคราะห์สด
-- พอได้แถวจาก DB ที่โครงสร้างไม่ตรงกัน ก็ขึ้น "—"/หายไปทั้งบล็อก (เช่น BSI, SWAB,
-- ผลผลิตประเมิน) ทั้งที่ผลวิเคราะห์จริงมีค่าเหล่านี้ครบ
--
-- ทางแก้: เพิ่มคอลัมน์ full_data (JSONB) เก็บ dict ผลวิเคราะห์ทั้งก้อนแบบดิบ
-- ไว้คู่กับคอลัมน์ flat เดิม (ยังเก็บไว้เผื่อ query/dashboard เดิมใช้อยู่)
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE analyses
  ADD COLUMN IF NOT EXISTS full_data JSONB;

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'analyses'
  AND column_name = 'full_data';
