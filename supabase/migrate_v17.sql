-- migrate_v17.sql — Phase 2b (2026-10): PDPA — ความยินยอม, ประวัติความยินยอม, คำขอลบข้อมูล
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v17.sql
--
-- เพิ่มอย่างเดียว:
--   users.consent_version / consent_at   ฉบับนโยบายที่ผู้ใช้ยอมรับล่าสุด (NULL = ยังไม่ยอมรับ → แอปถามก่อนใช้งาน)
--   users.consent_photos_research        ยินยอมให้ใช้รูปถ่าย/การยืนยันภาคสนามปรับปรุงระบบ (ไม่บังคับ ค่าเริ่มต้น FALSE)
--   users.deletion_requested_at          ผู้ใช้ขอลบข้อมูล → ซ่อนแปลง/หยุดแจ้งเตือนทันที ผู้ดูแลลบถาวรภายหลัง
--   plots.deactivated_reason             ทำไมแปลงถูกซ่อน (merged / user_deletion_request) — ยกเลิกคำขอลบแล้วคืนเฉพาะแปลงที่ถูกซ่อนเพราะคำขอ
--   consent_logs                         ประวัติการให้/เปลี่ยน/ถอนความยินยอมทุกครั้ง

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_version TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_photos_research BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS deactivated_reason TEXT;

-- แปลงที่ถูกซ่อนจากการรวมประวัติ (1c) ระบุเหตุผลย้อนหลัง
UPDATE plots SET deactivated_reason = 'merged'
WHERE is_active = FALSE AND deactivated_reason IS NULL AND id IN (SELECT source_plot_id FROM plot_merges);

CREATE TABLE IF NOT EXISTS consent_logs (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    version     TEXT        NOT NULL,
    action      TEXT        NOT NULL,   -- accept / update / decline / deletion_request / deletion_cancel
    choices     JSONB       NOT NULL,   -- {"share_with_officers": bool, "photos_research": bool}
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_logs_user ON consent_logs (user_id, created_at DESC);

ALTER TABLE consent_logs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'consent_logs') THEN
        CREATE POLICY "backend only" ON consent_logs FOR ALL USING (FALSE);
    END IF;
END $$;
REVOKE ALL ON consent_logs FROM anon, authenticated;

COMMIT;

-- ตรวจผล
SELECT count(*) FILTER (WHERE consent_version IS NULL) AS users_without_consent, count(*) AS users FROM users;
SELECT deactivated_reason, count(*) FROM plots WHERE is_active = FALSE GROUP BY deactivated_reason;
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
