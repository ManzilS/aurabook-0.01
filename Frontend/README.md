# AuraBook Frontend

React, TypeScript, Tailwind, Zustand, Axios, Recharts, and React Router frontend for the AuraBook landing page, replay demo, and live knowledge dashboard.

## Local Development

```bash
cd Frontend
npm install
npm run dev
```

The app runs on `http://127.0.0.1:5173`.

## Build

```bash
cd Frontend
npm run typecheck
npm run build
```

## Environment

Copy `.env.example` to `.env` if you want to point the dashboard or live demo at real services.

```env
VITE_AURABOOK_API_BASE_URL=http://127.0.0.1:8000
VITE_AURABOOK_DASHBOARD_API_URL=
VITE_AURABOOK_WS_URL=
VITE_AURABOOK_GITHUB_URL=https://github.com/ManzilS
VITE_AURABOOK_LINKEDIN_URL=https://www.linkedin.com
VITE_AURABOOK_LIVE_COMPARE_URL=
```

When the dashboard endpoint is unavailable, the UI displays an error and retries. It never substitutes sample notes. The scripted replay is separate from the live provider comparison.

## Vercel

This app is Vercel-ready as a static SPA:

- build command: `npm run build`
- output directory: `dist`
- root directory: `Frontend`

`vercel.json` rewrites all routes to `index.html` so React Router works in production.

See [deployment instructions](../DEPLOYMENT.md) for backend configuration, browser tests, and current limitations.
