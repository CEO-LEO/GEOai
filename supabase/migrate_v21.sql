-- migrate_v21.sql — Phase 4a (2026-10): จำแนกชนิดพืช — feature, การทาย, การยืนยัน, โมเดล, ข้อมูลอ้างอิง
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v21.sql
--
-- เพิ่มอย่างเดียว (ไม่แก้ตารางเดิม):
--   reference_labels     แปลงอ้างอิงที่มีชนิดพืช จากแหล่งภายนอก เช่น ทะเบียนเกษตรกรผ่านเกษตรจังหวัด (ใช้เทรนเท่านั้น ไม่แสดงในแอป)
--   crop_features        ค่าที่สกัดจากดาวเทียมต่อแปลง/แปลงอ้างอิง ต่อชุด feature ต่อปี (แคช — คำนวณแบบ batch)
--   crop_models          โมเดลแต่ละเวอร์ชัน: พารามิเตอร์เป็น JSON (ไม่ใช้ pickle) + ผลประเมิน + ชุดข้อมูลที่ใช้ · เปิดใช้ได้ทีละตัว
--   crop_predictions     ผลทายต่อแปลง (top-3 + ความมั่นใจ) ผูกกับเวอร์ชันโมเดลและขอบเขตที่ใช้ทาย
--   crop_label_feedback  คำตอบ ใช่ / ไม่ใช่ / เลือกพืชอื่น / ไม่แน่ใจ ของเจ้าของหรือเจ้าหน้าที่ → label สำหรับรอบเทรนถัดไป

BEGIN;

CREATE TABLE IF NOT EXISTS reference_labels (
    id            BIGSERIAL   PRIMARY KEY,
    source        TEXT        NOT NULL,                 -- เช่น "farmer_registry_2569"
    source_ref    TEXT        NOT NULL,                 -- รหัสแถวในแหล่งเดิม (กันนำเข้าซ้ำ)
    crop_ids      TEXT[]      NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lng           DOUBLE PRECISION NOT NULL,
    polygon       JSONB,
    area_code     TEXT,
    consent_basis TEXT        NOT NULL,                 -- ที่มาทางกฎหมายของข้อมูล เช่น "DPA กับ สนง.เกษตรจังหวัดราชบุรี"
    imported_by   TEXT        NOT NULL,
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_ref)
);

CREATE TABLE IF NOT EXISTS crop_features (
    id            BIGSERIAL   PRIMARY KEY,
    plot_id       BIGINT      REFERENCES plots(id) ON DELETE CASCADE,
    ref_id        BIGINT      REFERENCES reference_labels(id) ON DELETE CASCADE,
    feature_set   TEXT        NOT NULL,                 -- เช่น "emb_phen_v1"
    year          INTEGER     NOT NULL,                 -- ปีของ embedding
    geom_hash     TEXT        NOT NULL,                 -- ขอบเขตเปลี่ยน → คำนวณใหม่
    vals          REAL[]      NOT NULL,
    meta          JSONB       NOT NULL DEFAULT '{}',    -- เช่น tree_fraction, จำนวนภาพ
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((plot_id IS NULL) <> (ref_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_features_plot ON crop_features (plot_id, feature_set, year) WHERE plot_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_features_ref ON crop_features (ref_id, feature_set, year) WHERE ref_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crop_models (
    version       TEXT        PRIMARY KEY,
    algorithm     TEXT        NOT NULL,
    feature_set   TEXT        NOT NULL,
    classes       TEXT[]      NOT NULL,
    params        JSONB       NOT NULL,
    metrics       JSONB       NOT NULL DEFAULT '{}',
    dataset       JSONB       NOT NULL DEFAULT '{}',    -- จำนวนตัวอย่างต่อคลาส/อำเภอ + sha256 ของชุดข้อมูล
    passes_gate   BOOLEAN     NOT NULL DEFAULT FALSE,
    active        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by    TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_models_active ON crop_models (active) WHERE active;

CREATE TABLE IF NOT EXISTS crop_predictions (
    id            BIGSERIAL   PRIMARY KEY,
    plot_id       BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    model_version TEXT        NOT NULL REFERENCES crop_models(version),
    geom_hash     TEXT        NOT NULL,
    status        TEXT        NOT NULL CHECK (status IN ('ok', 'uncertain', 'not_orchard')),
    top           JSONB       NOT NULL DEFAULT '[]',    -- [{"crop_id", "p"}] เรียงมาก→น้อย สูงสุด 3
    confidence    REAL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_crop_predictions_plot ON crop_predictions (plot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crop_label_feedback (
    id            BIGSERIAL   PRIMARY KEY,
    plot_id       BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    prediction_id BIGINT      REFERENCES crop_predictions(id) ON DELETE SET NULL,
    answer        TEXT        NOT NULL CHECK (answer IN ('yes', 'no', 'other', 'unsure')),
    crop_ids      TEXT[]      NOT NULL DEFAULT '{}',
    by_user       TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'farmer',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_crop_feedback_plot ON crop_label_feedback (plot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_crop_feedback_user ON crop_label_feedback (by_user);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['reference_labels', 'crop_features', 'crop_models', 'crop_predictions', 'crop_label_feedback'] LOOP
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
WHERE n.nspname = 'public' AND c.relname IN ('reference_labels', 'crop_features', 'crop_models', 'crop_predictions', 'crop_label_feedback')
ORDER BY 1;
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
