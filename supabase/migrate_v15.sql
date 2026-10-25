-- migrate_v15.sql — Phase 1d (2026-10): ตารางคำแนะนำ "แก้อย่างไร" + ประวัติการแก้ไข
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v15.sql
--
-- เพิ่มอย่างเดียว ไม่แตะตารางเดิม:
--   recommendation_rules          กฎ พืช × ปัญหา × ความรุนแรง → หัวข้อ/เหตุผล/วิธีแก้ · status draft/reviewed/retired
--   recommendation_rule_history   ทุกการแก้ไข/อนุมัติ (เก็บทั้งแถวหลังแก้ใน snapshot)
-- seed: ร่างตั้งต้นจาก backend/config/recommendation_rules_seed.json (สร้างไฟล์นี้จาก JSON นั้น — แก้ที่ JSON แล้วสร้างใหม่)
--       ทุกข้อเป็น draft ตัดชื่อสารเคมี/ปริมาณปุ๋ย/ตัวเลขที่ยังไม่ได้ตรวจออกแล้ว · รันซ้ำได้ (ON CONFLICT DO NOTHING)

BEGIN;

CREATE TABLE IF NOT EXISTS recommendation_rules (
    id           BIGSERIAL   PRIMARY KEY,
    rule_key     TEXT        UNIQUE,                 -- id ของร่างตั้งต้น (กฎที่เพิ่มจากหน้า admin = NULL)
    crop         TEXT        NOT NULL DEFAULT '*',   -- '*' / 'group:<กลุ่ม>' / id ใน crops.json
    problem      TEXT        NOT NULL,
    severity     TEXT        NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    title_th     TEXT        NOT NULL,
    reason_th    TEXT        NOT NULL DEFAULT '',
    actions_th   TEXT[]      NOT NULL DEFAULT '{}',
    status       TEXT        NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'retired')),
    source       TEXT        NOT NULL DEFAULT '',     -- ที่มาของข้อความ
    removed      TEXT        NOT NULL DEFAULT '',     -- สิ่งที่ตัดออกจากข้อความเดิม (ให้ผู้ตรวจเห็น)
    reviewed_by  TEXT,
    reviewed_at  TIMESTAMPTZ,
    version      INTEGER     NOT NULL DEFAULT 1,
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recommendation_rules_lookup ON recommendation_rules (problem, severity, crop);

CREATE TABLE IF NOT EXISTS recommendation_rule_history (
    id          BIGSERIAL   PRIMARY KEY,
    rule_id     BIGINT      NOT NULL REFERENCES recommendation_rules(id) ON DELETE CASCADE,
    version     INTEGER,
    action      TEXT        NOT NULL,               -- create / edit / approve / retire / restore / seed
    changed_by  TEXT,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot    JSONB       NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recommendation_rule_history_rule ON recommendation_rule_history (rule_id, changed_at DESC);

ALTER TABLE recommendation_rules        ENABLE ROW LEVEL SECURITY;
ALTER TABLE recommendation_rule_history ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['recommendation_rules', 'recommendation_rule_history'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = t) THEN
            EXECUTE format('CREATE POLICY "backend only" ON %I FOR ALL USING (FALSE)', t);
        END IF;
    END LOOP;
END $$;
REVOKE ALL ON recommendation_rules, recommendation_rule_history FROM anon, authenticated;

-- ── ร่างตั้งต้น (draft ทั้งหมด) ──
INSERT INTO recommendation_rules (rule_key, crop, problem, severity, title_th, reason_th, actions_th, source, removed, updated_by)
VALUES
    ('wet_spot.medium.*', '*', 'wet_spot', 'medium', 'จุดนี้ชื้นกว่าส่วนอื่นของแปลง', 'ภาพดาวเทียมเรดาร์เห็นว่าดินตรงนี้ชื้นกว่าจุดอื่นในแปลงเดียวกัน อาจเป็นที่ต่ำหรือน้ำระบายช้า', ARRAY['ไปดูที่จุดนี้ว่ามีน้ำขังหลังฝนหรือไม่', 'ตรวจร่องระบายน้ำใกล้จุดนี้ไม่ให้อุดตัน', 'ลดการให้น้ำตรงจุดนี้ถ้าดินยังแฉะ', 'สังเกตใบเหลืองหรือใบร่วงที่โคนต้น ซึ่งอาจเป็นสัญญาณว่ารากมีปัญหา']::text[], 'rule_engine (ชื้นเกิน) + gee_analysis._calc_swab advice (wet)', '', 'seed'),
    ('wet_spot.high.*', '*', 'wet_spot', 'high', 'จุดนี้ชื้นกว่าส่วนอื่นของแปลงชัดเจน', 'ดินตรงนี้ชื้นกว่าจุดอื่นในแปลงมาก อาจมีน้ำขังหรือน้ำระบายไม่ออก', ARRAY['ไปดูที่จุดนี้โดยเร็ว ถ้ามีน้ำขังให้ทำทางระบายน้ำออก', 'งดให้น้ำตรงจุดนี้จนกว่าดินจะแห้งลง', 'ถ้าพบรากเน่าหรือต้นเหี่ยว ปรึกษาเจ้าหน้าที่เกษตรเรื่องการป้องกันโรค']::text[], 'rule_engine (น้ำขังวิกฤต) + gee_analysis._calc_swab advice (waterlogged)', 'ชื่อสาร Metalaxyl / Phosphonate / เมทาแลกซิล, ความลึกร่อง ≥ 50 ซม., ธาตุ Ca/K', 'seed'),
    ('wet_spot.medium.group:vegetable', 'group:vegetable', 'wet_spot', 'medium', 'จุดนี้ชื้นกว่าส่วนอื่นของแปลงผัก', 'ดินตรงนี้ชื้นกว่าจุดอื่นในแปลงเดียวกัน อาจเป็นที่ต่ำหรือน้ำระบายช้า', ARRAY['ดูว่ามีน้ำขังในร่องระหว่างแปลงผักหรือไม่', 'ทำร่องระหว่างแปลงให้น้ำไหลออกได้', 'ลดการรดน้ำตรงจุดนี้ถ้าดินยังแฉะ']::text[], 'ใหม่ (ปรับจาก wet_spot ทั่วไปสำหรับแปลงผัก)', '', 'seed'),
    ('water_collect.medium.*', '*', 'water_collect', 'medium', 'จุดที่น้ำจากรอบข้างไหลมารวม', 'จากความสูงต่ำของพื้นที่ น้ำจากหลายทิศไหลมาที่จุดนี้ ช่วงฝนตกหนักอาจมีน้ำขัง', ARRAY['ช่วงฝนตก ไปดูว่ามีน้ำขังที่จุดนี้หรือไม่', 'ถ้าน้ำขังบ่อย พิจารณาทำร่องระบายน้ำเริ่มจากจุดนี้ออกนอกแปลง', 'ช่วงหน้าฝนไม่ควรกองปุ๋ยหรือวัสดุไว้ตรงจุดนี้']::text[], 'rule_engine (ระบายน้ำ) + ข้อความจุดรวมน้ำในแอป', '', 'seed'),
    ('dry_spot.medium.*', '*', 'dry_spot', 'medium', 'จุดนี้แห้งกว่าส่วนอื่นของแปลง', 'ภาพดาวเทียมเรดาร์เห็นว่าดินตรงนี้แห้งกว่าจุดอื่นในแปลงเดียวกัน', ARRAY['ตรวจว่าน้ำจากระบบให้น้ำไปถึงจุดนี้หรือไม่ สายน้ำหยดอุดตันหรือไม่', 'เพิ่มการให้น้ำตรงจุดนี้', 'คลุมโคนต้นด้วยฟางหรือใบไม้ ช่วยลดการระเหยของน้ำ']::text[], 'rule_engine (แห้งเกิน) + gee_analysis._calc_swab advice (dry)', '', 'seed'),
    ('dry_spot.high.*', '*', 'dry_spot', 'high', 'จุดนี้แห้งกว่าส่วนอื่นของแปลงชัดเจน', 'ดินตรงนี้แห้งกว่าจุดอื่นในแปลงมาก', ARRAY['ให้น้ำตรงจุดนี้โดยเร็ว', 'ตรวจสายน้ำหยดหรือท่อว่ารั่วหรืออุดตัน', 'คลุมโคนต้นด้วยฟางหรือใบไม้']::text[], 'rule_engine (แล้งวิกฤต) + gee_analysis._calc_swab advice (drought)', 'การให้น้ำทางใบ (ยังไม่ได้ตรวจว่าเหมาะกับทุกพืช)', 'seed'),
    ('dry_spot.medium.group:vegetable', 'group:vegetable', 'dry_spot', 'medium', 'จุดนี้แห้งกว่าส่วนอื่นของแปลงผัก', 'ดินตรงนี้แห้งกว่าจุดอื่นในแปลงเดียวกัน', ARRAY['ตรวจว่ารดน้ำถึงแปลงผักตรงนี้ทั่วถึง', 'คลุมแปลงด้วยฟางหรือวัสดุคลุมดินเพื่อลดการระเหย']::text[], 'ใหม่ (ปรับจาก dry_spot ทั่วไปสำหรับแปลงผัก)', '', 'seed'),
    ('uneven_moisture.low.*', '*', 'uneven_moisture', 'low', 'ความชื้นในแปลงไม่เท่ากัน', 'บางส่วนของแปลงชื้น บางส่วนแห้ง ต่างกันมากกว่าแปลงทั่วไป', ARRAY['เดินดูแปลงตามสีบนแผนที่ เทียบส่วนที่ชื้นกับส่วนที่แห้ง', 'ถ้าระบบน้ำแยกโซนได้ ปรับการให้น้ำตามแต่ละส่วน']::text[], 'ใหม่ (ไม่มีข้อความเดิม)', '', 'seed'),
    ('wetter_than_usual.medium.*', '*', 'wetter_than_usual', 'medium', 'แปลงนี้ชื้นกว่าปกติของช่วงเดียวกันในปีก่อน ๆ', 'เทียบความชื้นตอนนี้กับความชื้นของแปลงนี้เองในช่วงเวลาเดียวกันของปีก่อน ๆ จากดาวเทียมเรดาร์', ARRAY['ตรวจร่องระบายน้ำรอบแปลงให้ไม่อุดตัน', 'ลดการให้น้ำลงช่วงนี้', 'สังเกตใบเหลืองหรือใบร่วงที่โคนต้น']::text[], 'rule_engine (เฝ้าระวังน้ำขัง/ชื้นเกิน)', '', 'seed'),
    ('wetter_than_usual.high.*', '*', 'wetter_than_usual', 'high', 'แปลงนี้ชื้นกว่าปกติมาก', 'ความชื้นตอนนี้สูงกว่าช่วงเดียวกันของปีก่อน ๆ ในแปลงนี้เองอย่างชัดเจน', ARRAY['ไปดูแปลงโดยเร็วว่ามีน้ำขังหรือไม่', 'ถ้ามีน้ำขัง ทำทางระบายน้ำออกและงดให้น้ำ', 'ถ้าต้นมีอาการผิดปกติ ติดต่อเจ้าหน้าที่เกษตร']::text[], 'rule_engine (เสี่ยงน้ำขังสูง / โรครากเน่า)', 'ความลึกร่อง ≥ 30 ซม., ยกแปลงปลูก, การฉีดสารป้องกันโรค', 'seed'),
    ('drier_than_usual.medium.*', '*', 'drier_than_usual', 'medium', 'แปลงนี้แห้งกว่าปกติของช่วงเดียวกันในปีก่อน ๆ', 'เทียบความชื้นตอนนี้กับความชื้นของแปลงนี้เองในช่วงเวลาเดียวกันของปีก่อน ๆ จากดาวเทียมเรดาร์', ARRAY['ตรวจระบบให้น้ำว่าทำงานครบทั้งแปลง', 'เพิ่มการให้น้ำช่วงนี้ถ้าดินแห้ง', 'คลุมโคนต้นเพื่อลดการระเหยของน้ำ']::text[], 'ใหม่ (สถานะ "แห้งกว่าปกติ" ที่เพิ่มใน 1d) + rule_engine (แห้งเกิน)', '', 'seed'),
    ('drier_than_usual.high.*', '*', 'drier_than_usual', 'high', 'แปลงนี้แห้งกว่าปกติมาก', 'ความชื้นตอนนี้ต่ำกว่าช่วงเดียวกันของปีก่อน ๆ ในแปลงนี้เองอย่างชัดเจน', ARRAY['ให้น้ำโดยเร็วถ้าดินแห้ง', 'ตรวจแหล่งน้ำและระบบให้น้ำว่าพอใช้ในช่วงนี้', 'ถ้าต้นเหี่ยวหรือใบร่วงมาก ติดต่อเจ้าหน้าที่เกษตร']::text[], 'ใหม่ (สถานะ "แห้งกว่าปกติมาก") + rule_engine (แล้งวิกฤต)', '', 'seed'),
    ('flow_through_rows.medium.*', '*', 'flow_through_rows', 'medium', 'ทางน้ำไหลผ่านแนวปลูก', 'ความลาดของพื้นที่ทำให้น้ำไหลผ่านแนวต้นไม้ อาจพาหน้าดินไปด้วย', ARRAY['ช่วงฝนตก ดูว่าน้ำไหลผ่านโคนต้นหรือพาดินไปหรือไม่', 'ทำร่องหรือคันดินเล็ก ๆ เบี่ยงทางน้ำออกจากแนวปลูก', 'ปลูกพืชคลุมดินตามแนวที่น้ำไหลผ่าน']::text[], 'rule_engine (ป้องกันหน้าดินพังทลาย)', '', 'seed'),
    ('bare_soil.medium.*', '*', 'bare_soil', 'medium', 'หน้าดินเปิดโล่งบางส่วน', 'ภาพดาวเทียมเห็นดินเปล่าที่ไม่มีพืชคลุมในบางส่วนของแปลง', ARRAY['คลุมหน้าดินด้วยเศษใบไม้หรือหญ้า', 'ปลูกพืชคลุมดิน']::text[], 'rule_engine (หน้าดินเปิดบางส่วน)', '', 'seed'),
    ('bare_soil.high.*', '*', 'bare_soil', 'high', 'หน้าดินเปิดโล่งมาก', 'ภาพดาวเทียมเห็นดินเปล่าที่ไม่มีพืชคลุมเป็นส่วนใหญ่ หน้าฝนหน้าดินอาจถูกน้ำชะไป', ARRAY['คลุมหน้าดินด้วยเศษใบไม้หรือหญ้า โดยเฉพาะก่อนหน้าฝน', 'ปลูกพืชคลุมดิน หรือทำร่องน้ำกันหน้าดินถูกชะ']::text[], 'rule_engine + flex_messages (หน้าดินเปิดโล่ง)', 'ชื่อพืชคลุมดินเฉพาะ (หญ้าแฝก) — รอผู้เชี่ยวชาญเลือกชนิดที่เหมาะกับพื้นที่', 'seed'),
    ('leaf_decline.medium.*', '*', 'leaf_decline', 'medium', 'ใบเขียวลดลงเทียบกับปีก่อน', 'ภาพดาวเทียมเห็นความเขียวของทรงพุ่มลดลงเมื่อเทียบกับช่วงเดียวกันของปีก่อน', ARRAY['เดินดูใบว่ามีแมลงหรือโรคหรือไม่', 'ตรวจว่าต้นได้น้ำพอหรือไม่', 'ปรึกษาเจ้าหน้าที่เกษตรเรื่องการบำรุงต้น']::text[], 'rule_engine (ใบโทรม)', 'การเพิ่มธาตุ N / Mg', 'seed'),
    ('leaf_decline.high.*', '*', 'leaf_decline', 'high', 'ใบเขียวลดลงมาก', 'ความเขียวของทรงพุ่มลดลงมากเมื่อเทียบกับช่วงเดียวกันของปีก่อน', ARRAY['ไปดูต้นโดยเร็ว ตรวจราก ใบ และลำต้น', 'ติดต่อเจ้าหน้าที่เกษตรหรือผู้เชี่ยวชาญ']::text[], 'rule_engine (โทรมหนัก / ตรวจโรครากเน่า)', 'การฉีดสารป้องกันโรค, ธาตุ Ca/K', 'seed')
ON CONFLICT (rule_key) DO NOTHING;

INSERT INTO recommendation_rule_history (rule_id, version, action, changed_by, snapshot)
SELECT r.id, r.version, 'seed', 'seed', to_jsonb(r)
FROM recommendation_rules r
WHERE r.updated_by = 'seed'
  AND NOT EXISTS (SELECT 1 FROM recommendation_rule_history h WHERE h.rule_id = r.id);

COMMIT;

-- ตรวจผล
SELECT status, count(*) AS rules FROM recommendation_rules GROUP BY status;
SELECT c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('recommendation_rules', 'recommendation_rule_history');
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
