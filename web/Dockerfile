# Production web image. `@sveltejs/adapter-node` builds a self-contained Node
# server; the /api catch-all route reverse-proxies to the API inside the Compose
# network. No Vite development server runs in this image.
FROM node:22.23.1-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build

FROM node:22.23.1-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/build ./build

EXPOSE 5175

CMD ["node", "build/index.js"]
