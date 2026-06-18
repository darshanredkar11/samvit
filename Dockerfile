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

RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5').embed(['warmup']))"

FROM node:24-slim AS admin-ui-builder
WORKDIR /app/admin-ui
COPY admin-ui/package.json admin-ui/package-lock.json* ./
RUN npm ci
COPY admin-ui/ ./
RUN npm run build

FROM base AS runtime
COPY --from=admin-ui-builder /app/admin-ui/dist /app/admin-ui/dist
RUN useradd --create-home --uid 10001 samvit
RUN chown -R samvit:samvit /opt/huggingface
USER samvit

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["samvit", "serve", "--host", "0.0.0.0", "--port", "8765"]

FROM base AS test
COPY tests/ tests/
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-q"]
