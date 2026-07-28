# Local full-stack web image. Vite serves the SvelteKit development application
# and proxies API calls inside the Compose network.
FROM node:22.23.1-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./

EXPOSE 5175

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
