# aurabook-CODEX

Preserved AuraBook prototype checkpoint. Continued development belongs in the separate private `ManzilS/aurabook-focused` repository. This repository retains the original client, frontend, and backend implementation; the local folder remains named `FullBook`.

This folder separates AuraBook into deployable/use-focused parts.

Start with [DEPLOYMENT.md](DEPLOYMENT.md) for local setup, production configuration, verification, and current limitations. Use Node 24 and Python 3.11+.

## CloudflareBackend

The Worker API used by the production website. It provides live provider comparison, model discovery, dashboard reads, and signed client ingestion/search/feedback routes. See [CloudflareBackend/README.md](CloudflareBackend/README.md).

## Client

Everything needed for the local/client-side Python package and related local routing/brain integrations.

```powershell
cd Client
python -m pip install -e .
```

## Frontend

Everything needed to launch the React frontend.

```powershell
cd Frontend
npm install
npm run dev
```

## Backend

Everything needed to run the AuraBook Cloud Brain backend.

```powershell
cd Backend
python -m pip install -e .
Copy-Item .env.example .env
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Secrets and local runtime artifacts are intentionally not copied.
