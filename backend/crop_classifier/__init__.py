"""
crop_classifier — Phase 4a: ทาย "แปลงนี้น่าจะปลูกอะไร" (spec ข้อ 7)

แยกเป็นแพ็กเกจที่มี interface ของตัวเอง (spec 7.1 "service ต่างหาก") — แอปหลักเรียกผ่าน service.py เท่านั้น
ตอนนี้รันใน Render service เดิม (ไม่มีค่าใช้จ่ายเพิ่ม) · ย้ายไปเป็น service แยกได้โดยไม่ต้องแก้ส่วนอื่น

  sources.py   แหล่งภาพแบบ adapter (embedding / Sentinel-2 / Sentinel-1) — Phase 4b เพิ่มแหล่งใหม่ที่นี่
  features.py  สกัด feature ต่อแปลงจาก GEE (batch + แคชในตาราง crop_features)
  labels.py    ประกอบชุดฝึกจากการยืนยันของผู้ใช้/เจ้าหน้าที่/ทะเบียนเกษตรกร (ฟังก์ชันล้วน)
  model.py     logistic regression หลายคลาส → พารามิเตอร์ JSON + ตัดสิน "ไม่แน่ใจ" (ฟังก์ชันล้วน)
  evaluate.py  ประเมินแบบแบ่งเชิงพื้นที่ (อำเภอ / กริด 5 กม.) + เกณฑ์เปิดใช้ (ฟังก์ชันล้วน)
  store.py     อ่าน/เขียน Supabase ของแพ็กเกจนี้
  service.py   งานระดับบน: ทายแปลง, สกัด feature แบบ batch, เทรน+ประเมิน+เก็บโมเดล

ห้ามใช้ภาพที่ผ่าน super-resolution เป็น input (spec 7.5) — แหล่งภาพใน sources.py เป็นภาพดิบเท่านั้น
"""
