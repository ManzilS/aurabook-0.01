# AuraBook Frontend

React, TypeScript, Tailwind, Zustand, Axios, Recharts, and React Router frontend for the AuraBook landing page, replay demo, and live knowledge dashboard.

## Local Development

```bash
cd frontend
npm install
npm run dev
```

The app runs on `http://127.0.0.1:5173`.

## Build

```bash
cd frontend
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

When the dashboard endpoints are not available, the UI falls back to mocked live polling so the product story still works for demos.

## Vercel

This app is Vercel-ready as a static SPA:

- build command: `npm run build`
- output directory: `dist`
- root directory: `frontend`

`vercel.json` rewrites all routes to `index.html` so React Router works in production.
