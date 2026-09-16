# Deliberate production web image: builds the SvelteKit app with adapter-node and
# serves the compiled output through server.js, which proxies /api the same way the
# Vite dev server does for local iteration (audit F26 / ADR 0063). This replaces the
# prior image, which ran the Vite dev server as the normal Compose web process.
FROM node:22.23.1-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build

FROM node:22.23.1-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/build ./build
COPY server.js ./server.js

EXPOSE 5175

CMD ["node", "server.js"]
