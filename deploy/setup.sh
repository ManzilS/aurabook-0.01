#!/bin/bash
# First-time production setup script.
# Run on your server after cloning the repo.
#
# Usage: cd deploy && bash setup.sh

set -euo pipefail

echo "=== AuraBook Production Setup ==="
echo

# Check prerequisites
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required"; exit 1; }
command -v docker compose >/dev/null 2>&1 || { echo "ERROR: docker compose is required"; exit 1; }

# Generate .env if missing
if [ ! -f .env ]; then
  echo "Creating .env from template..."
  cp .env.example .env

  # Auto-generate secrets
  POSTGRES_PW=$(openssl rand -hex 32)
  SESSION_SEC=$(openssl rand -base64 32 | tr -d '=+/' | head -c 43)
  API_SEC=$(openssl rand -base64 32 | tr -d '=+/' | head -c 43)

  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PW}|" .env
  sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SESSION_SEC}|" .env
  sed -i "s|^API_SHARED_SECRET=.*|API_SHARED_SECRET=${API_SEC}|" .env

  echo
  echo "Generated secrets in .env"
  echo "You still need to fill in:"
  echo "  - DOMAIN (your domain name)"
  echo "  - GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET"
  echo "  - OPENAI_API_KEY"
  echo "  - STRIPE_SECRET_KEY (optional, for billing)"
  echo
  echo "Edit .env now, then re-run this script."
  exit 0
fi

# Validate required env vars
source .env
if [ -z "${DOMAIN:-}" ]; then
  echo "ERROR: DOMAIN is not set in .env"; exit 1
fi
if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "ERROR: POSTGRES_PASSWORD is not set in .env"; exit 1
fi

echo "Domain: ${DOMAIN}"
echo "Starting services..."
echo

# Pull/build and start
docker compose -f docker-compose.prod.yml up -d --build

echo
echo "Waiting for backend health check..."
for i in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml exec backend python -c "import httpx; r=httpx.get('http://localhost:8000/health'); exit(0 if r.status_code==200 else 1)" 2>/dev/null; then
    echo "Backend is healthy!"
    break
  fi
  sleep 2
done

echo
echo "Running database migrations..."
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

echo
echo "=== Setup Complete ==="
echo "Your AuraBook instance is live at https://${DOMAIN}"
echo
echo "Next steps:"
echo "  1. Create a GitHub OAuth App pointing to https://${DOMAIN}/auth/github/callback"
echo "  2. Add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to .env"
echo "  3. Run: docker compose -f docker-compose.prod.yml up -d"
echo
