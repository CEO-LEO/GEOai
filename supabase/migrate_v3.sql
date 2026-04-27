-- ══════════════════════════════════════════════════════
-- GEOai — Migration to v3
-- เพิ่มคอลัมน์ land displacement, fertilizer, yield estimation
-- วิธีใช้: Supabase Dashboard → SQL Editor → New query → วาง → Run
-- ══════════════════════════════════════════════════════

-- ── Land displacement fields ──
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS displacement_vv_change REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS displacement_vh_change REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS surface_stability      REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS displacement_level     TEXT;

-- ── Fertilizer recommendation fields ──
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_n     REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_p     REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_k     REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_ca    REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_mg    REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fertilizer_level TEXT;

-- ── Yield estimation fields ──
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS yield_estimated_kg REAL;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS yield_quality      TEXT;

-- ── Land impact fields ──
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS land_impact_severity TEXT;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS land_impact_score    REAL;

-- ── Index for displacement queries ──
CREATE INDEX IF NOT EXISTS idx_analyses_displacement_level
    ON analyses (displacement_level)
    WHERE displacement_level IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_analyses_yield_quality
    ON analyses (yield_quality)
    WHERE yield_quality IS NOT NULL;
