#!/bin/sh
set -eu
case "${PORT:-}" in ''|*[!0-9]*) echo 'PORT must be an integer between 1024 and 65535.' >&2; exit 1;; esac
if [ "${#PORT}" -gt 5 ]; then echo 'PORT must be an integer between 1024 and 65535.' >&2; exit 1; fi
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  echo 'PORT must be an integer between 1024 and 65535.' >&2
  exit 1
fi
