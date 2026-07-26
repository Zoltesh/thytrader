# 0001: SvelteKit frontend

- Status: Accepted
- Date: 2026-07-26

## Context

ThyTrader's primary UI is an authenticated-style application dashboard with live balances, market data, charts, strategy controls, backtest progress, and order/risk state. It is not primarily a content site with isolated interactive islands. The frontend should remain lightweight and responsive without assembling unnecessary framework layers.

## Decision

Use SvelteKit with Svelte 5 and strict TypeScript for the application frontend. FastAPI remains the business/API authority. Use REST for commands and queries and a ThyTrader WebSocket for normalized live events.

Astro may be considered later for a separate public marketing or documentation site, but it will not wrap the trading application initially.

## Consequences

- The application gets routing, layouts, loading/error behavior, and flexible rendering in one framework.
- Svelte's compiled reactivity is well suited to live dashboard state.
- The team must define clean browser/server state boundaries and WebSocket recovery behavior.
- Frontend and backend contracts should be typed and tested; generate a TypeScript client from OpenAPI where practical.
- Adding Astro to the core app requires a new decision with a demonstrated benefit.

## Alternatives considered

- **Astro with Svelte islands:** excellent for content-centric sites, but adds boundaries and hydration decisions to a highly interactive dashboard.
- **React/Next.js:** mature ecosystem, but carries more runtime/framework complexity than needed for this local-first application.
- **Static SPA without SvelteKit:** initially small, but gives up useful routing, loading, error, and deployment conventions.
