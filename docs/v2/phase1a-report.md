# IAM ROOT v2 — รายงาน Phase 1a (ปิดช่องโหว่)

วันที่: 2026-10-16 · branch `feat/v2-phase1a-security` (ยังไม่ push / ยังไม่ merge — merge เข้า `master` = Render deploy ทันที)
สถานะ: โค้ดเสร็จ test ผ่าน **125/125** · **รอผู้ใช้ยืนยันว่าเปลี่ยน ADMIN_API_KEY แล้ว ก่อน deploy**

---

## 🚨 พบระหว่างทำ: ข้อความ LINE ส่งไม่ออกมาตั้งแต่ 8 ต.ค.

ตรวจผ่าน LINE Messaging API (อ่านอย่างเดียว) เมื่อ 2026-10-16:

| รายการ | ค่า |
|---|---|
| โควตาเดือนนี้ (`/v2/bot/message/quota`) | `limited` **300 ข้อความ** = แพลน Free |
| ใช้ไปแล้ว (`/quota/consumption`) | **300 / 300 — หมดแล้ว** |
| push สำเร็จต่อวัน 1–7 ต.ค. | 47, 47, 43, 43, 43, 41, 21 |
| push สำเร็จต่อวัน 8–15 ต.ค. | **0 ทุกวัน** |
| ผู้ติดตาม OA | 16 คน (ใน DB มีผู้ใช้ 15 คน, 23 แปลง) |

**ผลกระทบตอนนี้:** การแจ้งเตือนตอนเช้า, สรุปประจำวัน, แจ้งเตือนฝน และการ์ดผลที่ควรเด้งเข้าแชทหลังกด "ตรวจสอบแปลงนี้"
**ไม่ถึงผู้ใช้เลย** จนกว่าจะขึ้นเดือนใหม่ (ซึ่งจะหมดอีกในราววันที่ 7) — ผลในหน้า LIFF ยังแสดงปกติ ส่วนที่ตอบกลับในแชท (reply) ยังใช้ได้เพราะไม่นับโควตา

ช่วง 16 ก.ย.–7 ต.ค. ส่งได้วันละ 47 ข้อความต่อเนื่อง (รวมเกิน 300) แสดงว่าช่วงนั้นโควตาสูงกว่านี้ — **เดือนนี้แพลนถูกเปลี่ยนกลับเป็น Free หรือเปล่า?** (ดูคำถามข้อ 3)

**ทำไมใช้เยอะ:** จากข้อมูล 30 วันล่าสุด **82% ของผลวิเคราะห์ (647/793) ถูกจัดเป็น "เสี่ยงสูง"** → ระบบส่งคำเตือนเกือบทุกแปลงทุกวัน
(ผู้ใช้ทุกคนเปิดแจ้งเตือนความเสี่ยงไว้) บวกสรุปประจำวันของ 8 คนที่เปิดไว้ — นอกจากเปลืองโควตา ยังทำให้ผู้ใช้ชินกับคำเตือนจนไม่สนใจ
เกณฑ์ `compute_risk_level` น่าจะไวเกินไป (เสนอให้ทบทวนใน Phase 1c/1d พร้อมเกณฑ์ตามชนิดพืช)

---

## 1. สิ่งที่ทำ (9 commits)

| commit | เรื่อง |
|---|---|
| `8e00992` | เอกสาร Phase 0 + CLAUDE.md |
| `5fd0fa9` | เอาค่า `ADMIN_API_KEY`/`SPHERE_KEY` ออกจาก `render.yaml` เหลือแค่ชื่อ env (`sync: false`) |
| `af3d20d` | **ตรวจ LINE token ฝั่ง server** ทุก endpoint ผู้ใช้ + ตรวจเจ้าของแปลง + admin/webhook fail-closed + rate limit ใหม่ + ปิด CORS |
| `feabaf8` | LIFF แนบ token ทุกคำขอ, ลบ `code`/`state` จาก URL, ตัดบัญชีร่วม `test-user` |
| `aaa7ab7` | test ไม่พึ่ง `.env` ในเครื่อง, เพิ่ม test สิทธิ์, CI รันบน `master` |
| `32f2523` | ผู้ใช้ใหม่ปิดสรุปประจำวันเป็นค่าเริ่มต้น (ผู้ใช้เดิมไม่ถูกแตะ) |
| `f21eafc` | path รูปย่อแปลงใน bucket public ต้องเดาไม่ได้ (เดิมไล่ `thumbnails/1.png, 2.png…` ได้) |
| `c12ec90` | SQL ตรวจ RLS, `migrate_v12` + rollback, สคริปต์ backup — **ยังไม่ได้รันบน production** |
| `7612c67` | ลบคำกล่าวอ้าง "83%" ฯลฯ ใน `STATUS.md`, `BACKTEST_RESULTS.md` |

### 1.1 การยืนยันตัวตน ([backend/auth.py](../../backend/auth.py))
- client ส่ง `Authorization: Bearer <token>` + `X-Line-Token-Type: id_token|access_token`
- **ID token** ตรวจกับ `POST https://api.line.me/oauth2/v2.1/verify` (client_id = channel ID ของ LIFF)
- **access token เป็นทางสำรอง**: ใช้เมื่อ LIFF ยังไม่เปิด scope `openid` (`getIDToken()` คืน null) หรือ ID token หมดอายุเพราะเปิดแอปค้างไว้นาน — ตรวจว่าออกให้ channel เราจริงแล้วดึง userId จาก `/v2/profile`
- channel ID มาจากส่วนหน้าของ LIFF ID (`2010580115-…` → `2010580115`) หรือตั้งเองด้วย `LINE_LOGIN_CHANNEL_ID` → **ไม่ต้องเพิ่ม env ใหม่บน Render**
- cache ผลตรวจในแรม ≤ 10 นาที (key = sha256 ของ token ไม่เก็บ token ดิบ)
- dev ในเครื่อง: `IAMROOT_DEV_AUTH_USER=dev-user` (ปิดตัวเองเสมอถ้ามี env `RENDER`)

### 1.2 สิทธิ์ต่อ endpoint
| endpoint | ก่อน | หลัง |
|---|---|---|
| `GET /plots/{user_id}` | ใครก็ได้ | token + user_id ต้องเป็นเจ้าของ token |
| `GET …/history`, `…/persistent-wet` | ไม่เช็กเจ้าของเลย | token + แปลงต้องเป็นของผู้ใช้ (ไม่ใช่ → 404) |
| `GET …/ndvi-timeseries`, `DELETE /plots/…`, `PATCH /user/…/notify*` | เชื่อ user_id ใน URL | token + ต้องเป็นตัวเอง |
| `POST /analyze` | push หา user_id อะไรก็ได้ใน body | ใช้ผู้ใช้จาก token เท่านั้น push หาตัวเองเท่านั้น |
| `POST /analyze/grid` | ไม่มี auth | token |
| `POST /iot/reading` (push LINE ได้) | ไม่มี auth | **admin key** |
| `GET /analyze/preview`, `/weather-alert/preview` | ไม่มี auth | **admin key** |
| `/admin/*` | ถ้าไม่ตั้ง key = ปล่อยผ่าน | ไม่ตั้ง key = 503 (fail-closed) |
| `POST /webhook` | ไม่ตั้ง secret = ข้ามการตรวจลายเซ็น | ไม่ตั้ง secret = ปฏิเสธ |

**การตีความข้อ "endpoint ที่สั่ง push ต้องเป็น admin":** endpoint ที่ส่งไปหา "คนอื่น" ได้ (`/iot/reading`, `/admin/trigger/*`, `/admin/richmenu/setup`) เป็น admin ทั้งหมด
ส่วน `/analyze` ยังให้ผู้ใช้ทั่วไปเรียกได้ เพราะเป็นฟีเจอร์หลัก (เกษตรกรกดตรวจแปลงแล้วได้การ์ดในแชท) แต่ส่งหา **เจ้าของ token เท่านั้น** ถ้าต้องการให้เป็น admin จริง ๆ แจ้งได้ แต่ฟีเจอร์ตรวจแปลงของเกษตรกรจะพัง

### 1.3 Rate limit ([backend/middleware.py](../../backend/middleware.py))
- ไม่นับไฟล์ static (`/liff`, `/dashboard`), `/health`, `/webhook`
- เพดานต่อ IP เฉพาะ API: **600 คำขอ/นาที** (ปรับได้ด้วย `RATE_LIMIT_IP_PER_MIN`) — เผื่ออบรม 50 คน × ~12 คำขอ/นาที ใช้ IP จาก `CF-Connecting-IP` (Cloudflare หน้า Render) ซึ่ง client ปลอมไม่ได้
- ต่อ LINE userId หลังยืนยันตัวตน: ทั่วไป 60/นาที, `/analyze` 3/นาที (เท่าเดิม), `/analyze/grid` 10/นาที
- มี test จำลอง **50 คนใน Wi-Fi เดียวกันคนละ 10 คำขอ ไม่มีใครโดน 429** และคนข้าง ๆ ไม่โดนลูกหลงเมื่อมีคนกด analyze ถี่
- ข้อจำกัด: เก็บในแรม ใช้ได้กับ Render instance เดียว (ตอนนี้) ถ้าขยายหลาย instance ต้องย้ายไป Redis

### 1.4 LIFF ([liff/index.html](../../liff/index.html))
- `apiFetch()` แนบ token ทุกคำขอ ถ้าได้ 401 ตอนใช้ ID token จะลองใหม่ด้วย access token อัตโนมัติ
- `history.replaceState` ลบ `code`, `state`, `liffClientId`, `liffRedirectUri` หลัง `liff.init()` (คง `?tab=` ไว้สำหรับ Rich Menu)
- เปิดไม่สำเร็จ / ยืนยันตัวตนไม่ได้ → ข้อความ "กรุณาปิดแล้วเปิดใหม่จากแชท LINE" แทนการใช้บัญชีร่วม

### 1.5 Test และ CI
- [backend/conftest.py](../../backend/conftest.py) ตั้ง env ปลอมทับทุกครั้ง + ปิด `load_dotenv` → รัน `pytest` ได้เลยไม่ต้องใส่ env และไม่แตะ `.env` จริง
- [backend/test_auth.py](../../backend/test_auth.py) 45 test ใหม่ · ทั้งหมด **125 passed** (Python 3.13 ในเครื่อง, dependency ไม่ pin)
- [backend/pytest.ini](../../backend/pytest.ini) เก็บเฉพาะไฟล์ test จริง (`test_local.py` ยิง GEE จริง, `test_weather_alert.py` เป็นสคริปต์ที่พังอยู่ก่อนแล้วตั้งแต่ master)
- CI ([.github/workflows/deploy.yml](../../.github/workflows/deploy.yml)) รันบน `master` และ `feat/**` ด้วย Python 3.11 + requirements ที่ pin ไว้ — **ยังไม่เคยรันจริง จะเห็นผลครั้งแรกเมื่อ push**

---

## 2. ผลสแกน secret ใน git history (ไม่ได้ rewrite history)

เครื่องมือ: gitleaks 8.21.2 (`--log-opts=--all`, 106 commits) + regex ที่เขียนเพิ่ม เพราะ gitleaks **ไม่จับ** ค่าใน YAML รูปแบบ `value:`

| ที่พบ | commit | ความรุนแรง | ต้องทำ |
|---|---|---|---|
| `ADMIN_API_KEY` ตัวจริงใน `render.yaml` | `d319f68` (2026-06-28) | **วิกฤต** | เปลี่ยน key (ผู้ใช้กำลังทำ) |
| `SPHERE_KEY` ใน `render.yaml` | `8345da3` (2026-07-01) | ต่ำ (หน้าเว็บส่ง key นี้ให้ทุกคนอยู่แล้ว) | จำกัด referer ในหน้า GISTDA Sphere หรือออก key ใหม่ |
| LINE userId จริง 1 ราย ใน `main.py` (`/admin/seed-nayaiam`) | `0220afa` | ข้อมูลส่วนบุคคลใน repo public | เสนอให้ลบ endpoint seed นี้ (คำถามข้อ 6) |
| LIFF ID, เบอร์โทร rich menu | หลาย commit | ตั้งใจให้เป็นสาธารณะ | — |
| service account / token อื่นใน history | — | **ไม่พบ** (มีแต่ค่าตัวอย่าง) | — |

**ไฟล์ในเครื่องที่ไม่อยู่ใน git แต่มี secret** (gitleaks โหมด dir):
- `backend/seed_nayaiam.py` (**ยังไม่ track**) — hardcode `SUPABASE_KEY` ไว้ในโค้ด → **ห้าม commit** ถ้าเคยส่งไฟล์นี้ให้ใครควรเปลี่ยน service-role key
- `.claude/settings.local.json` (อยู่ใน `.gitignore` แล้ว) — มี Vercel token และ admin key อยู่ในบรรทัดคำสั่งที่เคยอนุญาต → ควรลบบรรทัดเหล่านั้น และยกเลิก Vercel token ถ้าไม่ใช้แล้ว
- `backend/.env` — ปกติ อยู่ใน `.gitignore`

---

## 3. Supabase: RLS และ service_role

- **service_role ไม่ถูกใช้ฝั่ง client:** ตรวจแล้ว `liff/`, `dashboard/` ไม่มีการเรียก Supabase ตรง ๆ และ `/liff/config.js` ส่งออกแค่ `API_URL`, `LIFF_ID`, `SPHERE_KEY`
- **RLS ตามไฟล์ใน repo:** ทุกตารางเปิด RLS + policy "backend only" **ยกเว้น `scheduler_runs`** (สร้างใน `migrate_v10` โดยไม่เปิด) → ใครมี anon key อ่าน/เขียนตารางนี้ได้ (ข้อมูลไม่อ่อนไหว แต่แก้เพื่อปิดช่องทาง) แก้ใน `migrate_v12`
- view `dashboard_summary` อาจอ่านได้ด้วย anon ถ้ามีสิทธิ์ SELECT (view ข้าม RLS) → ตรวจใน audit
- bucket `plot-images` เป็น public (ตั้งใจ — LINE ต้องดึงรูปได้) แผนที่ใช้ชื่อสุ่มอยู่แล้ว ส่วนรูปย่อแก้ให้เดาไม่ได้แล้ว แต่**ไฟล์ชื่อเดิม `thumbnails/<id>.png` ยังค้างอยู่ใน bucket** ต้องลบ (คำถามข้อ 5)
- **ตรวจของจริงบน production ไม่ได้จากเครื่องนี้** (ไม่มี anon key, ไม่มี `psql`) → ให้รัน [supabase/checks/rls_audit.sql](../../supabase/checks/rls_audit.sql) (อ่านอย่างเดียว) แล้วส่งผลกลับมา

**migration v12** ([supabase/migrate_v12.sql](../../supabase/migrate_v12.sql) / [rollback_v12.sql](../../supabase/rollback_v12.sql)) — เพิ่มอย่างเดียว ไม่ลบ/ไม่เปลี่ยนชนิดคอลัมน์:
1. เปิด RLS + policy ให้ `scheduler_runs`
2. `users.notify_daily_digest` DEFAULT → `FALSE` (โค้ดใส่ FALSE ให้ผู้ใช้ใหม่อยู่แล้ว ข้อนี้ทำให้ DB ตรงกัน)

ขั้นตอนรัน (รออนุมัติ): `SUPABASE_DB_URL=... bash scripts/backup_db.sh` → รัน `migrate_v12.sql` ใน SQL Editor → ตรวจผลท้ายไฟล์

---

## 4. ประมาณการข้อความ LINE OA ต่อเดือน

**วิธีนับ** (สรุปจากโค้ด `scheduler.py`, `webhook.py`, `line_sender.py` + สถิติจริง): 1 push ไปหา 1 คน = 1 ข้อความ (การ์ด + รูปส่งรวมใน push เดียว)
reply ในแชทไม่นับ — สถิติจริงสอดคล้อง: 1–7 ต.ค. push สำเร็จรวม 285 ใกล้เคียงยอดใช้โควตา 300 (ส่วนต่างน่าจะมาจากการตัดวันของสถิติ ซึ่งยังไม่ได้ยืนยัน)

| แหล่ง | ต่อผู้ใช้ต่อวัน (ค่าที่ใช้คำนวณ) | ที่มา |
|---|---|---|
| แจ้งเตือนความเสี่ยง (เปิดเป็นค่าเริ่มต้น) | แปลง/คน 1.5 × สัดส่วนเสี่ยงสูง 0.82 = **1.23** | DB: 23 แปลง/15 คน, 647/793 ผลเป็น high |
| สรุปประจำวัน (เฉพาะคนที่เปิด) | 1 ข้อความนำ + 1 ต่อแปลง = **2.5** | `daily_scan_job` |
| แจ้งเตือนฝน (หน้าฝน) | **~0.5** | ส่วนต่างจากสถิติจริงวันละ 47 |
| กดตรวจแปลงเอง / ปุ่มตรวจสอบ | **~0.1** | ประมาณ |

ตรวจสอบกับของจริง: 15 คน × 1.23 + 8 คน × 2.5 + 15 × 0.6 ≈ 47/วัน ✔ ตรงกับสถิติ

| สถานการณ์ | ต่อคน/เดือน | 100 คน | 500 คน | 1,000 คน |
|---|---|---|---|---|
| **A** พฤติกรรมเดิม (สรุปประจำวันเปิดทุกคน) | ~129 | 12,900 | 64,500 | 129,000 |
| **B** หลัง Phase 1a (สรุปประจำวันปิดเป็นค่าเริ่มต้น สมมติ 10% เปิดเอง) | ~63 | 6,300 | 31,500 | 63,000 |
| **C** เป้าหมาย: B + แจ้งเตือนเฉพาะเมื่อความเสี่ยง "เพิ่มขึ้น" (ไม่ซ้ำทุกวัน ~1 ครั้ง/แปลง/สัปดาห์) | ~26 | 2,600 | 13,000 | 26,000 |

**ค่าใช้จ่ายรายเดือน (แพลนที่ถูกที่สุด, ยังไม่รวม VAT 7%)** — ราคาจาก [หน้าทางการของ LINE for Business TH](https://lineforbusiness.com/th/service/line-oa-features/broadcast-message):
Free ฿0 / 300 ข้อความ (ซื้อเพิ่มไม่ได้) · Basic ฿1,280 / 15,000 (+฿0.10/ข้อความ) · Pro ฿1,780 / 35,000 (+฿0.06/ข้อความ)

| สถานการณ์ | 100 คน | 500 คน | 1,000 คน |
|---|---|---|---|
| A | Basic ฿1,280 | Pro ฿3,550 | Pro ฿7,420 |
| B | Basic ฿1,280 | Pro ฿1,780 | Pro ฿3,460 |
| C | Basic ฿1,280 | Basic ฿1,280 | Pro ฿1,780 |

**ข้อสรุป:**
- **แพลน Free (300) ไม่พอแม้แต่ผู้ใช้ 15 คนตอนนี้** (ใช้จริง ~1,400/เดือน) — ต้องขึ้น Basic ก่อนเริ่มชวนเครือข่าย
- การลดคำเตือนซ้ำทุกวัน (สถานการณ์ C) ประหยัดได้มากที่สุดและดีต่อผู้ใช้ด้วย — เสนอทำใน Phase 3 (กันส่งซ้ำ) และทบทวนเกณฑ์ความเสี่ยงใน 1c/1d
- ผู้ใช้เดิม 8 คนยังเปิดสรุปประจำวันอยู่ (ไม่ได้แตะตามที่ตกลง)

---

## 5. พฤติกรรมที่เปลี่ยน (แจ้งตามกติกา)

1. เปิด LIFF นอกแอป LINE แล้ว init ไม่สำเร็จ → เห็นข้อความให้เปิดใหม่จาก LINE (เดิมเข้าเป็นบัญชีร่วม `test-user`)
2. ใครที่เปิดหน้า LIFF รุ่นเก่าค้างไว้ตอน deploy จะเรียก API ไม่ได้ (401) จนกว่าจะปิดเปิดใหม่
3. `/analyze/preview`, `/weather-alert/preview`, `/iot/reading` ต้องใช้ admin key
4. ผู้ใช้ใหม่ไม่ได้รับสรุปประจำวันจนกว่าจะเปิดเองในเมนูตั้งค่า
5. ถ้า env `ADMIN_API_KEY` หรือ `LINE_CHANNEL_SECRET` หายบน Render → admin/webhook ปฏิเสธทั้งหมด (เดิมปล่อยผ่าน)
6. CORS ปิด (LIFF/dashboard เป็น same-origin ไม่กระทบ) — ถ้าย้าย frontend ไปโดเมนอื่นต้องตั้ง `CORS_ALLOW_ORIGINS`

---

## 6. ขั้นตอน deploy (รอผู้ใช้ยืนยัน)

1. [ผู้ใช้] ตั้ง `ADMIN_API_KEY` ใหม่ใน Render **และ** GitHub Secret (workflow ตั้งเวลาใช้ key นี้) → แจ้งผม
2. [ผู้ใช้] ใส่ key ใหม่ใน dashboard (ช่องกรอก admin key) และใน `backend/.env` เครื่องตัวเอง
3. [ผม] push branch → ดูผล CI ครั้งแรก
4. [ผู้ใช้อนุมัติ] merge เข้า `master` → Render deploy อัตโนมัติ
5. [ทดสอบ] checklist ด้านล่าง → ถ้าพัง rollback ด้วย Render "Rollback to previous deploy" หรือ `git revert` merge commit

## 7. วิธีทดสอบ

**ในเครื่อง**
```bash
cd backend && pytest -q                         # 125 passed — ไม่ต้องตั้ง env
IAMROOT_DEV_AUTH_USER=dev-user uvicorn main:app --reload --port 8000   # เปิด http://localhost:8000/liff/
```

**ใน LINE จริง (iOS และ Android)**
- [ ] เปิดจาก Rich Menu "วาดแปลง" → เห็นแผนที่, URL ไม่มี `code=`/`state=` (กดแชร์ลิงก์ดูได้)
- [ ] "แปลงของฉัน" แสดงแปลงของตัวเองครบ
- [ ] กดตรวจแปลง → ผลขึ้นในแอป (การ์ดในแชทจะ **ยังไม่มา** จนกว่าโควตา OA จะพอ — ดูหัวข้อด่วนบนสุด)
- [ ] "จุดน้ำขังในแปลง", "ประวัติการวิเคราะห์", ลบแปลงทดสอบ ทำงาน
- [ ] เปิดแอปค้างไว้ > 1 ชม. แล้วกดดูประวัติ → ยังใช้ได้ (สลับไปใช้ access token เอง)
- [ ] Rich Menu "ตั้งค่า"/"ตรวจสอบ" ในแชทยังตอบได้ (webhook)
- [ ] dashboard ใส่ admin key ใหม่แล้วโหลดได้, key เก่าใช้ไม่ได้ (403)
- [ ] เปิด `https://iamroot.onrender.com/plots/<userId ของตัวเอง>` ใน browser ตรง ๆ → ต้องได้ 401

---

## 8. สิ่งที่ค้าง / ความเสี่ยง / คำถาม

1. **ยืนยันการเปลี่ยน key** (ข้อ 6.1) — deploy รอข้อนี้
2. **scope `openid` ของ LIFF:** ถ้ายังไม่เปิด ระบบจะใช้ access token แทน (ใช้งานได้) แต่ spec ต้องการ ID token — ต้องเปิดในหน้า LIFF ของ LINE Developers (เป็นการเปลี่ยนค่าใน LINE Developers จึงต้องให้คุณตัดสินใจ)
3. **LINE OA:** เดือนนี้แพลนเป็น Free 300 และหมดแล้ว — ตั้งใจหรือไม่? จะขึ้น Basic (฿1,280 + VAT) เมื่อไร?
   ระหว่างนี้ต้องการให้ปิดสรุปประจำวันของผู้ใช้เดิม 8 คนชั่วคราวเพื่อเหลือโควตาให้คำเตือนสำคัญไหม (เปลี่ยนพฤติกรรมเดิม — ไม่ทำถ้าไม่สั่ง)
4. **รัน `rls_audit.sql`** แล้วส่งผล · อนุมัติ backup + `migrate_v12`
5. **ลบรูปย่อชื่อเดิม** `thumbnails/<id>.png` ใน bucket หลัง deploy (จะเขียนสคริปต์ให้ ต้องอนุมัติก่อนลบ)
6. **ลบ endpoint `/admin/seed-nayaiam`** (hardcode LINE userId จริง + ใส่ข้อมูลจำลองลง production ได้) ได้หรือไม่
7. **เอกสารนำเสนอที่ยังมีตัวเลขไม่ได้ทดสอบ** (ไม่ได้แก้ เพราะเป็นไฟล์ที่ไม่อยู่ใน git และเป็นเนื้อหาการนำเสนอ):
   - pptx ทุกเวอร์ชัน: "87% Net Accuracy", "R² = 0.83", "แจ้งเตือนล่วงหน้าเฉลี่ย 7.3 วัน", "ประหยัด 150,000–237,500 บาท/ปี"
   - สคริปต์ docx ทุกเวอร์ชัน: "ความแม่นยำ 87%", "False Positive …", "ลดความสูญเสีย 65%", "ข้อมูลจากสวนทุเรียนจริง… ไม่ใช่ตัวเลขจากห้องแล็บ"
   - `IAMROOT_Infographic_A4.html`: "R²=0.83 Accuracy", "Net Accuracy", "Confidence: 87%"
   - `GEOai_Technical_Document.md`, `GEOai_Mechanism_Flow.md`: R² ~0.50 / MAE บนข้อมูล **จำลอง**
   → **ห้ามใช้ในการคุย B2G** ต้องการให้ผมแก้ไฟล์เหล่านี้ (เสนอข้อความทดแทน) หรือคุณแก้เอง?
   - dashboard ยังโชว์ "ความแม่นยำโมเดลพยากรณ์ผลผลิต %" → จะซ่อนพร้อม feature flag ปุ๋ย/ผลผลิตใน Phase 1c
8. **ความเสี่ยงการ scale ที่พบเพิ่ม (แก้ใน 1b):** `daily_scan_job`/`rain_alert_job` หารายชื่อผู้ใช้จาก "รายงานล่าสุด 1,000 แถว" — เมื่อมีแปลงเกินราว 1,000 แปลง ผู้ใช้บางคนจะหลุดจากการสแกนถาวรโดยไม่มี error และสแกนวิเคราะห์ทุกแปลงทุกวันแม้ผู้ใช้ปิดการแจ้งเตือน (เปลือง GEE)
9. CI ยังไม่เคยรันบน GitHub (จะเห็นเมื่อ push) · local test ใช้ Python 3.13 กับ dependency รุ่นใหม่กว่าที่ pin
