# ── Build stage ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Copy source
COPY backend/   ./backend/
COPY liff/      ./liff/
COPY dashboard/ ./dashboard/

WORKDIR /app/backend

# GEE key จะ mount เข้ามาตอน runtime ผ่าน env/secret
ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
