FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for psycopg binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Copy OpenClaw skill (for Docker-based OpenClaw deployments)
COPY openclaw/ openclaw/

# Default: run the local server (web dashboard + scheduler)
# Alternatives:
#   CMD ["python", "-m", "app.sync.scheduler"]       # scheduler only
#   CMD ["python", "-m", "app.sync.run_pipeline"]     # one-shot pipeline
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.server"]
