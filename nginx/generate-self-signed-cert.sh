#!/usr/bin/env bash
# Generate a self-signed TLS cert + key for local HTTPS testing of the nginx
# proxy. NOT for production -- use a real CA (e.g. Let's Encrypt) there.
#
# The stack serves two vhosts (session manager + raw OpenAI). By default this
# writes ONE shared cert (server.crt/server.key) covering both hostnames via SAN.
# Pass --separate to instead write a distinct pair per vhost:
#   sm.crt/sm.key (session manager) and openai.crt/openai.key (raw OpenAI).
# In that case set the NGINX_SM_TLS_* / NGINX_OPENAI_TLS_* vars in .env to point
# at them.
#
# Usage:
#   ./nginx/generate-self-signed-cert.sh [--separate] [sm_server_name] [openai_server_name]
# Defaults: localhost and openai.localhost. Output goes to ./nginx/certs/.

set -euo pipefail

SEPARATE=0
if [[ "${1:-}" == "--separate" ]]; then
    SEPARATE=1
    shift
fi

SM_SERVER_NAME="${1:-localhost}"
OPENAI_SERVER_NAME="${2:-openai.localhost}"
CERT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"

mkdir -p "${CERT_DIR}"

# gen_cert <crt> <key> <cn> <san_csv>
gen_cert() {
    local crt="$1" key="$2" cn="$3" san="$4"
    if [[ -f "${crt}" || -f "${key}" ]]; then
        echo "Refusing to overwrite existing ${crt} / ${key}." >&2
        echo "Remove them first if you want to regenerate." >&2
        exit 1
    fi
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "${key}" \
        -out "${crt}" \
        -days 365 \
        -subj "/CN=${cn}" \
        -addext "subjectAltName=${san}"
    chmod 600 "${key}"
    echo "Wrote:"
    echo "  ${crt}"
    echo "  ${key}"
}

if [[ "${SEPARATE}" -eq 1 ]]; then
    gen_cert "${CERT_DIR}/sm.crt" "${CERT_DIR}/sm.key" "${SM_SERVER_NAME}" \
        "DNS:${SM_SERVER_NAME},DNS:localhost,IP:127.0.0.1"
    gen_cert "${CERT_DIR}/openai.crt" "${CERT_DIR}/openai.key" "${OPENAI_SERVER_NAME}" \
        "DNS:${OPENAI_SERVER_NAME},DNS:localhost,IP:127.0.0.1"
    echo "Separate certs. Set in .env:"
    echo "  NGINX_SM_TLS_CERT_FILE=/etc/nginx/certs/sm.crt"
    echo "  NGINX_SM_TLS_KEY_FILE=/etc/nginx/certs/sm.key"
    echo "  NGINX_OPENAI_TLS_CERT_FILE=/etc/nginx/certs/openai.crt"
    echo "  NGINX_OPENAI_TLS_KEY_FILE=/etc/nginx/certs/openai.key"
else
    gen_cert "${CERT_DIR}/server.crt" "${CERT_DIR}/server.key" "${SM_SERVER_NAME}" \
        "DNS:${SM_SERVER_NAME},DNS:${OPENAI_SERVER_NAME},DNS:localhost,IP:127.0.0.1"
    echo "Shared cert. Server names: ${SM_SERVER_NAME}, ${OPENAI_SERVER_NAME}"
fi
