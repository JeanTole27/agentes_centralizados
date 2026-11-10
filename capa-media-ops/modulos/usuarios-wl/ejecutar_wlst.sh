#!/bin/bash
# ============================================================
# Wrapper WLST generico (Capa Media Ops - modulo usuarios-wl)
# Lee los parametros del dominio desde dominio.env (generado
# por el nodo central). Ya no hay IPs ni rutas fijas en el codigo.
# ============================================================
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${BASE_DIR}/dominio.env" || { echo "[ERROR] falta dominio.env"; exit 2; }

TRUSTSTORE="${BASE_DIR}/${DOMINIO_ID}_trust.jks"
TRUST_PASS="${TRUST_PASS:-changeit}"   # el truststore solo contiene certificados publicos

export WLST_PROPERTIES="-Dweblogic.security.SSL.ignoreHostnameVerification=true -Dweblogic.security.TrustKeyStore=CustomTrust -Dweblogic.security.CustomTrustKeyStoreFileName=${TRUSTSTORE} -Dweblogic.security.CustomTrustKeyStoreType=JKS -Dweblogic.security.CustomTrustKeyStorePassPhrase=${TRUST_PASS} -Djdk.tls.client.protocols=${TLS_PROTOCOLOS} -Dweblogic.security.SSL.minimumProtocolVersion=${TLS_MINIMO}"
export ADMIN_URL SECURITY_REALM DOMINIO_ID
export CMOPS_BASE_DIR="${BASE_DIR}"

if [ -z "$1" ]; then
    echo "Uso: $0 <script.py>"
    exit 1
fi
[ -x "$WLST" ] || { echo "[ERROR] WLST no encontrado: $WLST"; exit 2; }

"$WLST" "${BASE_DIR}/$1"
