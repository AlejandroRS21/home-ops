# ─── Builder stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed to compile deps from uv.lock into a wheel house.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv in builder only (pinned for reproducibility).
RUN pip install --no-cache-dir uv==0.11.19

# Copy lockfile + project metadata, then export the resolved deps to a wheel
# directory. This gives a pinned, reproducible dependency set without ever
# needing the full source tree at build time.
COPY pyproject.toml uv.lock ./
RUN uv export --no-hashes --no-dev --no-emit-project -o /tmp/requirements.txt \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt
# Stage the pinned hatchling build backend and its helper deps for the runner.
# --no-deps keeps hatchling's "packaging" from re-resolving to a newer wheel
# than the uv-pinned one already in /wheels (pathspec/pluggy/tomlkit/
# trove-classifiers/editables are leaf packages with no transitive conflicts).
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels hatchling==1.32.0 \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels pathspec pluggy tomlkit trove-classifiers editables


# ─── Runner stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runner

WORKDIR /app

# Copy pre-built wheels from the builder and install them with no deps tree
# mutation, no dev extras, no Playwright/Chromium dead weight.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

# Copy the application source. user_profile.template.yml is provided so the
# loader's fallback path (config/user_profile.yml -> ./user_profile.yml) works
# for fresh clones that have not yet provisioned a profile.
COPY src /app/src
COPY config /app/config
COPY pyproject.toml /app/
# Isolated PEP 517 build normally fetches hatchling from PyPI at build time.
# The pinned hatchling wheel is already staged in /wheels by the builder, so
# --no-build-isolation resolves it offline; it is removed afterwards so the
# runtime image ships without build tooling.
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .
RUN pip uninstall -y hatchling

# Run as a non-root user. uid 10001 is the conventional "scratch" service UID.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin homeops \
    && chown -R homeops:homeops /app
USER 10001

# Liveness probe — `homeops status` should exit 0 even on an empty DB.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD homeops status >/dev/null 2>&1 || exit 1

CMD ["homeops", "daemon"]
