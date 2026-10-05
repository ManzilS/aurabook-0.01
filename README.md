# AuraBook

A memory layer for coding agents: capture a resolved debugging attempt, turn it into a reusable note, and retrieve relevant notes for later tasks.

An ongoing project exploring memory for AI coding agents, built to develop and demonstrate practical AI engineering skills. It includes a local Python proxy, a shared-memory backend, and a browser demo. The current implementation is a prototype; features and interfaces are still evolving.

## Try it

- [Website and demo](https://aurabooks.vercel.app/)
- [Installation and privacy guide](https://aurabooks.vercel.app/quickstart.html)
- [Source](https://github.com/ManzilS/aurabook-CODEX)

The scripted replay needs no credentials. Live comparison makes paid calls with your provider key; your key and prompt pass through the backend to the provider. Replay results illustrate the workflow and are not measured performance claims.

## Quick start

```powershell
git clone https://github.com/ManzilS/aurabook-CODEX.git
cd aurabook-CODEX
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ./Client
.\.venv\Scripts\aura.exe start --demo
```

On macOS/Linux, use `.venv/bin/python` and `.venv/bin/aura`. Demo mode uses scripted responses without provider credentials. The proxy listens at `http://127.0.0.1:8080`.

For the website, open another terminal in the repository root:

```powershell
cd Frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173` and select the scripted replay. A backend is required for live comparison and dashboard data.

Start with [DEPLOYMENT.md](DEPLOYMENT.md) for local setup, production configuration, verification, and current limitations. Use Node 24 and Python 3.11+.

## CloudflareBackend

The Worker API used by the production website. It provides live provider comparison, model discovery, dashboard reads, and signed client ingestion/search/feedback routes. See [CloudflareBackend/README.md](CloudflareBackend/README.md).

## Client

Everything needed for the local/client-side Python package and related local routing/brain integrations.

```powershell
cd Client
..\.venv\Scripts\python.exe -m pip install -e .
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
..\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
..\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Secrets and local runtime artifacts are intentionally not copied.

## Prototype boundaries

- Dashboard edits, comments, and ratings are session-only previews. Publishing them requires a user authorization workflow.
- Historical demonstration notes are examples, not evidence of current adoption or benchmark improvements.
- Client uploads require a configured backend and shared signing secret. The public demo does not issue client credentials.
- Redaction recognizes selected sensitive patterns; review context before enabling sharing.
- Generated Python runs through Pyodide in a browser worker with a timeout. It is not a security sandbox for hostile code or a full desktop Python environment.
- The site includes readable no-JavaScript content; interactive Python execution requires JavaScript and a Pyodide download.

## License

[MIT](LICENSE). Copyright (c) 2026 Manzil Sapkota.
