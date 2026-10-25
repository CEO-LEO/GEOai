-- migrate_v20.sql — Phase 3a (2026-10): ตั้งความถี่เตือนฝน + ร่างคำแนะนำ "ฝนหนักกำลังมา"
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v20.sql
--
-- เพิ่มอย่างเดียว:
--   users.rain_alert_freq   normal = เตือนทุกครั้งที่ฝนหนัก (ไม่ซ้ำระดับเดิมใน 7 วัน) · weekly_max = สูงสุดสัปดาห์ละครั้ง
--   recommendation_rules    ร่าง 3 ข้อของปัญหา heavy_rain_coming (สร้างจาก backend/config/recommendation_rules_seed.json)

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS rain_alert_freq TEXT NOT NULL DEFAULT 'normal';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_rain_alert_freq_check') THEN
        ALTER TABLE users ADD CONSTRAINT users_rain_alert_freq_check CHECK (rain_alert_freq IN ('normal', 'weekly_max'));
    END IF;
END $$;

INSERT INTO recommendation_rules (rule_key, crop, problem, severity, title_th, reason_th, actions_th, source, removed, updated_by)
VALUES
    ('heavy_rain_coming.medium.*', '*', 'heavy_rain_coming', 'medium', 'ฝนหนักกำลังมา — เตรียมแปลงก่อนฝนตก', 'พยากรณ์อากาศบอกว่าจะมีฝนตกหนักในพื้นที่แปลงภายในไม่กี่วันนี้', ARRAY['เปิดร่องระบายน้ำให้โล่ง เก็บเศษใบไม้ที่อุดตันออก', 'งดใส่ปุ๋ยและพ่นยาก่อนฝนตก', 'หลังฝนหยุด เดินดูจุดที่ชื้นกว่าส่วนอื่นของแปลงว่ามีน้ำขังหรือไม่']::text[], 'weather_alert (ข้อความเตือนฝนเดิม) — Phase 3', 'ความลึกรากและจำนวนชั่วโมงก่อนฝน (ยังไม่ได้ตรวจ)', 'seed'),
    ('heavy_rain_coming.high.*', '*', 'heavy_rain_coming', 'high', 'ฝนหนักมากกำลังมา — เตรียมแปลงก่อนฝนตก', 'พยากรณ์อากาศบอกว่าจะมีฝนตกหนักมากในพื้นที่แปลงภายในไม่กี่วันนี้', ARRAY['เปิดร่องระบายน้ำให้โล่งทั้งแปลง', 'งดใส่ปุ๋ยและพ่นยาก่อนฝนตก', 'ถ้าแปลงอยู่ที่ต่ำหรือเคยน้ำขัง เตรียมเครื่องสูบน้ำไว้ล่วงหน้า', 'หลังฝนหยุด ดูจุดที่ชื้นกว่าส่วนอื่นของแปลงก่อน']::text[], 'weather_alert (ข้อความเตือนฝนเดิม) — Phase 3', '', 'seed'),
    ('heavy_rain_coming.medium.group:vegetable', 'group:vegetable', 'heavy_rain_coming', 'medium', 'ฝนหนักกำลังมา — เตรียมแปลงผักก่อนฝนตก', 'พยากรณ์อากาศบอกว่าจะมีฝนตกหนักในพื้นที่แปลงภายในไม่กี่วันนี้', ARRAY['ทำร่องระหว่างแปลงผักให้น้ำไหลออกได้', 'เก็บผลผลิตที่พร้อมเก็บก่อนฝนตกหนัก', 'งดใส่ปุ๋ยก่อนฝนตก']::text[], 'ใหม่ (ปรับจาก heavy_rain_coming ทั่วไปสำหรับแปลงผัก) — Phase 3', '', 'seed')
ON CONFLICT (rule_key) DO NOTHING;

INSERT INTO recommendation_rule_history (rule_id, version, action, changed_by, snapshot)
SELECT r.id, r.version, 'seed', 'seed', to_jsonb(r)
FROM recommendation_rules r
WHERE r.problem = 'heavy_rain_coming' AND r.updated_by = 'seed'
  AND NOT EXISTS (SELECT 1 FROM recommendation_rule_history h WHERE h.rule_id = r.id);

COMMIT;

-- ตรวจผล
SELECT rain_alert_freq, count(*) FROM users GROUP BY rain_alert_freq;
SELECT problem, status, count(*) FROM recommendation_rules WHERE problem = 'heavy_rain_coming' GROUP BY problem, status;
