FROM python:3.11-slim as builder

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir -e .

FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only (no build tools)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source last (changes most often)
COPY src/ ./src/

# Create data directories
RUN mkdir -p /data/snapshots /data/db

VOLUME ["/data/db", "/data/snapshots"]
ENV HOME_OPS_DB_PATH=/data/db/home_ops.duckdb

ENTRYPOINT ["homeops"]
CMD ["--help"]
