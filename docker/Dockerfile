# docker/Dockerfile — Compliance Risk Investigator API (Task 3.9)
#
# Builds the FastAPI application image.
# Parquets, model outputs, and compliance rules are NOT baked in —
# they are mounted as read-only volumes at runtime via docker-compose.
#
# Build context: project root (one level above docker/)
#   docker-compose build
#   docker-compose up

FROM python:3.12-slim

# ── System dependencies ────────────────────────────────────────────────────────
# gcc and g++ are needed for some native Python packages (e.g. shap, scipy).
# libgomp1 is required by scikit-learn's OpenMP parallelism.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────────
# Install dependencies first (separate layer — cached unless requirements.txt changes)
COPY docker/requirements-api.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefer-binary -r requirements-api.txt

# ── Application code ───────────────────────────────────────────────────────────
# Copy entire project (docker/.dockerignore excludes venv, parquets, notebooks, etc.)
COPY . .

# ── Runtime directories ────────────────────────────────────────────────────────
# Create mount-point directories so docker-compose volumes bind cleanly.
# These will be overwritten by the host volume mounts at runtime.
RUN mkdir -p \
        /app/features/outputs \
        /app/models/outputs \
        /app/compliance

# ── Port ───────────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Entrypoint ─────────────────────────────────────────────────────────────────
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
