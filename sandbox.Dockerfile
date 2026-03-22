# ============================================================
# FlyAgent Sandbox Container Image
# ============================================================
# Pre-built environment for isolated agent execution.
# Each sandboxed SubAgent runs inside its own container with:
#   - Read-only root filesystem
#   - Only /workspace (bind mount) and /tmp (tmpfs) are writable
#   - Resource limits (memory, CPU, PIDs)
#   - Optional network isolation
#
# Build:  docker build -f sandbox.Dockerfile -t flyagent-sandbox:latest .
# ============================================================

FROM python:3.11-slim

# System utilities agents commonly need
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    jq \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pre-install common Python packages so agents don't waste steps on pip install
RUN pip install --no-cache-dir \
    numpy \
    pandas \
    matplotlib \
    scipy \
    scikit-learn \
    requests \
    beautifulsoup4 \
    lxml \
    pyyaml \
    toml \
    jsonschema \
    sympy \
    Pillow \
    openpyxl \
    tabulate

# Non-root user for defense in depth
RUN useradd -m -s /bin/bash agent
USER agent
WORKDIR /workspace

# Container stays alive for docker exec commands
CMD ["sleep", "infinity"]
