---
description: "อธิบายโครงสร้างและสถาปัตยกรรมภาพรวมของระบบ GEOai (ทั้ง High-Level และ Technical)"
name: "Explain GEOai System"
agent: "agent"
---

กรุณาอธิบายโครงสร้างและสถาปัตยกรรมของระบบ GEOai โดยแบ่งการอธิบายออกเป็น 2 ส่วนหลัก เพื่อให้ครอบคลุมทั้งมุมมองแบบเข้าใจง่ายและมุมมองเชิงลึก ดังนี้:

### ส่วนที่ 1: High-Level Overview (สำหรับผู้บริหารและเกษตรกร)
- อธิบายภาพรวมระบบว่า **GEOai** คืออะไร มีประโยชน์อย่างไร
- สรุป Data Flow แบบละเอียดเป็นลำดับขั้นตอน (Step-by-step) ด้วยรูปแบบ Input -> Process -> Output:
  - **Input (การรับข้อมูล)**: อธิบายขั้นตอนที่ผู้ใช้อินเตอร์แอกต์กับระบบผ่าน LINE (เช่น การส่ง Location, แจ้งพิกัดผ่านแชท, หรือลงทะเบียนและวาดแปลงผ่านหน้า LIFF App) และส่งข้อมูลผ่าน Webhook
  - **Process (การประมวลผล)**: เจาะลึกกระบวนการทำงานภายในเบื้องหลังตามลำดับ (เช่น FastAPI รับข้อมูล -> นำพิกัดไป Query ข้อมูลภาพถ่ายดาวเทียม Sentinel จาก Google Earth Engine -> ส่งค่าต่างๆ ให้ Rule Engine คำนวณปริมาณปุ๋ย/ความเสี่ยง -> บันทึกประวัติและผลลัพธ์ทั้งหมดลงในฐานข้อมูล Supabase)
  - **Output (ผลลัพธ์และการแสดงผล)**: อธิบายขั้นตอนการนำผลลัพธ์มาจัดรูปแบบ (การสร้าง Flex Message template) การส่งคำตอบแจ้งเตือนกลับไปยังแชท LINE ของผู้ใช้งาน และการส่งข้อมูลไปแสดงสถิติบน Dashboard
- สรุปฟีเจอร์สำคัญ (เช่น แนะนำปุ๋ย, ปริมาณผลผลิต, พื้นที่เสี่ยงดินสไลด์, ความอุดมสมบูรณ์)

### ส่วนที่ 2: Technical Architecture (สำหรับนักพัฒนา)
กรุณาอ้างอิงจากโครงสร้างไฟล์ใน Workspace เพื่ออธิบายกลไกเชิงลึก:
- **Backend (FastAPI)**: การรับ Request (`backend/main.py`), การเชื่อมต่อ Google Earth Engine (`backend/gee_analysis.py`), และการวิเคราะห์เงื่อนไข (`backend/rule_engine.py`)
- **LINE Integration**: การจัดการ Webhook (`backend/webhook.py`), การตอบกลับข้อความและสร้างหน้าตา Flex Message (`backend/line_sender.py`, `backend/flex_messages.py`)
- **Database (Supabase Postgres)**: โครงสร้างตารางจาก `schema.sql` (users, plots, analyses) รวมถึง RLS policies
- **Frontend / LIFF**: โครงสร้างแดชบอร์ด (`dashboard/index.html`) และแอปที่ฝังใน LINE (`liff/index.html`, `liff/sphere-mock.js`)
- **Deployment**: การจัดการคอนเทนเนอร์และเซิร์ฟเวอร์ด้วย Docker และ Nginx
