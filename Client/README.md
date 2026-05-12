# AuraBook

AuraBook is a local proxy plus cloud memory system for coding agents. This repo now includes:

- `aurabook/`: the local proxy client, watcher, middleware pipeline, demo runtime, status CLI, and retry queue
- `aurabook-cloud/`: the cloud API, Supabase interfaces, notes worker, demo JSON stores, and swarm generator
- `frontend/`: the React + TypeScript + Tailwind app for the landing page, replay demo, and live knowledge dashboard
- `scripts/`: offline replay, benchmark, and judge-mode demo tooling

## Provider Support

AuraBook now supports these upstream model platforms:

- `openai`
- `ollama`
- `lmstudio` and `llmstudio`
- `qwen`
- `claude`
- `gemini`

The local proxy routes by model prefix. Examples:

```json
{"model":"ollama/llama3.2"}
{"model":"claude/claude-sonnet-4-20250514"}
{"model":"gemini/gemini-2.5-flash"}
{"model":"qwen/qwen-plus"}
```

`openai_proxy` remains the default adapter for backward compatibility, and `openai/...` is also accepted.

## Quick Demo

Run the local replay harness without Supabase or OpenAI:

```bash
python scripts/replay_harness.py
```

Run the benchmark suite:

```bash
python scripts/benchmark_eval.py --iterations 3
```

Run the judge-mode story:

```bash
python scripts/judge_mode.py
```

Start the local proxy in offline demo mode:

```bash
aura start --demo
```

Inspect the last persisted local state:

```bash
aura status
```

Audit what would leave the machine after redaction:

```bash
aura audit --text "api_key=sk-example dev@example.com 192.168.1.20"
```

Persist a safer intervention ceiling:

```bash
aura preferences set-gradient notes
```

## Cloud Brain

Start the cloud API in demo mode:

```bash
cd aurabook-cloud
python api/main.py
```

Run one notes worker batch:

```bash
cd aurabook-cloud
python worker/janitor.py
```

Seed synthetic demo trajectories:

```bash
cd aurabook-cloud
python scripts/swarm_generator.py --samples 5
```

Choose different providers for cloud generation and embeddings:

```env
AURABOOK_CLOUD_GENERATION_PROVIDER=claude
AURABOOK_CLOUD_EMBEDDING_PROVIDER=gemini
AURABOOK_CLOUD_SUMMARY_MODEL=claude-sonnet-4-20250514
AURABOOK_CLOUD_FIX_MODEL=claude-sonnet-4-20250514
AURABOOK_CLOUD_EMBEDDING_MODEL=gemini-embedding-001
```

Notes:

- `ollama`, `lmstudio`, and `qwen` use OpenAI-compatible HTTP endpoints.
- `claude` is supported for generation, but embeddings should use another provider.
- the repo swarm, janitor, and synthetic swarm generator all use the configured cloud provider settings.

## Frontend

Run the web app locally:

```bash
cd frontend
npm install
npm run dev
```

Build the Vercel-ready static bundle:

```bash
cd frontend
npm run build
```

The frontend falls back to mocked polling and replay data when dedicated dashboard or live-demo endpoints are not configured.

## Docker

Bring up both demo services:

```bash
docker compose up --build
```

## Live Setup

When Supabase and OpenAI are ready:

1. Fill in [`.env.example`](/E:/Codex/AIBook/.env.example) and [`aurabook-cloud/.env.example`](/E:/Codex/AIBook/aurabook-cloud/.env.example).
2. Apply the schema from [`aurabook-cloud/supabase.sql`](/E:/Codex/AIBook/aurabook-cloud/supabase.sql) or follow the migration order in [`aurabook-cloud/MIGRATIONS.md`](/E:/Codex/AIBook/aurabook-cloud/MIGRATIONS.md).
3. Disable demo mode in both services.
4. Point the local proxy at the deployed cloud API.

For local multi-provider routing, also set the relevant adapter credentials in [`.env.example`](/E:/Codex/AIBook/.env.example). The most common combinations are:

- OpenAI remote: `AURABOOK_UPSTREAM_BASE_URL=https://api.openai.com/v1`
- Ollama local: `AURABOOK_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1`
- LM Studio local: `AURABOOK_LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1`
- Qwen remote: `AURABOOK_QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Claude remote: `AURABOOK_ANTHROPIC_API_KEY=...`
- Gemini remote: `AURABOOK_GEMINI_API_KEY=...`
