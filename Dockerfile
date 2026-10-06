# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY public ./public
COPY scripts/tide-assets.mjs ./scripts/
RUN npm run build

# Build Python dependencies separately so git/pip caches stay out of the runtime.
FROM python:3.11-slim-bookworm AS python-deps
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY backend/requirements-runtime.txt backend/requirements-model.txt backend/requirements.lock.txt ./
RUN python -m venv /opt/venv
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install -c requirements.lock.txt torch --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install -c requirements.lock.txt -r requirements-runtime.txt -r requirements-model.txt
COPY scripts/patch-opf.py ./
RUN /opt/venv/bin/python patch-opf.py

FROM python:3.11-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libgomp1 && rm -rf /var/lib/apt/lists/*
ARG APP_UID=1000
ARG APP_GID=1000
RUN (getent group "$APP_GID" || groupadd --gid "$APP_GID" redacted) && \
    useradd --uid "$APP_UID" --gid "$APP_GID" --create-home redacted && \
    mkdir -p /app/data /models/privacy-filter && \
    chown -R "$APP_UID:$APP_GID" /app/data /models
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY --from=frontend /build/dist ./dist
COPY backend/*.py ./backend/
COPY scripts/download-model.py scripts/start-server.sh ./scripts/
COPY scripts/tidecloak.py scripts/tidecloak-container.py ./scripts/
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY licenses ./licenses
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPF_DEVICE=cpu \
    PRIVACY_DATA_DIR=/app/data \
    PRIVACY_FRONTEND_DIR=/app/dist \
    PRIVACY_MODEL_DIR=/models/privacy-filter \
    OPF_CHECKPOINT=/models/privacy-filter/original \
    TIKTOKEN_CACHE_DIR=/models/privacy-filter/tiktoken \
    HF_HOME=/models/huggingface
USER redacted
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/service/health', timeout=3)"
ENTRYPOINT ["sh", "/app/scripts/start-server.sh"]
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
