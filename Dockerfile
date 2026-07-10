FROM python:3.12-slim

WORKDIR /app

# System deps for lxml/trafilatura
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/
COPY config.example.yaml .

# Create data dir
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV WORKERS=1

EXPOSE 8000

CMD ["uvicorn", "rss_sidecar.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
