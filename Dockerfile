FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md glossary.yaml ./
COPY src/ src/

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "rss_sidecar.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
