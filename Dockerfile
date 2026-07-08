FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV HF_HOME=/opt/huggingface

COPY pyproject.toml README.md LICENSE ./
COPY samvit/ samvit/
COPY migrations/ migrations/
RUN pip install --no-cache-dir .
# Model downloads on first startup to HF_HOME — keeps build memory under 2 GB.
# On Railway/Render the model is fetched once then cached in the container layer.

FROM node:20-slim AS admin-ui-builder
WORKDIR /app/admin-ui
COPY admin-ui/package.json admin-ui/package-lock.json* ./
RUN npm ci
COPY admin-ui/ ./
RUN npm run build

FROM base AS test
COPY tests/ tests/
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-q"]

FROM base AS runtime
COPY --from=admin-ui-builder /app/admin-ui/dist /app/admin-ui/dist
RUN useradd --create-home --uid 10001 samvit && \
    mkdir -p /opt/huggingface && \
    chown -R samvit:samvit /opt/huggingface
USER samvit

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["samvit", "serve", "--host", "0.0.0.0", "--port", "8765"]
