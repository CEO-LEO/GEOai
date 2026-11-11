# IAM ROOT — เอกสารส่งมอบงาน (ฉบับ 11 พ.ย. 2569)

สำหรับทีมที่รับงานต่อ · อ่านไฟล์นี้ก่อน แล้วตามด้วย [CLAUDE.md](../../CLAUDE.md) (กติกาและโครงสร้างโค้ด) และ [decisions.md](decisions.md) (บันทึกการตัดสินใจทุกข้อ)
เอกสารนี้ไม่มีรหัสผ่านหรือ key ใด ๆ — ขอสิทธิ์เข้าระบบจากเจ้าของโครงการโดยตรง (ข้อ 6)

## 1. ระบบนี้คืออะไร

แอปใน LINE (LIFF) สำหรับชาวสวนทุเรียน: ผู้ใช้วาดขอบเขตแปลง → ระบบใช้ภาพดาวเทียมชี้ **จุดที่ชื้นกว่าส่วนอื่น / จุดที่น้ำไหลมารวม** (เสี่ยงน้ำขัง)
แสดงเป็นแผนที่จุดสี พร้อมคำแนะนำ และเตือนฝนหนักทาง LINE · กำลังทดลองกับเครือข่ายเกษตรจังหวัดราชบุรี และเตรียมขอทุน TED Fund

- ใช้งานจริง: https://iamroot.onrender.com (แอป `/liff/` · หน้าผู้ดูแล `/dashboard/`)
- โค้ด: https://github.com/CEO-LEO/GEOai (branch `master` = ของจริงบน production)

## 2. สถานะวันนี้

| เรื่อง | สถานะ |
|---|---|
| ระบบ production | ใช้งานได้ปกติ (ตรวจ 11 พ.ย.) · เคยถูก Render ระงับเพราะค้างชำระ 29 ต.ค.–1 พ.ย. แก้แล้ว |
| งานพัฒนาตาม spec v2 | Phase 1–3 เสร็จและใช้งานจริง · Phase 4a (ทายชนิดพืช) มีโครงพร้อม แต่**ปิดอยู่**เพราะยังไม่มีข้อมูลฝึก |
| ข้อมูลภาคสนาม | **ยังเป็น 0** — คำตอบเกษตรกรอาสา 0 · label ชนิดพืช 0 (เกณฑ์เทรน ≥ 50 แปลง) |
| ตัวเลขความแม่นยำ | **ยังไม่มี** — ห้ามอ้างตัวเลขใด ๆ จนกว่าหน้า `/dashboard/validation.html` จะคำนวณจากข้อมูลจริง |
| LINE | แพลน Free 300 ข้อความ/เดือน ใช้ไป 12 (รีเซ็ต 1 ธ.ค.) |
| Earth Engine | เรียกผ่านโปรเจกต์ `iamroot-geoai` ตั้งแต่ 11 พ.ย. · ตัววัดการใช้ (EECU) เปิดอยู่ เก็บได้ 6 วัน ≈ 27 EECU-วินาที/แปลง/วัน |
| ชุดทดสอบ | ~450 tests · CI รันบน `master` และ `feat/**` |

## 3. สิ่งที่สร้างเสร็จแล้ว (ย่อ)

- **Phase 1:** ความปลอดภัย (เปลี่ยน key, RLS), แผนที่จุด + ประโยคสรุปแปลง, ชนิดพืช/ประเภทสวน (ยกร่อง), คำเตือนขอบเขตแปลง, คำแนะนำจากตารางที่คนตรวจ, รายงาน PDF/แชร์ LINE → [final-report.md](final-report.md)
- **Phase 2:** บทบาท เกษตรกร/เจ้าหน้าที่/ผู้ดูแล + รหัสเชิญ, PDPA (ยินยอม ดู/ดาวน์โหลด/ขอลบ), รูปถ่ายในแปลง, ยืนยันจุด จริง/ไม่จริง, แดชบอร์ดจังหวัด → [phase2-report.md](phase2-report.md)
- **Phase 3:** เตือนฝนหนักแบบบอกจุดเสี่ยง, เทียบหน้าฝน/หน้าแล้ง, ปักหมุดต้นไม้รายต้น → [phase3-report.md](phase3-report.md)
- **Phase 4a:** โครงจำแนกชนิดพืช + เมนูเจ้าหน้าที่สำรวจพืช → [phase4a-report.md](phase4a-report.md)
- **รอบทุเรียน/ทุน (5 พ.ย.):** พืชเริ่มต้น = ทุเรียน, ระบบเกษตรกรอาสา + สุ่มจุดให้ตรวจ (วัด recall), ตัววัดค่าใช้จ่าย Earth Engine, ตรวจข้อความในเอกสารทุน → [grant-claims-review.md](grant-claims-review.md), [success-criteria-draft.md](success-criteria-draft.md)

## 4. งานค้าง — เรียงตามความสำคัญ

| # | งาน | ต้องทำอะไร | เอกสารพร้อมใช้ |
|---|---|---|---|
| 1 | ตรวจ PDPA ก่อนเก็บข้อมูลอาสา | ส่งชุดคำถาม 8 ข้อให้นักกฎหมาย/DPO → แก้นโยบายตามคำตอบ | [pdpa-review-request.md](pdpa-review-request.md) |
| 2 | เก็บข้อมูลภาคสนาม | ชวนเกษตรกรอาสา 8–10 ราย (เป้า ≥ 50 จุดที่ตอบชัด) + ขอเจ้าหน้าที่เกษตรสำรวจพืช (พืชละ ≥ 10 จุด) | [volunteer-recruitment.md](volunteer-recruitment.md), [officer-guide.md](officer-guide.md) |
| 3 | ผู้เชี่ยวชาญตรวจคำแนะนำทุเรียน 17 ข้อ | ทุกข้อยังเป็น "ร่าง" — ส่งให้นักวิชาการเกษตรตรวจ แล้วอนุมัติที่ `/dashboard/recommendations.html` | [officer-review-message.md](officer-review-message.md), `recommendations-for-review.xlsx` |
| 4 | แหล่งพยากรณ์ฝน | Open-Meteo รุ่นฟรีห้ามใช้เชิงพาณิชย์ → สมัคร token กรมอุตุฯ (TMD) แล้วเชื่อมโค้ด | [weather-tmd-openmeteo.md](weather-tmd-openmeteo.md) |
| 5 | ไลเซนส์ Earth Engine | ใช้เชิงพาณิชย์/บริการรัฐต้องแผนเสียเงิน (~$500/เดือน) — ต้องตัดสินใจ | [license-review.md](license-review.md), [earth-engine-cost.md](earth-engine-cost.md) |
| 6 | สรุปผลวัด EECU ครบ 7 วัน | ~13 พ.ย. อ่าน `/admin/ee-usage` แล้วเติมใน earth-engine-cost.md · ยืนยันว่าสแกนรายวันหลัง 11 พ.ย. ทำงานผ่านโปรเจกต์ใหม่ | [earth-engine-cost.md](earth-engine-cost.md) |
| 7 | เอกสาร/สไลด์ขอทุน | ตัวเลขเดิมหลายตัวยังไม่ได้ทดสอบ ต้องใช้ข้อความทดแทน · ส่งสไลด์เป็น PDF (ข้อความเดิมยังอยู่ใต้กล่องที่วางทับ) | [claims-replacement.md](claims-replacement.md), [grant-slides-v2-review.md](grant-slides-v2-review.md) |
| 8 | เรื่องธรรมาภิบาลที่ยังไม่ตัดสินใจ | ชื่อผู้ควบคุมข้อมูล, DPA กับเกษตรจังหวัด, ระยะเก็บ log | [data-governance.md](../data-governance.md) ข้อ 10 |

งานเล็กด้านความปลอดภัยที่เจ้าของต้องทำเอง: เปิด 2-step verification ของบัญชี Google ที่เป็นเจ้าของโปรเจกต์ (Google Cloud เริ่มบังคับ) ·
ตามบันทึก `RENDER_API_KEY` เคยถูกพิมพ์ในแชทและยังไม่ได้สร้างใหม่ — ควรลบแล้วสร้างใหม่ · ลบไฟล์ `backend/.env.bak-*` ในเครื่อง

## 5. โครงสร้างระบบ

| ส่วน | ใช้อะไร |
|---|---|
| Backend | Python 3.11 + FastAPI ตัวเดียว (`backend/main.py`) เสิร์ฟทั้ง API และหน้าเว็บ |
| หน้าแอป | `liff/` (JavaScript ธรรมดา ไม่มี build step) · หน้าผู้ดูแล `dashboard/` (ใช้ header `X-Admin-Key`) |
| ดาวเทียม | Google Earth Engine (Sentinel-1, Sentinel-2) — โปรเจกต์ Cloud `iamroot-geoai` |
| ฐานข้อมูล/รูป | Supabase (Postgres + Storage) · migration อยู่ใน `supabase/` ถึง v25 |
| LINE | Messaging API + LIFF |
| โฮสต์ | Render service `IAMROOT` แพลน starter (เสียเงินรายเดือน — ค้างชำระแล้วระบบถูกระงับ) · push `master` = deploy อัตโนมัติ |
| งานตามเวลา | GitHub Actions `keep-alive.yml` เรียก `/admin/trigger/*` ช่วงเช้า (สแกนรายวัน, เตือนฝน) |

แผนสำรอง: branch `feat/cloud-run` (ย้ายไป Google Cloud Run) เขียนเสร็จแต่**ไม่ได้ merge** — ดู `docs/v2/cloud-run-migration.md` ใน branch นั้น

## 6. สิทธิ์ที่ต้องขอจากเจ้าของโครงการ

GitHub repo (สิทธิ์เขียน + Secrets) · Render · Supabase · Google Cloud โปรเจกต์ `iamroot-geoai` · LINE Developers / LINE Official Account ·
รหัสผู้ดูแลระบบ (`ADMIN_API_KEY`) · ไฟล์ `backend/.env` (ไม่อยู่ใน git) · โฟลเดอร์ `fieldwork/` และ `backups/` (อยู่ในเครื่องเจ้าของเท่านั้น มีพิกัดแปลง)

## 7. กติกาที่ต้องรักษา

1. **ห้ามอ้างตัวเลขความแม่นยำ/ผลผลิต/ต้นทุน ที่ระบบไม่ได้คำนวณจากข้อมูลจริง** — รายงานพร้อมจำนวนตัวอย่างเสมอ
2. คำแนะนำทางเกษตรมาจากตาราง `recommendation_rules` ที่คนตรวจแล้วเท่านั้น ห้ามเขียนในโค้ดหรือเดาตัวเลข
3. ก่อน migration: backup + มีไฟล์ rollback · migration แบบเพิ่มอย่างเดียว · หลัง deploy ทุกครั้งตรวจ `/health`, `/liff/`, `/dashboard/`
4. เปลี่ยนเนื้อหานโยบายความเป็นส่วนตัว = เปลี่ยน `PRIVACY_VERSION` (ผู้ใช้ทุกคนถูกถามใหม่) · ห้ามลบข้อมูลผู้ใช้ถาวรเอง ให้ผู้ดูแลกดที่ `/dashboard/users.html`
5. การทายชนิดพืช (`CROP_GUESS_ENABLED`) และคำเตือนระดับความเสี่ยงแบบเดิม **ปิดอยู่โดยตั้งใจ** — อย่าเปิดจนกว่าจะมีข้อมูลยืนยัน
6. repo เป็น public — ห้าม commit key, พิกัดแปลง, หรือชื่อเกษตรกร
7. ส่งข้อความ LINE เพิ่มต้องประเมินโควตาก่อน ([line-quota-estimate.md](line-quota-estimate.md))

## 8. เริ่มงานวันแรก

1. อ่าน [CLAUDE.md](../../CLAUDE.md) → ตารางความคืบหน้าบนสุดของ [decisions.md](decisions.md)
2. รันในเครื่อง: `cd backend && pip install -r requirements.txt && pytest -q` (Python 3.11)
3. เปิด `/dashboard/validation.html` และ `/dashboard/crop-model.html` ดูจำนวนข้อมูลภาคสนาม
4. เริ่มจากงานค้างข้อ 1–2 (ไม่ต้องเขียนโค้ด ต้องประสานคน)
