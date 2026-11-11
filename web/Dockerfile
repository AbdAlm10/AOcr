FROM node:22-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV API_INTERNAL_URL=http://api:8791
RUN npm run build
EXPOSE 8787
CMD ["npx", "next", "start", "--hostname", "0.0.0.0", "--port", "8787"]
