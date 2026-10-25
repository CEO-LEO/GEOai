-- migrate_v16.sql — Phase 2a (2026-10): บทบาทผู้ใช้ + พื้นที่รับผิดชอบ + รหัสเชิญ + audit log + สถิติการใช้งาน
-- ก่อนรันบน production: backup ทุกตาราง (db_tool.py backup) — rollback: supabase/rollback_v16.sql
--
-- เพิ่มอย่างเดียว:
--   users.role                  farmer (ค่าเริ่มต้น) / officer / admin
--   users.share_with_officers   เจ้าของแปลงยินยอมให้เจ้าหน้าที่ในพื้นที่เห็นแปลง (ตั้งในหน้ายินยอม 2b) — ค่าเริ่มต้น FALSE
--   plots.area_code/area_name   อำเภอของแปลง (config/admin_areas.json) · plots.created_by · plots.owner_label (ชื่อเกษตรกรถ้าเจ้าหน้าที่วาดแทน)
--   user_areas                  อำเภอที่เจ้าหน้าที่รับผิดชอบ
--   invites + invite_redemptions รหัสเชิญเจ้าหน้าที่ (ไม่ต้องสมัครเอง)
--   audit_logs                  ใครเข้าดู/แก้ข้อมูลแปลงของใคร, เปลี่ยนบทบาท, ใช้รหัสเชิญ
--   events                      สถิติการใช้งานสำหรับรายงาน B2G

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'farmer';
ALTER TABLE users ADD COLUMN IF NOT EXISTS share_with_officers BOOLEAN NOT NULL DEFAULT FALSE;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_role_check') THEN
        ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('farmer', 'officer', 'admin'));
    END IF;
END $$;

ALTER TABLE plots ADD COLUMN IF NOT EXISTS area_code TEXT;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS area_name TEXT;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS created_by TEXT;
ALTER TABLE plots ADD COLUMN IF NOT EXISTS owner_label TEXT;
CREATE INDEX IF NOT EXISTS idx_plots_area_code ON plots (area_code);

CREATE TABLE IF NOT EXISTS user_areas (
    user_id    TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    area_code  TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, area_code)
);

CREATE TABLE IF NOT EXISTS invites (
    code        TEXT        PRIMARY KEY,
    role        TEXT        NOT NULL DEFAULT 'officer' CHECK (role IN ('officer', 'admin')),
    area_codes  TEXT[]      NOT NULL DEFAULT '{}',
    note        TEXT        NOT NULL DEFAULT '',
    created_by  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    max_uses    INTEGER     NOT NULL DEFAULT 1 CHECK (max_uses BETWEEN 1 AND 100),
    used_count  INTEGER     NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invite_redemptions (
    id          BIGSERIAL   PRIMARY KEY,
    code        TEXT        NOT NULL REFERENCES invites(code) ON DELETE CASCADE,
    user_id     TEXT        NOT NULL,
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (code, user_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id            BIGSERIAL   PRIMARY KEY,
    actor_user_id TEXT,                 -- NULL = ผู้ดูแลผ่าน X-Admin-Key
    actor_role    TEXT        NOT NULL,
    action        TEXT        NOT NULL, -- view_plot / edit_plot / analyze_plot / set_role / redeem_invite / ...
    target_type   TEXT,
    target_id     TEXT,
    target_owner  TEXT,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target_owner ON audit_logs (target_owner, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    TEXT,
    name       TEXT        NOT NULL,    -- app_open / plot_create / analyze / view_recommendation / share_report / photo_upload / ...
    plot_id    BIGINT,
    props      JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_name_created ON events (name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_created ON events (user_id, created_at DESC);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['user_areas', 'invites', 'invite_redemptions', 'audit_logs', 'events'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backend only' AND tablename = t) THEN
            EXECUTE format('CREATE POLICY "backend only" ON %I FOR ALL USING (FALSE)', t);
        END IF;
    END LOOP;
END $$;
REVOKE ALL ON user_areas, invites, invite_redemptions, audit_logs, events FROM anon, authenticated;

COMMIT;

-- ตรวจผล
SELECT role, count(*) AS users FROM users GROUP BY role;
SELECT c.relname, c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('user_areas', 'invites', 'invite_redemptions', 'audit_logs', 'events');
SELECT count(*) AS anon_or_authenticated_grants_left
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
