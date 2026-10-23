# syntax=docker/dockerfile:1
# Build context is frontend/. This image serves only the public website/demo.
FROM node:24-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --strict-ssl=true --no-audit --no-fund
COPY . .
RUN npx vite build --config vite.website.config.ts \
    && find dist-website -type d -exec chmod 0755 {} + \
    && find dist-website -type f -exec chmod 0644 {} +

FROM nginx:1.28-alpine
ENV PORT=8080 NGINX_ENVSUBST_FILTER="^PORT$"
COPY --from=builder /app/dist-website /usr/share/nginx/html
COPY website/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 website/19-validate-port.sh /docker-entrypoint.d/19-validate-port.sh
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -Y off -q -O /dev/null "http://127.0.0.1:${PORT}/healthz" || exit 1
CMD ["nginx", "-g", "daemon off;"]
