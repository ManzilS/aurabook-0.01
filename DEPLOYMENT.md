# Run and Deploy FullBook

Use Node 24 and Python 3.11 or newer. Commands below start in the repository root.

## Local website

```powershell
cd Frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Open http://127.0.0.1:5173. The scripted replay works without credentials.
The live demo requires a running backend and your provider API key. Select
OpenAI, Claude, Gemini, or DeepSeek, validate the key, select a returned model,
and run a prompt. Provider requests consume your API credits. Python output
runs in a disposable browser worker; execution stops after 30 seconds.
Pyodide downloads from jsDelivr and needs network access. Browser workers are
not a full security sandbox for hostile code and cannot run arbitrary desktop packages.

## Backend options

The production backend is `CloudflareBackend`. `Backend` contains the Python
alternative and its processing tools. Supabase stores data for either backend.

For the Worker locally:

```powershell
cd CloudflareBackend
npm ci
Copy-Item .dev.vars.example .dev.vars
npm run dev
```

Fill in `.dev.vars` with the database URL, service-role key, server OpenAI key
for ingestion/embeddings, and a shared signing secret. Configure
`Frontend/.env` with `VITE_AURABOOK_API_BASE_URL=http://127.0.0.1:8787`.
The client must use the same signing secret. Never put it or a database
service-role key in a `VITE_` variable. Private routes reject unsigned requests.

For the Python API:

```powershell
cd Backend
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Configure the database and provider settings before using this API with real
data. See `.env.example`; demo mode is a separate local fixture store.

## Vercel and Cloudflare

1. Deploy `CloudflareBackend` using `npm run deploy`. Set its secrets as listed
   in `CloudflareBackend/README.md`, and set allowed origins to your Vercel URL.
2. Connect the `ManzilS/aurabook-CODEX` GitHub repo to Vercel. Root directory: `Frontend`.
   Build command: `npm run build`. Output: `dist`. Node version: 24.
3. Set `VITE_AURABOOK_API_BASE_URL` to the deployed Worker's HTTPS URL, then rebuild.
   This also configures the no-JavaScript page's form and API links.
4. Confirm `/health`, `/v1/dashboard/snapshot`, `/`, `/demo`, and `/dashboard`.
   Test the website in a signed-out browser with JavaScript both on and off.
5. A Vercel 403 must be investigated in Deployment Protection and Firewall
   settings. Frontend HTML cannot fix a request blocked before it reaches the app.

No domain purchase is necessary; the generated Vercel and workers.dev URLs work.
Use `Backend/supabase.sql` or its ordered chunks for database setup, reviewing
the SQL first. Do not seed or clear production data as part of deployment.

## Verification

```powershell
cd Client
python -m pytest -q
cd ../Backend
python -m pytest -q
cd ../CloudflareBackend
npm run typecheck
npm test
cd ../Frontend
npm run lint
npm run build
```

Browser smoke tests need Chromium (`npx playwright install chromium`) and a
Vite dev server at port 5174. Start `npm run dev -- --port 5174` in another
terminal, then run `npm run test:smoke`. They cover responsive pages, no-JS
content, backend failure, provider selection, and real Pyodide execution.
No paid model requests or database writes are made by these tests.

## Current boundaries

The public dashboard reads real backend data. Failed requests show an error;
they never generate sample notes. Ratings, comments, and solution edits are
explicitly labeled session-only previews, not published database changes.
Publishing them needs a user authentication and authorization workflow.
The no-JS page links to current API data rather than asserting database counts.
It cannot run Pyodide, which requires JavaScript.

Automated tests do not establish that production credentials, provider billing,
database migrations, or Vercel access controls are configured correctly.
