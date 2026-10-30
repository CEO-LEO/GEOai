# IAM ROOT — image สำหรับ Google Cloud Run (ย้ายจาก Render 2026-10-30 · ดู docs/v2/cloud-run-migration.md)
# secret ทั้งหมดมาจาก env ของ Cloud Run ตอนรัน — ห้าม COPY .env (กันด้วย .dockerignore / .gcloudignore)

# ── Build stage ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────
FROM python:3.11-slim

# libfribidi0: Pillow wheel มี libraqm ในตัวแต่โหลด FriBiDi จากระบบตอนรัน — ไม่มี = วาดสระ/วรรณยุกต์ไทยเพี้ยน
# (รูปแผนที่ที่ส่ง LINE และรายงาน PDF) · บน Render ทำงานได้เพราะ image มี fribidi อยู่แล้ว
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=builder /install /usr/local
COPY backend/   ./backend/
COPY liff/      ./liff/
COPY dashboard/ ./dashboard/

WORKDIR /app/backend
USER app

# Cloud Run ส่งพอร์ตมาทาง $PORT (ปกติ 8080) · 8000 ไว้รันในเครื่อง
ENV PYTHONUNBUFFERED=1 \
    PORT=8000

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
