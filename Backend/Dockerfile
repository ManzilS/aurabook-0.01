FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY config.py /app/config.py
COPY api /app/api
COPY db /app/db
COPY models /app/models
COPY services /app/services
COPY worker /app/worker
COPY scripts /app/scripts
COPY supabase.sql /app/supabase.sql

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["aurabook-cloud-api"]
