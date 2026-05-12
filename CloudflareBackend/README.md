# AuraBook Cloudflare Backend

Cloudflare Worker API gateway for the Vercel frontend.

## Required Secrets

Set these in Cloudflare using `wrangler secret put ...` or the Worker dashboard:

```powershell
npx wrangler secret put AURABOOK_CLOUD_SUPABASE_URL
npx wrangler secret put AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY
npx wrangler secret put AURABOOK_CLOUD_OPENAI_API_KEY
```

Required for private client routes if you want signed requests:

```powershell
npx wrangler secret put AURABOOK_CLOUD_API_SHARED_SECRET
```

If this secret is unset, private routes are open to anyone who knows the Worker URL. Keep it set in production.

## Public Env Var

Set this to your Vercel origin after deployment:

```text
AURABOOK_ALLOWED_ORIGINS=https://your-vercel-project.vercel.app,http://localhost:5173,http://127.0.0.1:5173
```

## Deploy

```powershell
cd E:\Codex\AIBook\FullBook\CloudflareBackend
npm install
npx wrangler login
npx wrangler deploy
```

Then set the Vercel frontend env vars to the Worker URL:

```text
VITE_AURABOOK_API_BASE_URL=https://aurabook-api.YOUR-SUBDOMAIN.workers.dev
VITE_AURABOOK_DASHBOARD_API_URL=https://aurabook-api.YOUR-SUBDOMAIN.workers.dev/v1/dashboard
VITE_AURABOOK_WS_URL=
VITE_AURABOOK_LIVE_COMPARE_URL=https://aurabook-api.YOUR-SUBDOMAIN.workers.dev/v1/demo/compare
```

## Routes

Public frontend routes:

```text
GET  /health
GET  /v1/dashboard/snapshot
GET  /v1/dashboard/ws
GET  /v1/demo/models/warm
POST /v1/demo/models
POST /v1/demo/compare
```

Private client routes:

```text
POST /v1/ingest
POST /v1/search
POST /v1/prefetch
POST /v1/feedback
POST /v1/admin/prune
```

When `AURABOOK_CLOUD_API_SHARED_SECRET` is set, private routes require:

```text
X-AuraBook-Timestamp
X-AuraBook-Signature
```

Optional idempotency header:

```text
X-AuraBook-Idempotency-Key
```
