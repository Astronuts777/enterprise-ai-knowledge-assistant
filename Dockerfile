FROM node:22-slim AS web-builder

WORKDIR /workspace
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
ENV NEXT_OUTPUT_MODE=export
ENV NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_APP_MODE=accounts
RUN pnpm build:web

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/services/api/src:/app/packages/core/src
ENV WEB_STATIC_DIR=/app/apps/web/out
ENV LOCAL_STORAGE_PATH=/tmp/enterprise-ai-uploads

WORKDIR /app
COPY requirements.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.lock
COPY alembic.ini ./
COPY migrations migrations
COPY packages/core packages/core
COPY services/api services/api
COPY --from=web-builder /workspace/apps/web/out apps/web/out

EXPOSE 10000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn enterprise_ai_api.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
