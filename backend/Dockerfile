# syntax=docker/dockerfile:1
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r requirements.lock
RUN useradd --uid 10001 --create-home freecollab \
    && mkdir -p /data/files /data/mail && chmod 0700 /data/mail \
    && chown -R freecollab:freecollab /data /app
COPY --chown=freecollab:freecollab app ./app
USER freecollab
EXPOSE 8000
CMD ["uvicorn", "app.main:socket_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
