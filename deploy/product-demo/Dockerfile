# Dedicated, disposable example installation. Never attach customer volumes.
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --strict-ssl=true --no-audit --no-fund
COPY frontend/ ./
RUN npm run build:demo

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 DEMO_INSTANCE=true
RUN apt-get update && apt-get install -y --no-install-recommends nginx ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend/app ./app
COPY deploy/product-demo/run.py deploy/product-demo/nginx.conf.template /opt/freecollab-demo/
COPY --from=frontend /build/frontend/dist-demo /srv/product-demo
RUN useradd --uid 10001 --create-home freecollab \
    && chmod -R a+rX /srv/product-demo /opt/freecollab-demo
USER freecollab
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["python", "/opt/freecollab-demo/run.py"]
