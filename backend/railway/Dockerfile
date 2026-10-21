# syntax=docker/dockerfile:1
# Railway build context/root directory: backend. API and workers share /data.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r requirements.lock
RUN groupadd --gid 10001 freecollab && useradd --uid 10001 --gid 10001 --create-home freecollab \
    && mkdir -p /data/files /data/mail /data/security /opt/freecollab \
    && chown -R 10001:10001 /data /app && chmod 0700 /data /data/files /data/mail /data/security
COPY --chown=10001:10001 app ./app
COPY railway/runtime.py /opt/freecollab/railway-runtime.py
# The bounded launcher initializes the four known volume directories, then
# permanently drops to uid/gid10001 before starting any application process.
USER root
EXPOSE 8000
CMD ["python", "/opt/freecollab/railway-runtime.py"]
