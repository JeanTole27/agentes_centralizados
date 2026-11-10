#!/bin/bash
# ============================================================
# Prepara un dominio para la gestion de usuarios (una vez por dominio
# o al rotar la clave del admin):
#   1) extrae el certificado del AdminServer y crea <DOMINIO>_trust.jks
#   2) genera credenciales cifradas (.wl_userconfig / .wl_keyfile)
#
# El usuario y la clave del admin llegan por STDIN (linea 1 y 2),
# enviados por el nodo central. Nunca quedan en disco ni en argumentos.
# ============================================================
set -u
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${BASE_DIR}/dominio.env" || { echo "[ERROR] falta dominio.env"; exit 2; }
umask 077

IFS= read -r WL_ADMIN_USER
IFS= read -r WL_ADMIN_PASS
export WL_ADMIN_USER WL_ADMIN_PASS
[ -n "$WL_ADMIN_PASS" ] || { echo "[ERROR] no se recibio la clave del admin"; exit 2; }

CRT="${BASE_DIR}/${DOMINIO_ID}_server.crt"
JKS="${BASE_DIR}/${DOMINIO_ID}_trust.jks"
TRUST_PASS="${TRUST_PASS:-changeit}"

echo "[1] Extrayendo certificado de ${ADMIN_IP}:${ADMIN_PUERTO}..."
echo | openssl s_client -connect "${ADMIN_IP}:${ADMIN_PUERTO}" 2>/dev/null | openssl x509 > "$CRT"
if [ ! -s "$CRT" ]; then
    echo "    ERROR: no se pudo extraer el certificado"
    exit 1
fi
echo "    Certificado extraido OK"
openssl x509 -in "$CRT" -noout -subject -enddate 2>/dev/null | sed 's/^/    /'

echo "[2] Creando truststore $(basename "$JKS")..."
rm -f "$JKS"
"$KEYTOOL" -importcert -noprompt -trustcacerts -alias "${DOMINIO_ID}_adminserver" \
    -file "$CRT" -keystore "$JKS" -storepass "$TRUST_PASS" >/dev/null || { echo "    ERROR keytool"; exit 1; }
echo "    Truststore creado OK"

echo "[3] Generando credenciales cifradas..."
# El resto de STDIN ("y") responde la confirmacion de storeUserConfig si la pide.
bash "${BASE_DIR}/ejecutar_wlst.sh" generar_credenciales_weblogic.py
rc=$?
unset WL_ADMIN_PASS
chmod 600 "${BASE_DIR}/.wl_userconfig" "${BASE_DIR}/.wl_keyfile" 2>/dev/null
exit $rc
