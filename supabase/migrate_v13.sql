-- migrate_v13.sql — Phase 1b (2026-10)
-- ⚠️ ห้ามรันบน production จนกว่า: (1) backup ด้วย scripts/backup_db.sh (2) ได้รับอนุมัติ
--    rollback: supabase/rollback_v13.sql
--
-- เพิ่มตารางใหม่ line_message_log (ไม่แตะตารางเดิม):
--   - บันทึกทุกการส่งข้อความ LINE: สำเร็จ / ล้มเหลว / ถูกข้ามเพราะโควตาใกล้เต็ม
--     (2026-10 โควตาหมดแล้ว push ล้มเหลวเงียบๆ 8 วันโดยไม่มีใครรู้)
--   - ใช้กันส่งซ้ำ: คำเตือนความเสี่ยงวันเดียวกัน และเตือนฝนระดับเดิมภายใน 7 วัน
--   - เป็นตัวเลขการใช้งานสำหรับรายงาน B2G (จำนวนการแจ้งเตือนที่ส่งถึงเกษตรกรจริง)
-- โค้ด (database.log_line_message ฯลฯ) ทำงานได้แม้ยังไม่ได้รัน migration นี้ — แค่ไม่มีบันทึก
-- และไม่มีการกันส่งซ้ำของเตือนฝน

BEGIN;

CREATE TABLE IF NOT EXISTS line_message_log (
    id           BIGSERIAL   PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    plot_id      BIGINT      REFERENCES plots(id) ON DELETE SET NULL,
    kind         TEXT        NOT NULL,   -- risk_alert / rain_alert / weekly_digest / iot_alert / general
    priority     TEXT        NOT NULL,   -- critical / normal / low
    status       TEXT        NOT NULL,   -- sent / failed / skipped_quota
    http_status  SMALLINT,
    level        TEXT,                   -- ระดับที่แจ้ง (high / watch / warning / critical)
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_line_message_log_plot_kind
    ON line_message_log (plot_id, kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_line_message_log_status_created
    ON line_message_log (status, created_at DESC);

ALTER TABLE line_message_log ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'line_message_log') THEN
        CREATE POLICY "backend only" ON line_message_log FOR ALL USING (FALSE);
    END IF;
END $$;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'line_message_log';
