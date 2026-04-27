-- ══════════════════════════════════════════════════════
-- GEOai — Supabase SQL Schema v3
-- วิธีใช้: Supabase Dashboard → SQL Editor → New query → วาง → Run
-- v3: เพิ่ม land displacement, fertilizer, yield estimation
-- ══════════════════════════════════════════════════════

-- ── 1. ตาราง users (สร้างก่อนเพราะ FK ต้องอ้างอิง) ──
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT        PRIMARY KEY,   -- LINE user ID
    display_name  TEXT,
    province      TEXT,
    crop_type     TEXT        DEFAULT 'durian',
    notify_weekly BOOLEAN     DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. ตาราง plots (หลายแปลงต่อคน) ──────────────────
CREATE TABLE IF NOT EXISTS plots (
    id          BIGSERIAL       PRIMARY KEY,
    user_id     TEXT            NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name        TEXT            NOT NULL DEFAULT 'แปลงที่ 1',
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    area_rai    REAL,
    polygon     JSONB,          -- GeoJSON coordinates [[lng,lat], ...] or null (point-only)
    is_active   BOOLEAN         DEFAULT TRUE,
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plots_user_id ON plots(user_id);

-- ── 3. ตารางผลวิเคราะห์แปลง ─────────────────────────
CREATE TABLE IF NOT EXISTS analyses (
    id                BIGSERIAL       PRIMARY KEY,
    user_id           TEXT            NOT NULL,
    plot_id           BIGINT          REFERENCES plots(id) ON DELETE SET NULL,
    lat               DOUBLE PRECISION NOT NULL,
    lng               DOUBLE PRECISION NOT NULL,
    ndvi_now          REAL,
    ndvi_prev         REAL,
    ndvi_change       REAL,
    soil_moisture_vv  REAL,
    elevation         REAL,
    elevation_diff    REAL,
    -- v3: land displacement fields
    displacement_vv_change    REAL,        -- VV backscatter change (dB) year-over-year
    displacement_vh_change    REAL,        -- VH backscatter change (dB)
    surface_stability         REAL,        -- 0-1 stability score
    displacement_level        TEXT,        -- 'low', 'medium', 'high'
    -- v3: fertilizer recommendation
    fertilizer_n              REAL,        -- กก./ต้น/ปี
    fertilizer_p              REAL,
    fertilizer_k              REAL,
    fertilizer_ca             REAL,
    fertilizer_mg             REAL,
    fertilizer_level          TEXT,        -- 'maintenance', 'recovery', 'intensive', 'critical'
    -- v3: yield estimation
    yield_estimated_kg        REAL,        -- กก./ไร่/ปี
    yield_quality             TEXT,        -- 'high', 'medium', 'low', 'very_low'
    -- v3: land impact
    land_impact_severity      TEXT,        -- 'low', 'medium', 'high'
    land_impact_score         REAL,        -- 0-100
    -- topsoil analysis (BSI)
    bsi_score                 REAL,        -- Bare Soil Index (BSI) จาก Sentinel-2
    risk_level                TEXT,        -- topsoil risk: 'low', 'medium', 'high'
    -- predictive AI
    predicted_yield_kg_per_rai REAL,       -- พยากรณ์ผลผลิต (Mock ML) กก./ไร่
    message           TEXT,
    created_at        TIMESTAMPTZ     DEFAULT NOW()
);

-- ── 4. Indexes ───────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_analyses_user_id
    ON analyses (user_id);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at
    ON analyses (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_plot_id
    ON analyses (plot_id);
CREATE INDEX IF NOT EXISTS idx_analyses_location
    ON analyses (lat, lng);

-- ── 5. Row Level Security ────────────────────────────
ALTER TABLE users     ENABLE ROW LEVEL SECURITY;
ALTER TABLE plots     ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyses  ENABLE ROW LEVEL SECURITY;

-- service_role (backend) ข้ามผ่าน RLS ได้เสมอ
-- anon/authenticated ต้องผ่าน policy → lock ไว้
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'users') THEN
        CREATE POLICY "backend only" ON users     FOR ALL USING (FALSE);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'plots') THEN
        CREATE POLICY "backend only" ON plots     FOR ALL USING (FALSE);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'analyses') THEN
        CREATE POLICY "backend only" ON analyses  FOR ALL USING (FALSE);
    END IF;
END $$;

-- ── 6. View สรุปสถิติสำหรับ dashboard ────────────────
CREATE OR REPLACE VIEW dashboard_summary AS
SELECT
    COUNT(*)                                                AS total_analyses,
    COUNT(DISTINCT user_id)                                 AS unique_users,
    COUNT(*) FILTER (WHERE ndvi_change < -0.20
                     OR (elevation_diff < -1.5
                         AND soil_moisture_vv > -10))       AS high_risk_count,
    COUNT(*) FILTER (WHERE ndvi_change BETWEEN -0.20 AND -0.10
                     OR elevation_diff < -1.5)              AS medium_risk_count,
    ROUND(AVG(ndvi_now)::NUMERIC, 3)                        AS avg_ndvi,
    MAX(created_at)                                         AS latest_at
FROM analyses;

-- ── 7. ตรวจสอบว่าสร้างสำเร็จ ─────────────────────────
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('analyses', 'users', 'plots');
