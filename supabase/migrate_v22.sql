-- migrate_v22.sql — Phase 3 ข้อ 6.3 (ตัวเลือก, ทำ 2026-10-28): ปักหมุดต้นไม้รายต้น + บันทึกต่อต้น
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v22.sql
--
-- เพิ่มอย่างเดียว:
--   tree_pins   หมุดต้นไม้ในแปลง (ปักจาก GPS ตอนยืนที่ต้น) — ลบ = ซ่อน (removed_at) ประวัติยังอยู่
--   tree_logs   บันทึกต่อต้น: อาการ / ใส่ปุ๋ย / พ่นยา / บันทึกอื่น (ผู้ใช้บันทึกเอง ไม่ใช่คำแนะนำของระบบ)
--   photos.tree_id  รูปที่ถ่ายให้ต้นนั้น (nullable — รูปเดิมไม่เปลี่ยน)

BEGIN;

CREATE TABLE IF NOT EXISTS tree_pins (
    id            BIGSERIAL   PRIMARY KEY,
    client_id     TEXT        NOT NULL UNIQUE,          -- สร้างในเครื่อง (กันซ้ำเวลาส่งใหม่จากคิวออฟไลน์)
    plot_id       BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    label         TEXT        NOT NULL DEFAULT '',
    lat           DOUBLE PRECISION NOT NULL,
    lng           DOUBLE PRECISION NOT NULL,
    accuracy_m    REAL,
    crop_id       TEXT,
    created_by    TEXT        NOT NULL,
    created_role  TEXT        NOT NULL DEFAULT 'farmer',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tree_pins_plot ON tree_pins (plot_id) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS tree_logs (
    id            BIGSERIAL   PRIMARY KEY,
    client_id     TEXT        NOT NULL UNIQUE,
    tree_id       BIGINT      NOT NULL REFERENCES tree_pins(id) ON DELETE CASCADE,
    kind          TEXT        NOT NULL CHECK (kind IN ('symptom', 'fertilize', 'spray', 'note')),
    happened_on   DATE        NOT NULL,
    tags          TEXT[]      NOT NULL DEFAULT '{}',
    product       TEXT        NOT NULL DEFAULT '',
    note          TEXT        NOT NULL DEFAULT '',
    by_user       TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'farmer',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tree_logs_tree ON tree_logs (tree_id, happened_on DESC);
CREATE INDEX IF NOT EXISTS idx_tree_logs_user ON tree_logs (by_user);

ALTER TABLE photos ADD COLUMN IF NOT EXISTS tree_id BIGINT REFERENCES tree_pins(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_photos_tree ON photos (tree_id) WHERE tree_id IS NOT NULL;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tree_pins', 'tree_logs'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = t) THEN
            EXECUTE format('CREATE POLICY "backend only" ON %I FOR ALL USING (FALSE)', t);
        END IF;
        EXECUTE format('REVOKE ALL ON %I FROM anon, authenticated', t);
    END LOOP;
END $$;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('tree_pins', 'tree_logs') ORDER BY 1;
SELECT column_name FROM information_schema.columns WHERE table_name = 'photos' AND column_name = 'tree_id';
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
