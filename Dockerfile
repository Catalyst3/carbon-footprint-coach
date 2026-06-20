# Small, portable image — works on Cloud Run, Fly.io, Railway, Render, etc.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ ./backend/
COPY prompts/ ./prompts/
COPY static/ ./static/

# Hosts inject the port via $PORT (Cloud Run/Render/Fly). Default to 8000 locally.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn backend.server:app --host 0.0.0.0 --port ${PORT}"]
