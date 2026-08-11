-- migrate_v6.sql
-- เพิ่มตาราง grid_snapshots — เก็บผลตารางความชื้น/น้ำขังรายจุดของแต่ละแปลง
-- ทุกวัน (จาก daily_scan_job) เพื่อดูว่าจุดไหน "ชื้นซ้ำๆ ต่อเนื่อง" หลายวัน —
-- สัญญาณบ่งชี้ว่าน่าจะมีทางน้ำไหลผ่านใต้ผิวดินตรงจุดนั้นจริง (ไม่ใช่แค่ฝนตกครั้งเดียว)
-- รันใน Supabase Dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS grid_snapshots (
    id           BIGSERIAL        PRIMARY KEY,
    plot_id      BIGINT           NOT NULL REFERENCES plots(id) ON DELETE CASCADE,
    lat          DOUBLE PRECISION NOT NULL,
    lng          DOUBLE PRECISION NOT NULL,
    status       TEXT             NOT NULL,   -- waterlogged/wet/optimal/dry/drought
    swab_index   REAL,
    created_at   TIMESTAMPTZ      DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_grid_snapshots_plot_id    ON grid_snapshots (plot_id);
CREATE INDEX IF NOT EXISTS idx_grid_snapshots_created_at ON grid_snapshots (created_at DESC);

ALTER TABLE grid_snapshots ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = 'grid_snapshots') THEN
        CREATE POLICY "backend only" ON grid_snapshots FOR ALL USING (FALSE);
    END IF;
END $$;

-- ตรวจสอบผลลัพธ์
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'grid_snapshots';
