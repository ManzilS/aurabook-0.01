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
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

ARG DB_EXTRAS="postgres"
RUN pip install --no-cache-dir ".[$DB_EXTRAS]"

EXPOSE 8000

CMD ["aurabook-cloud-api"]
