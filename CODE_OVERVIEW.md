# AuraBook Code Overview

A complete reference for every piece of code in the AuraBook project. Organized by component, then by file. If someone asks "what does X do?" — the answer is here.

---

## Architecture at a Glance

```
Developer's AI Tool (Claude Code, Codex, Cursor, etc.)
        │
        ▼
┌─────────────────┐     ┌──────────────────────┐
│  AuraBook Client │────▶│  AuraBook Cloud Brain │
│  (open source)   │◀────│  (closed source API)  │
└────────┬────────┘     └──────────┬───────────┘
         │                         │
         ▼                         ▼
┌─────────────────┐     ┌──────────────────────┐
│   LocalBrain     │     │  PostgreSQL + pgvector│
│ (on-device DB)   │     │  (community data)     │
└─────────────────┘     └──────────────────────┘
```

**What it does**: AuraBook sits between developers and their AI coding tools. When an AI fails and eventually finds a fix, AuraBook captures that trajectory. When *another* developer hits the same error, AuraBook injects the community-sourced fix into the AI's context — preventing repeated failures.

---

## Backend (`Backend/`)

The closed-source cloud API. Stores trajectories, generates notes, and serves search results to clients.

### `config.py` — Central Settings

All environment-driven configuration for the backend. Uses `pydantic-settings` with the `AURABOOK_CLOUD_` prefix. Contains: database URLs, provider API keys, rate limiting params, OAuth creds, Stripe keys, search tuning thresholds, and model selection logic.

**Why it exists**: Single source of truth for all runtime config. Every service reads from this. The `resolve_summary_model()` / `resolve_fix_model()` methods handle the cascading logic of "which LLM should I use?" across 6 providers.

---

### `api/main.py` — FastAPI Application

The heart of the backend. Creates the FastAPI app, wires all dependencies, and defines core endpoints.

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/ingest` | Receive a failure trajectory from a client |
| `POST /v1/search` | Semantic search for relevant community notes |
| `POST /v1/prefetch` | Exact-match lookup by CDC hash (instant cache hit) |
| `POST /v1/feedback` | Client reports if a note was helpful or wrong |
| `GET /v1/dashboard/snapshot` | Full state for the frontend dashboard |
| `WS /v1/dashboard/ws` | WebSocket for live dashboard updates |
| `POST /v1/demo/compare` | Live A/B comparison (with vs. without AuraBook) |
| `POST /v1/admin/api-keys` | Create new API keys |
| `POST /v1/admin/prune` | Delete old data past retention window |

**Key design decisions**:
- `build_services()` is the factory — it detects which DB backend to use (postgres, supabase, or demo) and constructs the entire dependency graph
- `_authenticate()` chains three auth methods: session cookie → API key → HMAC signature
- After every ingest, a background task auto-runs the janitor to immediately convert the trajectory into a searchable note

---

### `api/auth.py` — API Key Authentication

Manages API keys with the `ab_` prefix. Keys are shown once at creation, then only the SHA-256 hash is stored. Supports two header styles: `Authorization: Bearer ab_...` or `X-API-Key: ab_...`.

**Why it exists**: Client SDKs and CI/CD pipelines need programmatic access. Human users authenticate via OAuth; machines use API keys.

---

### `api/oauth.py` — GitHub OAuth

Full OAuth2 authorization code flow. User clicks "Login with GitHub" → redirect to GitHub → callback exchanges code for token → upserts user in DB → signs a session cookie.

**Why it exists**: Humans need to log in to manage orgs, view the dashboard, and access team features. GitHub is the natural identity provider for developers.

---

### `api/rbac.py` — Role-Based Access Control

Enforces the role hierarchy: `owner > admin > member > viewer`. Every org-scoped endpoint calls `require_org_role(request, slug, minimum_role)` which:
1. Extracts the caller's user ID from session or API key
2. Looks up their membership in the target org
3. Compares their role rank against the required minimum
4. Returns 403 if insufficient

**Why it exists**: Teams need fine-grained permissions. A viewer shouldn't be able to delete members. An admin shouldn't be able to fire the owner.

---

### `api/orgs.py` — Organization Management

CRUD for organizations and their members. Endpoints: create org, get org, add/list/remove members, leaderboard. Org creation automatically makes the caller the owner. Member limits are enforced by plan tier.

**Why it exists**: Multi-tenancy. Teams need private workspaces where their trajectories are scoped and their leaderboard tracks contributions.

---

### `api/billing.py` — Stripe Integration

Three endpoints:
- `POST /v1/billing/checkout` — Creates a Stripe Checkout session for plan upgrades
- `POST /v1/billing/portal` — Opens Stripe's Customer Portal for subscription management
- `POST /v1/billing/webhook` — Handles Stripe events (payment success → upgrade plan, cancellation → downgrade to free)

**Why it exists**: Monetization. Free tier has limits (5 members, 1K trajectories). Paid plans unlock more capacity.

---

### `api/rate_limit.py` — Token-Bucket Rate Limiter

In-memory rate limiter keyed by client ID, API key prefix, or IP address. Uses a token-bucket algorithm with configurable RPM and burst capacity. Returns 429 with `Retry-After` header when exhausted.

**Why it exists**: Protects the API from abuse and noisy clients. Cheap enough to run in-process for a single-worker deployment.

---

### `api/errors.py` — Global Error Handling

Catches all exceptions and returns structured JSON. Validation errors (422) include field-level details. Unhandled exceptions (500) never leak stack traces to the client.

**Why it exists**: Production APIs must never expose internal state. Every error response must be machine-parseable.

---

### `api/observability.py` — Metrics & Logging

Three things:
1. `RequestLoggingMiddleware` — Logs every request with method, path, status, latency, and client ID. Also catches unhandled exceptions and returns structured 500 responses.
2. `Metrics` class — Counters for requests, errors, latency. Exposed at `/metrics` in Prometheus text format.
3. `/ready` endpoint — Health probe that checks DB connectivity and embedding provider status.

**Why it exists**: You can't operate what you can't observe. `/ready` is what Kubernetes/Docker health checks hit. `/metrics` is what Prometheus scrapes.

---

### `db/base.py` — Storage Interfaces

Two abstract base classes: `GraphStore` (trajectory CRUD, prefetch, processing state) and `VectorStore` (embeddings, semantic search, feedback). Every storage backend implements these.

**Why it exists**: Decouples the API from any specific database. You can swap Postgres for Supabase for an in-memory demo without changing a single endpoint.

---

### `db/postgres.py` — Direct Postgres Backend

Full implementation of GraphStore + VectorStore using `asyncpg` and `pgvector`. Manages connection pooling, schema creation, and all SQL queries. ~644 lines of production-grade async Postgres code.

**Why it exists**: The primary production backend. Direct Postgres with pgvector gives us sub-10ms semantic search at scale without paying for a third-party vector DB.

---

### `db/demo_store.py` — In-Memory Demo Backend

JSON-file-backed implementations of GraphStore and VectorStore. Includes `DeterministicEmbeddingService` that generates embeddings from token hashes (no API calls needed).

**Why it exists**: Lets you run the full backend locally without any database or API keys. Used in tests, demos, and the "one-click try it" experience.

---

### `db/graph.py` — Supabase Graph Store

GraphStore implementation using the Supabase REST API. Used when the backend is configured with `db_backend=supabase`.

**Why it exists**: Original backend before we built the direct Postgres layer. Still supported for users who prefer Supabase's managed experience.

---

### `db/vector.py` — Supabase Vector Store

VectorStore implementation via Supabase's vector functions (RPC calls).

---

### `db/supabase_client.py` — Supabase Client Provider

Manages the Supabase client lifecycle. Handles initialization and connection management.

---

### `db/multi_tenant.py` — Multi-Tenancy Schema

Contains the DDL for organizations, users, memberships, and leaderboard stats tables. Also defines `PLAN_LIMITS` — the capacity constraints for each tier (free: 5 members/1K trajectories, starter: 25/10K, team: 100/100K, enterprise: 10K/10M).

**Why it exists**: The data model for team features. Executed on startup to ensure tables exist.

---

### `models/schema.py` — Pydantic Data Models

All request/response shapes for the API. Key models:
- `TrajectoryIngestRequest` — What clients send when they capture a failure→fix
- `StoredTrajectory` — Internal representation with processing state
- `NoteRecord` — A summarized, embedded note ready for search
- `NoteSearchResult` — What the search endpoint returns
- `PrefetchMatch` — Exact-match cache hit
- `DashboardSnapshot` — Full frontend state

**Why it exists**: Type safety at the boundary. Pydantic validates every request and serializes every response.

---

### `services/embeddings.py` — Embedding Service

Thin async wrapper around the configured embedding provider. Handles retries with exponential backoff on transient errors.

**Why it exists**: Every search query and every note needs to be embedded into a vector. This is the single chokepoint for that operation.

---

### `services/retrieval.py` — Search Ranking

Builds the text representation of a trajectory for embedding, and implements `rerank_results()` which blends three signals: vector similarity (65%), lexical overlap (25%), and quality score (10%).

**Why it exists**: Raw vector search returns candidates. Reranking ensures the best result actually surfaces — not just the closest embedding but the most *useful* match.

---

### `services/privacy.py` — Path Sanitization

Strips absolute file paths from trajectories before storage. Replaces `C:\Users\manzi\project\src\app.py` with `<repo>/src/app.py`. Handles Windows, Unix, temp dirs, and home directories.

**Why it exists**: Privacy. Users don't want their filesystem layout stored in a community database. This runs on every ingest.

---

### `services/fingerprints.py` — Text Normalization

Normalizes free text for deduplication: lowercases, removes paths and line numbers, collapses whitespace, applies aliases (e.g., "SettingWithCopyWarning" → "pandas slice assignment warning").

**Why it exists**: Two developers hitting the "same" error will have different paths, line numbers, and formatting. Fingerprinting strips the noise so we can detect duplicates.

---

### `services/dashboard.py` — Dashboard Assembly

Transforms raw trajectories and notes into the dashboard's view model: problem tags, error clusters, attempt timelines, cascade chains. Groups related trajectories by fingerprint to show error propagation.

**Why it exists**: The frontend dashboard needs a rich, pre-computed view. This service does the heavy lifting so the API endpoint is just a thin pass-through.

---

### `services/live_compare.py` — Live A/B Demo

Runs two real LLM calls side-by-side: one without AuraBook context ("baseline") and one with an injected community note. Returns both outputs with token counts so the frontend can show the comparison.

**Why it exists**: The demo page. Shows prospects the concrete difference AuraBook makes — fewer tokens, faster resolution, no repeated mistakes.

---

### `services/model_providers.py` — LLM Provider Abstraction

Factory functions for both text generation and embedding providers. Supports OpenAI, Anthropic (Claude), Gemini, Ollama, LM Studio, and Qwen. Each provider gets its own class with retry logic and timeout handling.

**Why it exists**: Provider flexibility. Users pick their preferred LLM for summarization and embeddings. The code doesn't care which — it talks to an abstract interface.

---

### `services/model_catalog.py` — Model Discovery

Lists available models for a given API key by querying the provider's model endpoint. Used in the demo UI to populate model dropdowns.

---

### `services/idempotency.py` — Idempotency Store

Two implementations: `MemoryIdempotencyStore` (in-process dict with TTL) and `FileBackedIdempotencyStore` (JSON file for demo mode). Keyed by `X-AuraBook-Idempotency-Key` header.

**Why it exists**: Network retries shouldn't duplicate trajectories. If a client sends the same ingest twice with the same idempotency key, we return the cached response.

---

### `services/openai_failures.py` — Error Classification

Classifies OpenAI API errors into retryable vs. fatal categories. Detects rate limits, quota exhaustion, and invalid requests to decide whether to retry or halt.

---

### `worker/janitor.py` — Notes Janitor

Background job that:
1. Fetches unprocessed trajectories from the graph store
2. Summarizes each one into a 1-2 sentence "System Note" via LLM
3. Embeds the note text into a vector
4. Upserts the embedded note into the vector store
5. Marks the trajectory as processed

**Why it exists**: The core intelligence loop. Raw trajectories are verbose and unstructured. Notes are concise, embedded, and searchable. The janitor converts one to the other.

---

### `worker/summarizer.py` — Note Summarizer

LLM-powered summarization. Takes a full trajectory (goal, error, attempts, fix) and produces a single system note like: "System Note: Use httpx.AsyncClient instead of requests inside async functions — blocking HTTP clients freeze the event loop."

**Why it exists**: The quality of notes determines the quality of the product. The summarizer's prompt is carefully tuned to produce actionable, specific, verified advice — not generic tips.

---

### `scripts/swarm_generator.py` — Synthetic Trajectory Generator

Generates fake-but-realistic failure→fix trajectories using LLM calls. Has a library of 50+ failure cases across 8 domains (async Python, pandas, React, etc.). Supports concurrent generation and LLM-seeded discovery of new cases.

**Why it exists**: Cold-start problem. A new deployment has zero notes. The swarm generator bootstraps it with realistic content so early users get immediate value.

---

### `scripts/seed.py` — Cold-Start Pipeline

Orchestrates the full bootstrap: migrations → swarm generation → janitor processing → readiness verification. One command (`aurabook-cloud-seed --samples 50`) takes a fresh Postgres from empty to production-ready.

**Why it exists**: First-run experience. Admins need a single command to go from "I just deployed" to "it's serving useful search results."

---

### `scripts/repo_swarm_runner.py` — Repository Swarm

Generates trajectories from actual git repositories by analyzing commit history for bug-fix patterns.

---

### `scripts/sanitize_live_trajectories.py` — Retroactive Sanitization

Re-runs the privacy sanitizer on all stored trajectories. Used when the sanitization rules are updated and you need to clean old data.

---

### `scripts/retention_cleanup.py` — Data Retention

Deletes trajectories older than the configured retention window. Runs as a periodic job.

---

### `alembic/` — Database Migrations

| File | What it does |
|------|------|
| `env.py` | Reads `AURABOOK_CLOUD_DATABASE_URL` and configures Alembic's async connection |
| `versions/001_baseline_schema.py` | Core tables: trajectories, trajectory_nodes, trajectory_edges, trajectory_notes |
| `versions/002_api_keys.py` | api_keys table with hash index |
| `versions/003_multi_tenancy.py` | organizations, users, org_memberships, user_stats + org_id columns on existing tables |

**Why it exists**: Schema evolution without downtime. Alembic lets us add columns, tables, and indexes incrementally as the product grows.

---

### `tests/`

| File | Coverage |
|------|----------|
| `conftest.py` | Shared fakes: FakeGraphStore, FakeVectorStore, FakeEmbeddingService, FakeSummarizer |
| `test_api.py` | Endpoint behavior: ingest, search, prefetch, feedback, demo compare, HMAC auth |
| `test_integration.py` | Full-flow: error handling, rate limiting, auth chains, RBAC, ingest→search pipeline |
| `test_worker.py` | Janitor batch processing, error handling, retry behavior |
| `test_summarizer.py` | Note generation quality, retry logic, token counting |
| `test_retrieval.py` | Reranking, lexical overlap, normalization |
| `test_graph.py` | Graph store operations |
| `test_vector_store.py` | Vector store search and upsert |
| `test_model_providers.py` | Provider factory, error classification |
| `test_repo_swarm.py` | Repository analysis |

---

## Client (`Client/aurabook/`)

The open-source SDK that runs inside the developer's environment. Intercepts AI tool conversations, detects errors, and injects community context.

### `core/connectivity.py` — Data Router

Defines three connectivity modes and implements `DataRouter`:
- **local_only**: All data stays on-device via LocalBrain
- **hybrid**: Merges results from both LocalBrain and cloud, sorted by similarity with deduplication
- **cloud_only**: Only talks to the cloud backend

Also contains `LocalBrainClient` — an HTTP adapter that translates LocalBrain's API into the same shapes as `CloudBrainClient`.

**Why it exists**: Privacy flexibility. Some users/enterprises won't send data to the cloud. Others want community knowledge. The router abstracts this choice away from the rest of the codebase.

---

### `core/runtime.py` — Dependency Wiring

The `ApplicationRuntime` dataclass holds the fully assembled application: pipeline, orchestrator, watcher, all services. The `build_runtime()` factory constructs everything based on settings and repo policy.

**Why it exists**: Clean separation between "what are the pieces" and "how are they wired together." Tests can swap any component.

---

### `core/orchestrator.py` — Request Orchestrator

Coordinates the full lifecycle of intercepting an AI request: detect error signals → check prefetch cache → search community → inject context → capture trajectory on resolution.

---

### `core/pipeline.py` — Middleware Pipeline

Executes pre-middleware (PII scrubbing, community context injection) and post-middleware (trajectory capture) around each AI tool interaction.

---

### `services/community.py` — Community Context Service

The brain of the client. For each user prompt:
1. Checks if this exact error was seen before (prefetch by CDC hash)
2. If not, does a semantic search against the cloud
3. Decides the "gradient" of injection (pass-through, notes, guide, walkthrough, full)
4. Formats the community knowledge for injection into the AI's context

**Why it exists**: This is the core value prop. When it works, the AI gets the answer on the first try instead of repeating the community's past failures.

---

### `services/cloud_client.py` — Cloud API Client

HTTP client that talks to the Cloud Brain API. Methods: `prefetch()`, `search()`, `ingest()`, `submit_feedback()`.

---

### `services/memory.py` — Local Memory Store

Caches community notes locally to reduce API calls and provide offline access.

---

### `services/identity.py` — Client Identity

Generates and persists a unique client ID used for rate limiting and trajectory attribution.

---

### `services/ingest_queue.py` — Upload Queue

Queues failed trajectories for background upload. Handles retries when the cloud is unreachable.

---

### `services/preferences.py` — User Preferences & Repo Policy

Reads `.aurabook.yml` from the repository root to determine per-project settings (cloud enabled? which mode? custom thresholds?).

---

### `middleware/community.py` — Context Injection Middleware

Pre-middleware that calls the CommunityContextService and injects any found notes into the AI's system prompt.

---

### `middleware/trajectory.py` — Trajectory Capture Middleware

Post-middleware that detects when a conversation resolves a coding error and captures the full trajectory (goal, error, attempts, fix) for upload.

---

### `middleware/pii.py` — PII Scrubber

Pre-middleware that strips sensitive information before it leaves the client.

---

### `watcher/prefetch.py` — Ghost Prefetch Watcher

Background service that monitors file changes, computes CDC hashes of the current code state, and pre-fetches matching notes from the cloud. When the developer eventually asks the AI about that file, the note is already cached.

**Why it exists**: Latency. Prefetching means zero wait time when the user triggers a search.

---

### `utils/config.py` — Client Settings

Client-side configuration: cloud URL, connectivity mode, LocalBrain URL, timeouts, feature flags.

---

## Client/LocalBrain (`Client/LocalBrain/`)

On-device storage and indexing. Runs as a local HTTP service.

### `src/core/`

| File | Purpose |
|------|---------|
| `orchestrator.py` | Coordinates file watching, parsing, and indexing |
| `pipeline.py` | Processing pipeline for ingested documents |
| `registry.py` | Registry of available parsers and handlers |
| `scheduler.py` | Background task scheduling (re-indexing, cleanup) |
| `state.py` | Application state management |
| `models.py` | Internal data models |

### `src/gateway/`

HTTP API server that the client talks to. Provides search, ingest, and health endpoints locally.

### `src/vault/`

| File | Purpose |
|------|---------|
| `lance_engine.py` | LanceDB-backed vector storage for local embeddings |
| `sqlite_engine.py` | SQLite metadata store for documents and chunks |
| `ref_counting.py` | Reference counting for garbage collection |
| `schema.py` | Database schema definitions |
| `subscriptions.py` | Pub/sub for real-time updates |

### `src/chunking/`

| File | Purpose |
|------|---------|
| `cdc.py` | Content-Defined Chunking — splits text at natural boundaries |
| `dedup.py` | Deduplication of chunks by content hash |
| `fingerprint.py` | Content fingerprinting for change detection |

### `src/parsers/`

File format parsers: text, PDF, images, audio, Office docs, archive files, and AI conversation exports (ChatGPT, Claude, Gemini).

### `src/ingress/`

| File | Purpose |
|------|---------|
| `watcher.py` | Filesystem watcher for new/changed files |
| `identity.py` | Document identity tracking |
| `scope_gate.py` | Decides which files to index (respects .gitignore etc.) |

### `src/janitor/`

Background maintenance: re-indexing stale content, syncing with remote, tombstoning deleted documents.

---

## Client/Router (`Client/Router/`)

AI request router that sits between the developer's tool and the LLM provider. Applies middleware (context injection, logging, early exit).

### `src/adapters/`

Provider-specific HTTP adapters: OpenAI, Ollama, LM Studio, Gemini. Each translates the generic request format into the provider's API shape.

### `src/middleware/`

| File | Purpose |
|------|---------|
| `router.py` | Routes requests to the correct provider adapter |
| `early_exit.py` | Short-circuits requests when a cached answer exists |
| `logger.py` | Logs all requests/responses for debugging |

### `src/core/`

Pipeline orchestration, similar to the client's core — registry, state management, models.

### `src/gateway/`

HTTP server that accepts OpenAI-compatible API requests and proxies them through the middleware pipeline.

---

## Frontend (`Frontend/src/`)

React + Vite + Tailwind SPA. Shows the community knowledge dashboard, live demo, and team management.

### Pages

| File | What it renders |
|------|----------------|
| `LandingPage.tsx` | Marketing page with value prop, how-it-works, CTA |
| `DemoPage.tsx` | Live A/B comparison — type a coding question, see baseline vs. AuraBook side-by-side |
| `DashboardPage.tsx` | Knowledge feed: notes, error clusters, activity ticker |
| `ErrorIssuePage.tsx` | Deep dive into an error cluster: full cascade chain, all attempts |
| `NoteIssuePage.tsx` | Single note detail: source trajectory, rating history, comments |
| `OrgsListPage.tsx` | List/create organizations |
| `OrgPage.tsx` | Org management: members, leaderboard, settings, billing |

### Services

| File | Purpose |
|------|---------|
| `dashboardTransport.ts` | Fetches snapshots from the API, normalizes snake_case→camelCase, handles WebSocket and polling fallback |
| `demoRunner.ts` | Drives the live demo: sends prompts, streams responses |
| `orgApi.ts` | API client for org CRUD, auth, billing |
| `pyodideRunner.ts` | Runs Python in the browser via Pyodide (for code demos) |

### Store (Zustand)

| File | Purpose |
|------|---------|
| `appStore.ts` | Global UI state: mobile menu, theme |
| `dashboardStore.ts` | Dashboard state: notes, clusters, activities, connection status |
| `demoStore.ts` | Demo page state: prompt, responses, loading |

### Components

| Directory | Contents |
|-----------|----------|
| `layout/` | AppShell (routes), Navbar, ConnectionStatus indicator, Footer |
| `dashboard/` | FeedCard, ActivityTicker, DashboardSidebar, NoteDetailPanel |
| `demo/` | TerminalPanel (typewriter animation), TypewriterLine |
| `errors/` | ErrorChainMap (cascade visualization), ErrorClusterDetailPanel |
| `effects/` | NetworkBackdrop (animated background) |
| `common/` | Button, SectionHeading, SkeletonCard |

---

## Extensions (`Extensions/vscode/`)

VS Code extension that connects the editor to AuraBook.

| File | Purpose |
|------|---------|
| `extension.ts` | Activation entry point. Registers commands (showDashboard, setup, toggleMode), starts health polling |
| `client.ts` | HTTP client that checks LocalBrain and Cloud Brain health endpoints |
| `statusBar.ts` | Status bar item showing connected/disconnected/syncing state with color coding |
| `setupWizard.ts` | Webview panel that auto-detects installed AI tools (Claude Code, Codex, Cursor, Aider, Continue) and guides configuration |
| `package.json` | Extension manifest: commands, configuration schema, activation events |

---

## Infrastructure & Deployment

### `.github/workflows/ci.yml` — CI Pipeline

Runs on every PR and push to main:
- Backend Tests (Python 3.11)
- Client Tests (Python 3.11)
- LocalBrain Tests (Python 3.12)
- Router Tests (Python 3.12)
- Docker Build verification

Also exposes `workflow_call` so the deploy pipeline can gate on it.

### `.github/workflows/deploy.yml` — CD Pipeline

Triggered on push to main:
1. Runs full CI (test gate)
2. Builds Docker image, pushes to GitHub Container Registry
3. Runs Alembic migrations against production DB
4. SSH deploys: pulls new image, restarts backend, health checks

### `docker-compose.yml` — Local Development Stack

Postgres (pgvector) + Backend API. For running the full backend locally with `docker compose up`.

### `deploy/docker-compose.prod.yml` — Production Stack

Full production deployment: Caddy (auto-TLS) + Frontend (nginx) + Backend + Worker + Postgres.

### `deploy/Caddyfile` — Reverse Proxy Config

Routes `/v1/*` and `/auth/*` to the backend, everything else to the frontend SPA. Adds security headers (HSTS, X-Frame-Options, etc.) and gzip compression.

### `deploy/Dockerfile.frontend` — Frontend Build

Multi-stage: Node.js build → nginx serving static files with SPA fallback.

### `deploy/setup.sh` — First-Time Setup

One-command production bootstrap: checks prerequisites, generates secrets, creates .env, starts Docker Compose, runs migrations, verifies health.

### `deploy/.env.example` — Production Env Template

Documents every required environment variable with comments.

---

## Configuration Files

| File | Purpose |
|------|---------|
| `Backend/pyproject.toml` | Python package definition, dependencies, script entry points, optional extras (postgres, billing, supabase) |
| `Backend/alembic.ini` | Alembic config — points to migrations directory, sets naming conventions |
| `Client/pyproject.toml` | Client SDK package with dev dependencies |
| `Client/LocalBrain/pyproject.toml` | LocalBrain package (requires Python 3.12+) |
| `Client/Router/pyproject.toml` | Router package (requires Python 3.12+) |
| `Frontend/package.json` | React app dependencies: React 19, react-router, recharts, zustand, Tailwind |
| `Frontend/vite.config.ts` | Vite bundler config |
| `Frontend/vercel.json` | SPA routing for Vercel deployment |
| `Frontend/tailwind.config.ts` | Custom colors (night, surface, accent), animations |
| `Extensions/vscode/package.json` | VS Code extension manifest |

---

## Data Flow: End-to-End

### When a developer hits an error and their AI eventually fixes it:

```
1. AI tool (Cursor/Claude) resolves an error
2. Client middleware detects the fix (trajectory capture)
3. Client sanitizes paths (privacy.py)
4. Client uploads trajectory → POST /v1/ingest
5. Backend validates, stores in graph store
6. Background task: janitor summarizes → "System Note: ..."
7. Janitor embeds the note → vector store
8. Note is now searchable by the community
```

### When another developer hits the same error:

```
1. AI tool sends a prompt with error context
2. Client pre-middleware checks prefetch cache (CDC hash)
3. If miss → semantic search → POST /v1/search
4. Backend embeds query, searches pgvector, reranks results
5. Returns top matching notes
6. Client injects note into AI's system prompt
7. AI gets it right on the first try
```

---

## Key Design Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Abstract base classes | `db/base.py` | Swap storage backends without changing API code |
| Factory functions | `build_services()`, `build_runtime()` | Construct the right dependencies based on config |
| Middleware pipeline | Client core | Composable pre/post processing of every AI interaction |
| Token bucket | `rate_limit.py` | Simple, effective rate limiting without external state |
| Background tasks | Janitor after ingest | Don't make the user wait for note generation |
| Idempotency keys | Ingest endpoint | Network retries are safe |
| CDC hashing | Prefetch watcher | Instant exact-match lookups without embedding |
| Signed cookies | OAuth sessions | Stateless auth without a session store |
| Webhook-driven state | Stripe billing | Plan changes happen in real-time via events |

---

## Test Strategy

- **Unit tests**: Fake stores, mocked providers — fast, isolated, 130+ tests
- **Integration tests**: Full ASGI client against real app with fake backends — tests auth chains, error handling, rate limiting
- **CI**: 4 parallel jobs + Docker build on every PR
- **No flaky tests**: All async, no sleeps, no network calls in tests
