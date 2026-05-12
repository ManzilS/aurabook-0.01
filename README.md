# FullBook

This folder separates AuraBook into deployable/use-focused parts.

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
