-- seed_demo_ratchaburi.sql — แปลงตัวอย่างสมมติในราชบุรี 8 แปลง หลายพืช (Phase 1g)
-- ⚠️ สำหรับฐานข้อมูลทดสอบ/สาธิตเท่านั้น — ห้ามรันบน production (แปลงจะเข้าสแกนรายวัน + สถิติ dashboard)
-- สร้างจาก backend/config/demo_plots_ratchaburi.json · ต้องรัน migrate_v14 ก่อน · ลบ: DELETE FROM plots WHERE user_id = 'demo-ratchaburi';

BEGIN;

INSERT INTO users (user_id, display_name) VALUES ('demo-ratchaburi', 'ผู้ใช้ตัวอย่าง (สมมติ)') ON CONFLICT (user_id) DO NOTHING;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: มะพร้าวน้ำหอม ดำเนินสะดวก', 13.518, 99.955, 2.25, '[[99.9547, 13.5177], [99.9553, 13.5177], [99.9553, 13.5183], [99.9547, 13.5183], [99.9547, 13.5177]]'::jsonb, 'raised_bed')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['coconut_aromatic']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: องุ่น ดำเนินสะดวก', 13.535, 99.94, 2.25, '[[99.9397, 13.5347], [99.9403, 13.5347], [99.9403, 13.5353], [99.9397, 13.5353], [99.9397, 13.5347]]'::jsonb, 'raised_bed')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['grape']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: ฝรั่ง โพธาราม', 13.7, 99.85, 2.25, '[[99.8497, 13.6997], [99.8503, 13.6997], [99.8503, 13.7003], [99.8497, 13.7003], [99.8497, 13.6997]]'::jsonb, 'flat')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['guava']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: แปลงผัก บ้านโป่ง', 13.81, 99.88, 2.25, '[[99.8797, 13.8097], [99.8803, 13.8097], [99.8803, 13.8103], [99.8797, 13.8103], [99.8797, 13.8097]]'::jsonb, 'vegetable_bed')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['vegetables']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: มะม่วง เมืองราชบุรี', 13.54, 99.79, 2.25, '[[99.7897, 13.5397], [99.7903, 13.5397], [99.7903, 13.5403], [99.7897, 13.5403], [99.7897, 13.5397]]'::jsonb, 'flat')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['mango']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: ชมพู่ บางแพ', 13.69, 99.94, 2.25, '[[99.9397, 13.6897], [99.9403, 13.6897], [99.9403, 13.6903], [99.9397, 13.6903], [99.9397, 13.6897]]'::jsonb, 'raised_bed')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['rose_apple']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: สวนผสม ทุเรียน+กล้วย ปากท่อ', 13.39, 99.8, 2.25, '[[99.7997, 13.3897], [99.8003, 13.3897], [99.8003, 13.3903], [99.7997, 13.3903], [99.7997, 13.3897]]'::jsonb, 'flat')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['durian', 'banana']) AS c;

WITH p AS (
  INSERT INTO plots (user_id, name, lat, lng, area_rai, polygon, plot_type)
  VALUES ('demo-ratchaburi', 'ตัวอย่าง: ส้มโอ+ผัก วัดเพลง', 13.575, 99.905, 2.25, '[[99.9047, 13.5747], [99.9053, 13.5747], [99.9053, 13.5753], [99.9047, 13.5753], [99.9047, 13.5747]]'::jsonb, 'flat')
  RETURNING id)
INSERT INTO plot_crops (plot_id, crop_id, source) SELECT p.id, c, 'officer' FROM p, unnest(ARRAY['pomelo', 'vegetables']) AS c;

COMMIT;
