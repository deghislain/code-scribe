# --- Build stage ---
FROM python:3.11-slim AS builder

WORKDIR /app

RUN pip install --upgrade pip

COPY pyproject.toml ./
COPY backend/ ./backend/

# Install Python deps into a prefix so we can copy them cleanly
RUN pip install --prefix=/install --no-cache-dir .

# Strip compiled extensions and test files to cut size
RUN find /install -type d -name "tests"     -exec rm -rf {} + 2>/dev/null || true && \
    find /install -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /install -name "*.dist-info"        -exec rm -rf {} + 2>/dev/null || true


# --- Runtime stage ---
FROM python:3.11-slim

# WeasyPrint needs Pango/Cairo/GLib for PDF rendering.
# Install everything in one layer and purge apt caches in the same RUN
# to keep the layer as small as possible.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-liberation \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# Copy only the trimmed installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source — exclude heavy dirs explicitly
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Runtime directories
RUN mkdir -p /data/outputs /data/workspaces

# Default env — override at deploy time via Code Engine secrets/configmaps
ENV OUTPUT_DIR=/data/outputs \
    WORKSPACE_DIR=/data/workspaces \
    DB_PATH=/data/code_scribe.db \
    PORT=8080 \
    # Stops Python from writing .pyc files inside the container
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
