# ThyTrader Web

The SvelteKit/Svelte 5 frontend for ThyTrader. It uses strict TypeScript and proxies `/api`
requests to the local FastAPI process at `127.0.0.1:8200` during development.

Use Node `22.23.1` via `nvm use` from the repository root. Node 24 is not currently supported:
its Rolldown native binding has produced `SIGBUS` failures on Linux/WSL2 during Vite startup.

From the repository root, start the API first:

```bash
uv run thytrader-api
```

Then start the web application:

```bash
cd web
npm ci
npm run dev
```

The development server uses `http://127.0.0.1:5175`; ports `5173` and `5174` are intentionally
avoided. Vite fails explicitly rather than silently selecting another port if `5175` is occupied.

Run all frontend quality gates with:

```bash
npm run lint
npm run check
npm run test
npm run build
```

Playwright starts real FastAPI and SvelteKit processes for the unmocked demo integration test.
