-- migrate_v18.sql — Phase 2c (2026-10): รูปถ่ายภาคสนาม
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v18.sql
--
-- เพิ่มอย่างเดียว:
--   photos   รูปที่แนบกับแปลง (หรือจุดในแปลง) — ไฟล์อยู่ใน Storage bucket private "plot-photos" (ลบ EXIF แล้ว)
--            พิกัด/เวลาที่ถ่ายเก็บแยกในตาราง · client_id กันอัปโหลดซ้ำตอนสัญญาณหลุดแล้วส่งใหม่

BEGIN;

CREATE TABLE IF NOT EXISTS photos (
    id            BIGSERIAL   PRIMARY KEY,
    client_id     TEXT        NOT NULL UNIQUE,          -- สร้างในเครื่องผู้ใช้ (กันซ้ำเวลาส่งใหม่)
    plot_id       BIGINT      NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    uploaded_by   TEXT        NOT NULL,
    uploader_role TEXT        NOT NULL DEFAULT 'farmer',
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    gps_source    TEXT CHECK (gps_source IS NULL OR gps_source IN ('exif', 'device', 'map_point')),
    taken_at      TIMESTAMPTZ,
    tags          TEXT[]      NOT NULL DEFAULT '{}',
    note          TEXT        NOT NULL DEFAULT '',
    storage_path  TEXT        NOT NULL,
    width         INTEGER,
    height        INTEGER,
    bytes         INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_photos_plot ON photos (plot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_photos_uploaded_by ON photos (uploaded_by);

ALTER TABLE photos ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'photos') THEN
        CREATE POLICY "backend only" ON photos FOR ALL USING (FALSE);
    END IF;
END $$;
REVOKE ALL ON photos FROM anon, authenticated;

COMMIT;

-- ตรวจผล
SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'photos';
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
