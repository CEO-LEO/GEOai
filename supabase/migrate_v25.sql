-- migrate_v25.sql — บันทึกการใช้ Earth Engine จริง (EECU-วินาที) ต่องาน (2026-11-05 — earth-engine-cost.md)
-- ก่อนรัน: backup · rollback: supabase/rollback_v25.sql · เพิ่มอย่างเดียว · ไม่มีพิกัด/รหัสผู้ใช้ — เก็บแค่ชื่องาน เวลา และ EECU
--   ใช้เมื่อเปิด env EE_USAGE_METER=1 (ปิดเป็นค่าเริ่มต้น) — ดู backend/ee_meter.py
BEGIN;

CREATE TABLE IF NOT EXISTS ee_usage_log (
    id             BIGSERIAL        PRIMARY KEY,
    op             TEXT             NOT NULL,
    eecu_seconds   DOUBLE PRECISION NOT NULL,
    wall_seconds   DOUBLE PRECISION,
    profile_calls  INTEGER,
    ok             BOOLEAN          NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ee_usage_log_time ON ee_usage_log (created_at DESC, op);

ALTER TABLE ee_usage_log ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'ee_usage_log') THEN
        CREATE POLICY "backend only" ON ee_usage_log FOR ALL USING (FALSE);
    END IF;
END $$;
REVOKE ALL ON ee_usage_log FROM anon, authenticated;

COMMIT;
