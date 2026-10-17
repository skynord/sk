# --- Stage 1: build the React dashboard -------------------------------
FROM node:20-slim AS frontend-build

WORKDIR /fe

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python API runtime --------------------------------------
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /fe/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST=/app/frontend/dist

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
