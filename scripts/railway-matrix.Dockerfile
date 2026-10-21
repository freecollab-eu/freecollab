# Railway root directory/build context: scripts. Retain this service's /data.
FROM ghcr.io/element-hq/synapse@sha256:97665cd4539dcf107c69fb6afdb720e1d1c9c09fb2b5701fb13d6c33b22cd8f0
COPY secure-matrix-config.py /opt/freecollab/secure-matrix-config.py
ENV SYNAPSE_CONFIG_PATH=/data/homeserver.yaml PORT=8008
EXPOSE 8008
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["python /opt/freecollab/secure-matrix-config.py && exec /start.py"]
