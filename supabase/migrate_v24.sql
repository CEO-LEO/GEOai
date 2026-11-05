-- migrate_v24.sql — สุ่มจุดให้เกษตรกรอาสาตรวจ (แผนพิสูจน์ความแม่นยำ ข้อ 5.4 — 2026-11-05)
-- ก่อนรัน: backup · rollback: supabase/rollback_v24.sql · เพิ่มอย่างเดียว
--   validation_samples          การสุ่มแต่ละครั้ง: seed + วิธี + จุดตั้งต้นทั้งหมด + จุดที่สุ่มได้ (ตรวจย้อนหลัง/ทำซ้ำได้)
--   field_validations.selection 'user' = ผู้ใช้เลือกจุดเอง · 'random' = จุดจากการสุ่ม (server ตั้งเอง) — recall ใช้เฉพาะ 'random'
--   field_validations.sample_id การสุ่มที่คำตอบนี้มาจาก
BEGIN;

CREATE TABLE IF NOT EXISTS validation_samples (
    id               BIGSERIAL   PRIMARY KEY,
    plot_id          BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    user_id          TEXT        NOT NULL,
    seed             BIGINT      NOT NULL,
    method           TEXT        NOT NULL,
    params           JSONB       NOT NULL,
    candidates_n     INTEGER     NOT NULL,
    candidates_hash  TEXT        NOT NULL,
    candidates       JSONB       NOT NULL,
    points           JSONB       NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_validation_samples_plot ON validation_samples (plot_id, user_id, created_at DESC);

ALTER TABLE field_validations ADD COLUMN IF NOT EXISTS selection TEXT NOT NULL DEFAULT 'user';
ALTER TABLE field_validations DROP CONSTRAINT IF EXISTS field_validations_selection_check;
ALTER TABLE field_validations ADD CONSTRAINT field_validations_selection_check CHECK (selection IN ('user', 'random'));
ALTER TABLE field_validations ADD COLUMN IF NOT EXISTS sample_id BIGINT REFERENCES validation_samples(id) ON DELETE SET NULL;

ALTER TABLE validation_samples ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'validation_samples') THEN
        CREATE POLICY "backend only" ON validation_samples FOR ALL USING (FALSE);
    END IF;
END $$;
REVOKE ALL ON validation_samples FROM anon, authenticated;

COMMIT;

SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'validation_samples';
