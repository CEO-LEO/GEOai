-- migrate_v14.sql — Phase 1c (2026-10): ชนิดพืชหลายชนิด + ประเภทสวน + แก้ขอบเขตแปลงโดยเก็บประวัติ
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v14.sql
--
-- เพิ่มอย่างเดียว ไม่ลบ/ไม่เปลี่ยนชนิดคอลัมน์เดิม:
--   plots.plot_type        flat / raised_bed / vegetable_bed (NULL = ยังไม่ระบุ — แปลงเดิมทั้งหมด)
--   plots.boundary_check   ผลตรวจคุณภาพขอบเขตล่าสุด (คำเตือน + สัดส่วนประเภทพื้นที่) — ใช้ทำแถบบนการ์ดแปลง
--   plots.updated_at       เวลาแก้ไขแปลงล่าสุด
--   plot_crops             ชนิดพืชของแปลง (หลายชนิดได้) — source: user (ผู้ใช้เลือก) / officer / model (Phase 4)
--   plot_boundary_history  ขอบเขตเดิมก่อนแก้ไข (ประวัติวิเคราะห์ยังผูกกับ plot_id เดิม ไม่หาย)
--   plot_merges            บันทึกการรวมประวัติจากแปลงซ้ำ (แปลงต้นทางปิดใช้งาน ไม่ลบ) + คอลัมน์
--   analyses.merged_from_plot_id / grid_snapshots.merged_from_plot_id ไว้ย้อนกลับการรวมได้

BEGIN;

ALTER TABLE plots ADD COLUMN IF NOT EXISTS plot_type TEXT;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS boundary_check JSONB;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'plots_plot_type_check') THEN
        ALTER TABLE plots ADD CONSTRAINT plots_plot_type_check
            CHECK (plot_type IS NULL OR plot_type IN ('flat', 'raised_bed', 'vegetable_bed'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS plot_crops (
    plot_id     BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    crop_id     TEXT        NOT NULL,          -- id ใน backend/config/crops.json
    source      TEXT        NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'officer', 'model')),
    confirmed   BOOLEAN     NOT NULL DEFAULT FALSE,   -- เจ้าหน้าที่ยืนยันแล้ว (Phase 2)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (plot_id, crop_id)
);

CREATE TABLE IF NOT EXISTS plot_boundary_history (
    id          BIGSERIAL   PRIMARY KEY,
    plot_id     BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    area_rai    REAL,
    polygon     JSONB,
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plot_boundary_history_plot ON plot_boundary_history (plot_id, replaced_at DESC);

CREATE TABLE IF NOT EXISTS plot_merges (
    id                BIGSERIAL   PRIMARY KEY,
    user_id           TEXT        NOT NULL,
    source_plot_id    BIGINT      NOT NULL,
    target_plot_id    BIGINT      NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE analyses       ADD COLUMN IF NOT EXISTS merged_from_plot_id BIGINT;
ALTER TABLE grid_snapshots ADD COLUMN IF NOT EXISTS merged_from_plot_id BIGINT;

ALTER TABLE plot_crops            ENABLE ROW LEVEL SECURITY;
ALTER TABLE plot_boundary_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE plot_merges           ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['plot_crops', 'plot_boundary_history', 'plot_merges'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = t) THEN
            EXECUTE format('CREATE POLICY "backend only" ON %I FOR ALL USING (FALSE)', t);
        END IF;
    END LOOP;
END $$;
-- default privileges ถูกถอนไว้แล้วใน v12 — ถอนซ้ำเผื่อรันบนฐานที่ยังไม่ได้รัน v12
REVOKE ALL ON plot_crops, plot_boundary_history, plot_merges FROM anon, authenticated;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('plot_crops', 'plot_boundary_history', 'plot_merges');

SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'plots'
  AND column_name IN ('plot_type', 'boundary_check', 'updated_at');

SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
