-- migrate_v19.sql — Phase 2d (2026-10): การยืนยันภาคสนาม (ground truth) "จริง / ไม่จริง / ไม่แน่ใจ"
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v19.sql
--
-- เพิ่มอย่างเดียว:
--   field_validations   คำตอบของผู้ใช้ต่อสิ่งที่ระบบบอก (รายจุดบนแผนที่ หรือทั้งแปลง) — ใช้วัดความแม่นและเป็น label ใน Phase 4
--                       context เก็บสิ่งที่ระบบแสดงตอนนั้น (ค่าเทียบในแปลง, สถานะ, z, วันที่ภาพ) เพื่อเทียบย้อนหลังได้

BEGIN;

CREATE TABLE IF NOT EXISTS field_validations (
    id                BIGSERIAL   PRIMARY KEY,
    plot_id           BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    user_id           TEXT        NOT NULL,
    role              TEXT        NOT NULL,                 -- farmer / officer / admin ตอนตอบ
    scope             TEXT        NOT NULL CHECK (scope IN ('point', 'plot')),
    problem           TEXT        NOT NULL,                 -- ประเภทปัญหาเดียวกับ recommendations.PROBLEMS_TH
    severity          TEXT,
    answer            TEXT        NOT NULL CHECK (answer IN ('true', 'false', 'unsure')),
    lat               DOUBLE PRECISION,
    lng               DOUBLE PRECISION,
    note              TEXT        NOT NULL DEFAULT '',
    photo_client_id   TEXT,                                 -- รูปที่แนบคู่กัน (photos.client_id) ถ้ามี
    context           JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_field_validations_plot ON field_validations (plot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_field_validations_problem ON field_validations (problem, answer);

ALTER TABLE field_validations ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'field_validations') THEN
        CREATE POLICY "backend only" ON field_validations FOR ALL USING (FALSE);
    END IF;
END $$;
REVOKE ALL ON field_validations FROM anon, authenticated;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'field_validations';
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
