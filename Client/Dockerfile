FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY aurabook /app/aurabook
COPY scripts /app/scripts
COPY fixtures /app/fixtures

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["aura", "start", "--demo"]
