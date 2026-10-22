FROM node:24-slim

ENV NODE_ENV=production \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_AUTH_DIR=/data/auth

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src

EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD node -e "fetch('http://127.0.0.1:3001/health').then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"
CMD ["node", "src/index.ts"]
