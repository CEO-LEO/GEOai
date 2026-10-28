# ตรวจไลเซนส์ข้อมูลดาวเทียม (Phase 4a ข้อ 7.2) — 25 ต.ค. 2569

ผู้อ่าน: เจ้าของโปรเจกต์ · **ไม่ใช่คำปรึกษาทางกฎหมาย** — ควรให้ผู้รู้ตรวจก่อนทำสัญญากับหน่วยงานรัฐ
· spec ข้อ 9.3 ค่าเริ่มต้น: ถือว่าโครงการเป็น**เชิงพาณิชย์**

## สรุปสั้น

1. **Earth Engine (แพลตฟอร์ม) เป็นเรื่องใหญ่ที่สุด และกระทบทั้งแอป ไม่ใช่แค่การจำแนกพืช**
   ระบบใช้ Earth Engine ทุกครั้งที่วิเคราะห์แปลงอยู่แล้ว ถ้าโครงการขายหรือให้บริการสำนักงานเกษตรจังหวัด
   จะใช้แบบฟรีไม่ได้ ต้องมีแพลน commercial (เริ่มประมาณ **$500/เดือน**) หรือเข้าโครงการที่ Google สนับสนุน
2. ชุดข้อมูลส่วนใหญ่ใช้เชิงพาณิชย์ได้ แค่ใส่ที่มา: Satellite Embedding, Dynamic World, Sentinel-1/2, geoBoundaries
3. **MERIT Hydro (ใช้วัด "ที่ลุ่มต่ำ") มีเงื่อนไขที่ขัดกับงานนี้** — แบบเชิงพาณิชย์ (ODbL) ต้องเปิดเผยข้อมูลที่ได้จากมันต่อสาธารณะ
   ซึ่งขัดกับการเก็บข้อมูลแปลงเป็นความลับ → เสนอเปลี่ยนแหล่ง (ข้อ 4)

## 1. Earth Engine

| ประเด็น | เงื่อนไข (จากหน้าเว็บ Google ณ วันที่ตรวจ) |
|---|---|
| ใช้ฟรีได้ | องค์กรไม่แสวงกำไร · สถาบันการศึกษา (วิจัย/สอน) · สื่อ · หน่วยงานรัฐบางกรณี (ประเทศพัฒนาน้อยที่สุด, รัฐบาลชนพื้นเมือง หรือ**ใช้เพื่องานวิจัย**) · บุคคลทั่วไปที่ไม่ใช่เชิงพาณิชย์ |
| ใช้ฟรีไม่ได้ | งานรับจ้าง/ส่งมอบตามสัญญาที่มีค่าตอบแทน (fee-for-service) · หารายได้จากบริการที่สร้างบน Earth Engine · หน่วยงานรัฐที่ใช้งาน**ประจำ** (operational) หรือดูแลบริการต่อเนื่อง |
| แพลนเสียเงิน | ค่ารายเดือน ~$500 (Basic) / ~$2,000 (Professional) รวมเครดิตประมวลผล + คิดตามการใช้ ~$0.40/EECU-ชม. (ตัวเลขจากผลค้นหาหน้าราคา — ตรวจหน้าจริงก่อนตัดสินใจ) |
| ทางเลือก | Google for Startups Cloud Program (ครอบคลุม Earth Engine) · หรือให้หน่วยงานรัฐ/มหาวิทยาลัยเป็นเจ้าของ Cloud project ในนามงานวิจัย (ต้องเป็นงานวิจัยจริง ไม่ใช่บริการประจำ) |
| ข้อสังเกต | Google ระบุว่าหน่วยงานรัฐต้องใช้บัญชีองค์กร ไม่ใช่บัญชีส่วนตัว · โควตา noncommercial ตั้งแต่ เม.ย. 2569 เป็นรายเดือน (Community Tier 150 EECU-ชม.) |

**การใช้ของระบบตอนนี้:** สแกนรายวัน ~25 แปลง + วิเคราะห์ตามที่ผู้ใช้กด — น้อยมาก แพลน Basic ครอบคลุมได้สบาย
ค่าใช้จ่ายหลักคือค่ารายเดือน ไม่ใช่ค่าประมวลผล

## 2. ชุดข้อมูล

| ชุดข้อมูล | ใช้ทำอะไร | ไลเซนส์ | เชิงพาณิชย์ | ต้องทำ |
|---|---|---|---|---|
| AlphaEarth Satellite Embedding V1 (รายปี 64 มิติ 10 ม.) | จำแนกพืช (4a) | CC BY 4.0 | ได้ | ใส่ที่มา "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind." · ตรวจ 25 ต.ค. 2569: มีข้อมูล 2017–2025 ที่ราชบุรี |
| Sentinel-1 / Sentinel-2 (Copernicus) | ความชื้น ดัชนีพืช รูปแปลง | Copernicus Sentinel Data Terms (ฟรี เปิด ใช้เชิงพาณิชย์ได้) | ได้ | ใส่ "Contains modified Copernicus Sentinel data [ปี]" |
| Dynamic World (Google) | ประเภทพื้นที่ในขอบเขต | CC BY 4.0 | ได้ | ใส่ที่มา |
| geoBoundaries ADM2 | ขอบเขตอำเภอ | CC BY 4.0 | ได้ | ใส่ที่มา (มีแล้วในเอกสาร) |
| SRTM | ความสูง (ระบบเดิม) | สาธารณสมบัติ (USGS) | ได้ | — |
| **MERIT Hydro** | ความสูงเหนือทางน้ำ (HAND) → ป้าย "ที่ลุ่มต่ำ" | CC BY-NC 4.0 **หรือ** ODbL 1.0 | NC: ไม่ได้ · ODbL: ได้ แต่ข้อมูลที่ได้จากมันต้องเปิดเผยแบบ ODbL · ขอไม่ให้แจกจ่ายต่อทั้งชุด | ดูข้อ 4 |
| Open-Meteo (ไม่ใช่ Earth Engine) | พยากรณ์ฝน | ฟรีเฉพาะไม่ใช่เชิงพาณิชย์ | ต้องสมัคร | ดู [weather-source-options.md](weather-source-options.md) |

## 3. การจำแนกพืช (Phase 4a) ทำอะไรไปแล้ว

- สร้างระบบครบ แต่**ยังไม่แสดงการทายให้ผู้ใช้** (ปิดด้วย `CROP_GUESS_ENABLED`) — เปิดเมื่อเจ้าของตอบข้อ 5 และโมเดลผ่านเกณฑ์
- คำนวณ feature ด้วย Earth Engine แบบ batch ไม่กี่สิบแปลง (อยู่ในโควตาเดียวกับที่ระบบใช้อยู่)

## 4. MERIT Hydro — ผลเทียบกับทางเลือก (อัปเดต 28 ต.ค. 2569)

**ข้อเท็จจริงสำคัญ:** ค่า HAND จาก MERIT ใช้เฉพาะ "ลักษณะประจำแปลง" ในโหมดรันคู่ (`plot_profile`) —
**ไม่แสดงผู้ใช้ ไม่ใช้ตัดสินการแจ้งเตือน ไม่ส่งออกให้ใคร** ความเสี่ยงทางไลเซนส์จึงต่ำกว่าที่ประเมินไว้ตอนแรก

เทียบทางเลือกที่ใช้เชิงพาณิชย์ได้ กับแปลงจริง 22 แปลง (ค่าเฉลี่ยในขอบเขต):

| แหล่ง | ไลเซนส์ | ผล |
|---|---|---|
| MERIT Hydro (ใช้อยู่) | CC BY-NC 4.0 / ODbL | ค่ากลาง 2.0 ม. — แก้ความสูงทรงพุ่มออกจาก DEM แล้ว |
| Global HAND 30 ม. (Donchyts et al. 2016, ใน Earth Engine) | CC BY 4.0 | ค่ากลาง 5.6 ม. · สัมพันธ์รวม r = 0.99 แต่ป้าย "ลุ่มต่ำ/ลุ่มต่ำมาก" ตรงกันแค่ **10 ใน 22** — ที่ลุ่มต่ำมาก 7 แปลงกลายเป็นแค่ลุ่มต่ำ |
| ASF GLO-30 HAND (จาก Copernicus DEM) | CC0 | ไม่อยู่ใน Earth Engine — ต้องส่งพิกัดแปลงไปเซิร์ฟเวอร์ภายนอก (ผู้ประมวลผลรายใหม่) หรือดาวน์โหลดไฟล์มาเก็บเอง |

**เหตุที่ต่างกัน (น่าจะเป็น):** SRTM และ Copernicus DEM วัดผิวบนสุด**รวมทรงพุ่ม** สวนผลไม้จึงดูสูงกว่าทางน้ำเกินจริง
ส่วน MERIT ลบความสูงต้นไม้ออกแล้ว → สำหรับสวนในที่ราบภาคกลาง **MERIT แม่นกว่า**

**ตัดสินใจ:** ใช้ MERIT ต่อ (ภายในเท่านั้น) · ไม่เปลี่ยนเป็นแหล่งที่ทำให้ผลแย่ลง ·
ก่อนแสดงป้าย "ที่ลุ่มต่ำ" ให้ผู้ใช้ในงานเชิงพาณิชย์: ขออนุญาตผู้พัฒนา MERIT (ทีม Univ. of Tokyo) หรือประเมิน ASF GLO-30 HAND
แบบดาวน์โหลดเฉพาะพื้นที่ราชบุรีมาเก็บเอง (ไม่ส่งพิกัดแปลงออกไป) แล้วเทียบกับผลภาคสนามก่อน
## 5. ต้องให้เจ้าของตอบ

1. โครงการกับสำนักงานเกษตรจังหวัดราชบุรี **มีค่าตอบแทนหรือเป็นบริการประจำ** หรือเป็นโครงการวิจัยนำร่อง (เช่น ผ่านมหาวิทยาลัย)?
2. Cloud project ของ Earth Engine ตอนนี้ลงทะเบียนเป็นประเภทไหน (ดูที่ console.cloud.google.com → Earth Engine → การกำหนดค่า)
3. ถ้าเป็นเชิงพาณิชย์: สมัคร Google for Startups / แพลน Basic / ให้หน่วยงานเป็นเจ้าของ project — Claude จะไม่สมัครหรือจ่ายเงินเอง
4. ~~อนุมัติเปลี่ยน MERIT Hydro~~ — ตรวจแล้วไม่ควรเปลี่ยนตอนนี้ (ข้อ 4) · ตัดสินใจเฉพาะเมื่อจะแสดงป้าย "ที่ลุ่มต่ำ" ให้ผู้ใช้เชิงพาณิชย์

ที่มา: [Satellite Embedding V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL) ·
[Earth Engine for Noncommercial Use](https://earthengine.google.com/noncommercial) ·
[Noncommercial tiers](https://developers.google.com/earth-engine/guides/noncommercial_tiers) ·
[Transition to commercial](https://developers.google.com/earth-engine/guides/transition_to_commercial) ·
[Earth Engine pricing](https://cloud.google.com/earth-engine/pricing) ·
[MERIT Hydro](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1) ·
[Sentinel-2 SR](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED) ·
[Global HAND (community catalog)](https://gee-community-catalog.org/projects/hand/) ·
[ASF GLO-30 HAND](https://registry.opendata.aws/glo-30-hand/) ·
[Copernicus DEM GLO-30](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30) ·
[HydroSHEDS](https://developers.google.com/earth-engine/datasets/catalog/WWF_HydroSHEDS_15ACC)
