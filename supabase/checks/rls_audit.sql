-- rls_audit.sql — ตรวจสถานะ RLS / policy / สิทธิ์ของ anon / bucket (อ่านอย่างเดียว ไม่แก้อะไร)
-- วิธีใช้: Supabase Dashboard → SQL Editor → วางทั้งไฟล์ → Run แล้วส่งผลทั้ง 4 ตารางกลับมา
--
-- สิ่งที่คาดหวัง:
--   1) ทุกตารางใน public มี rls_enabled = true
--      (จากไฟล์ใน repo: scheduler_runs ที่สร้างใน migrate_v10 "ไม่ได้เปิด RLS" → แก้ใน migrate_v12)
--   2) ทุกตารางมี policy "backend only" (USING false) — anon/authenticated อ่านเขียนไม่ได้
--   3) ไม่มี view ที่ anon อ่านได้ (dashboard_summary เป็น view: view ใน Postgres
--      รันด้วยสิทธิ์เจ้าของ view จึงข้าม RLS ของตารางต้นทาง ถ้า anon มีสิทธิ์ SELECT
--      บน view จะอ่านสถิติรวมได้)
--   4) bucket plot-images เป็น public (ตั้งใจ — LINE ต้องดึงรูปได้) แต่ชื่อไฟล์ต้องเดาไม่ได้

-- 1) RLS ต่อตาราง
SELECT c.relname                AS table_name,
       c.relrowsecurity         AS rls_enabled,
       c.relforcerowsecurity    AS rls_forced
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relrowsecurity, c.relname;

-- 2) policy ที่มีอยู่
SELECT tablename, policyname, roles, cmd, qual
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

-- 3) สิทธิ์ของ role ที่ client ใช้ได้ (anon / authenticated) บนตารางและ view ใน public
SELECT table_name, grantee, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- 4) bucket ของ Storage
SELECT id, name, public, file_size_limit, allowed_mime_types
FROM storage.buckets
ORDER BY name;
