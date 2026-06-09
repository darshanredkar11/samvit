FROM python:3.12-slim

# System deps for psycopg / bcrypt native libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Pre-download the embedding model into the image so startup is instant
# Decision #14: model must be available; fail build if download fails
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy source
COPY samvit/ samvit/
COPY migrations/ migrations/

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["uvicorn", "samvit.main:app", "--host", "0.0.0.0", "--port", "8765"]
