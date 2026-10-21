# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --strict-ssl=true --no-audit --no-fund
COPY . .
ARG VITE_API_URL
ARG VITE_WHISPER_MODEL_BASE_PATH
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_WHISPER_MODEL_BASE_PATH=$VITE_WHISPER_MODEL_BASE_PATH
RUN npm run build && npm run build:widget \
    && if [ -n "$VITE_API_URL" ]; then cp nginx.static.conf /tmp/nginx.conf; \
       else cp nginx.conf /tmp/nginx.conf; fi

FROM nginx:1.28-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=builder /app/dist-widget/widget.iife.js /usr/share/nginx/html/widget.iife.js
COPY --from=builder /tmp/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
