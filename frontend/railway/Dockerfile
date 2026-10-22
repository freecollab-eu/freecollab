# syntax=docker/dockerfile:1
# Railway root directory/build context: frontend; keep VITE_API_URL unset.
FROM node:24-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --strict-ssl=true --no-audit --no-fund
COPY . .
RUN npm run build && npm run build:widget
FROM nginx:1.28-alpine
ENV PORT=8080 FREECOLLAB_TRUSTED_EDGE_CIDR=127.0.0.1/32 NGINX_ENVSUBST_FILTER="^(PORT|FREECOLLAB_PUBLIC_HOST|FREECOLLAB_BACKEND_HOST|FREECOLLAB_TRUSTED_EDGE_CIDR)$"
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=builder /app/dist-widget/widget.iife.js /usr/share/nginx/html/widget.iife.js
COPY railway/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 railway/19-validate-routing.sh /docker-entrypoint.d/19-validate-routing.sh
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
