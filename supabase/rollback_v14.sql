-- rollback_v14.sql — ย้อน migrate_v14.sql
-- ⚠️ ข้อมูลชนิดพืช/ประเภทสวน/ประวัติขอบเขตที่ผู้ใช้กรอกหลัง v14 จะหาย — export ตารางเหล่านี้ก่อนรัน
-- การรวมประวัติแปลง (plot_merges) ย้อนกลับก่อน: ย้าย analyses/grid_snapshots กลับแปลงต้นทาง + เปิดใช้งานแปลงต้นทาง

BEGIN;

UPDATE analyses SET plot_id = merged_from_plot_id, merged_from_plot_id = NULL
WHERE merged_from_plot_id IS NOT NULL;
UPDATE grid_snapshots SET plot_id = merged_from_plot_id, merged_from_plot_id = NULL
WHERE merged_from_plot_id IS NOT NULL;
UPDATE plots SET is_active = TRUE WHERE id IN (SELECT source_plot_id FROM plot_merges);

ALTER TABLE analyses       DROP COLUMN IF EXISTS merged_from_plot_id;
ALTER TABLE grid_snapshots DROP COLUMN IF EXISTS merged_from_plot_id;

DROP TABLE IF EXISTS plot_merges;
DROP TABLE IF EXISTS plot_boundary_history;
DROP TABLE IF EXISTS plot_crops;

ALTER TABLE plots DROP CONSTRAINT IF EXISTS plots_plot_type_check;
ALTER TABLE plots DROP COLUMN IF EXISTS updated_at;
ALTER TABLE plots DROP COLUMN IF EXISTS boundary_check;
ALTER TABLE plots DROP COLUMN IF EXISTS plot_type;

COMMIT;
