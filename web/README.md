# ThyTrader Web

The SvelteKit/Svelte 5 frontend for ThyTrader. It uses strict TypeScript and proxies `/api`
requests to the local FastAPI process at `127.0.0.1:8200` during development.

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
