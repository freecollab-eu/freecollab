#!/bin/sh
set -eu
fail() { printf '%s\n' 'FreeCollab Railway: invalid public host, private backend, port or trusted edge CIDR.' >&2; exit 1; }
case "${PORT:-}" in ''|*[!0-9]*) fail;; esac
[ "${#PORT}" -le 5 ] || fail
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || fail
case "${FREECOLLAB_PUBLIC_HOST:-}" in ''|*[!a-zA-Z0-9.-]*) fail;; esac
[ "${#FREECOLLAB_PUBLIC_HOST}" -le 253 ] || fail
printf '%s' "${FREECOLLAB_PUBLIC_HOST:-}" | grep -Eq '^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$' || fail
case "${FREECOLLAB_BACKEND_HOST:-}" in ''|*[!a-z0-9.-]*) fail;; esac
printf '%s' "${FREECOLLAB_BACKEND_HOST:-}" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.railway\.internal$' || fail
case "${FREECOLLAB_TRUSTED_EDGE_CIDR:-}" in ''|*[!a-fA-F0-9:./]*) fail;; esac
case "${FREECOLLAB_TRUSTED_EDGE_CIDR:-}" in '0.0.0.0/0'|'::/0') fail;; esac
printf '%s' "${FREECOLLAB_TRUSTED_EDGE_CIDR:-}" | grep -Eq '^[a-fA-F0-9:.]+/[0-9]{1,3}$' || fail
[ "${FREECOLLAB_TRUSTED_EDGE_CIDR##*/}" -gt 0 ] || fail
