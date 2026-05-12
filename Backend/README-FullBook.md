# AuraBook Backend

This folder contains the AuraBook Cloud Brain backend.

Install and run:

```powershell
python -m pip install -e .
Copy-Item .env.example .env
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Supabase schema chunks are in:

```text
supabase_chunks/
```

Run tests:

```powershell
python -m pytest tests -q
```

Real `.env`, logs, virtualenvs, cache folders, and local runtime data were not copied.
