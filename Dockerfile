FROM python:3.12-slim

# Install system utilities (curl for healthcheck, openssl for TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ app/
COPY templates/ templates/
COPY static/ static/
COPY run.py .

# Prepare directories for runtime volume mounts
RUN mkdir -p /app/data /app/certs

# Expose HTTPS (8443) and HTTP (8080)
EXPOSE 8443 8080

# Health check probe (HTTPS first, then the plain-HTTP fallback)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f -k https://127.0.0.1:8443/health || curl -f http://127.0.0.1:8080/health || exit 1

# Default runner command; HOST/PORT/HTTPS_ENABLED come from the environment
CMD ["python", "run.py"]
