-- migrate_v4.sql
-- เพิ่มคอลัมน์ BSI / Topsoil / ML yield สำหรับ GEOai v3+
-- รันใน Supabase Dashboard → SQL Editor

ALTER TABLE analyses
  ADD COLUMN IF NOT EXISTS bsi_score                 REAL,
  ADD COLUMN IF NOT EXISTS risk_level                TEXT,
  ADD COLUMN IF NOT EXISTS predicted_yield_kg_per_rai REAL;

-- Optional: index เพื่อ query รายงานตาม risk_level ได้เร็วขึ้น
CREATE INDEX IF NOT EXISTS idx_analyses_risk_level
  ON analyses (risk_level);

-- ตรวจสอบผลลัพธ์
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'analyses'
  AND column_name IN ('bsi_score', 'risk_level', 'predicted_yield_kg_per_rai');
