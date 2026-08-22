-- migrate_v10.sql
-- ตาราง scheduler_runs — เก็บสถานะ "ชั่วโมงล่าสุดที่ daily_scan/rain_alert ทำสำเร็จ
-- ของวันนี้" ให้ scheduler.py::run_job_with_catchup ใช้ตามงานย้อนหลังได้ ถ้า
-- GitHub Actions ทิ้งรอบ cron ไปเฉยๆ (ยืนยันเกิดจริง 21-22 ส.ค. 2569 — รอบ 07:00
-- หายไปทั้งรอบ ทำให้ผู้ใช้ทุกคนที่ตั้งเวลาไว้ 7 โมงไม่ได้รับการแจ้งเตือนเลยทั้งวัน)
-- รันใน Supabase Dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS scheduler_runs (
    job_name       TEXT PRIMARY KEY,
    last_run_date  DATE NOT NULL,
    last_run_hour  SMALLINT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'scheduler_runs';
