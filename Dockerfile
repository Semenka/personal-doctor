FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for psycopg binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Default: run the scheduler (persistent mode)
# Override with CMD for one-shot jobs
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.sync.scheduler"]
