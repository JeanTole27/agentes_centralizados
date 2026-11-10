#!/bin/bash
# ============================================================
# Recibe por STDIN las acciones (ACCION|USUARIO|PASSWORD|DESCRIPCION|GRUPO),
# las guarda en un temporal con permisos 600, ejecuta la gestion de usuarios
# y borra el temporal (shred). Las claves nunca quedan en disco.
# ============================================================
set -u
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 077
TMP="$(mktemp "${BASE_DIR}/.acciones.XXXXXX")" || exit 2
limpiar() { shred -u "$TMP" 2>/dev/null || rm -f "$TMP"; }
trap limpiar EXIT
cat > "$TMP"
echo "[INFO]  $(grep -c . "$TMP") accion(es) recibidas del nodo central"
ARCHIVO_USUARIOS="$TMP" bash "${BASE_DIR}/ejecutar_wlst.sh" gestionar_usuarios_weblogic.py
