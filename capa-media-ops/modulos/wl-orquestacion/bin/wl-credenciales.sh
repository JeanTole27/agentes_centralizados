#!/bin/bash
# =============================================================================
# wl-credenciales.sh - Credenciales cifradas por dominio SIN usuarios fijos
# -----------------------------------------------------------------------------
# Reemplazo NO interactivo de wl-bootstrap.sh / wl-bootstrap-admin.sh para el
# nodo central (Capa Media Ops). Ninguna credencial ni usuario esta en el codigo:
#
#   ./wl-credenciales.sh --verificar [dom1,dom2]
#        Informa por dominio: si existe el directorio, quien es el PROPIETARIO del
#        dominio en el SO, si coincide con el usuario que ejecuta, y si existen las
#        credenciales cifradas de Node Manager y de consola.
#
#   ./wl-credenciales.sh <dominio>   (usuario/clave por STDIN, 4 lineas)
#        linea 1: usuario Node Manager    linea 2: clave Node Manager
#        linea 3: usuario consola (vacio = no generar)   linea 4: clave consola
#        Genera <DOMAIN_HOME>/nmsecure/{nmUserConfig,nmUserKey}.secure y,
#        opcionalmente, {adminUserConfig,adminUserKey}.secure.
#        Las claves pasan a WLST por variables de entorno (no se escriben en disco
#        ni se interpolan en codigo, a diferencia del bootstrap original).
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/wl-config.sh"
YO="$(id -un)"

propietario() { stat -c %U "$1" 2>/dev/null || echo "?"; }

if [[ "${1:-}" == "--verificar" ]]; then
  doms=()
  if [[ -n "${2:-}" ]]; then IFS=',' read -ra doms <<< "$2"; else mapfile -t doms < <(wl_all_domains); fi
  echo "Usuario que ejecuta: ${YO}  |  MW_HOME: ${MW_HOME}"
  for d in "${doms[@]}"; do
    if ! wl_domain_exists "$d"; then
      echo "DOMINIO=$d DIR=NO_EXISTE PROPIETARIO=- COINCIDE=- NM_CRED=- ADMIN_CRED=-"
      continue
    fi
    dh="$(wl_domain_home "$d")"
    p="$(propietario "$dh")"
    nm=FALTA; ad=FALTA
    [[ -f "$dh/nmsecure/nmUserConfig.secure" && -f "$dh/nmsecure/nmUserKey.secure" ]] && nm=OK
    [[ -f "$dh/nmsecure/adminUserConfig.secure" && -f "$dh/nmsecure/adminUserKey.secure" ]] && ad=OK
    echo "DOMINIO=$d DIR=OK PROPIETARIO=$p COINCIDE=$([[ "$p" == "$YO" ]] && echo SI || echo NO) NM_CRED=$nm ADMIN_CRED=$ad"
  done
  exit 0
fi

dom="${1:-}"
wl_domain_exists "$dom" || { echo "ERROR: dominio no existe o no esta configurado: $dom"; exit 2; }
dh="$(wl_domain_home "$dom")"
p="$(propietario "$dh")"
if [[ "$p" != "$YO" ]]; then
  echo "ERROR: el dominio $dom pertenece a '$p' y se esta ejecutando como '$YO'."
  echo "       Corrija usuario_so de la instalacion en inventario/configuracion_modulos.json."
  exit 3
fi

IFS= read -r NMU; IFS= read -r NMP; IFS= read -r ADU; IFS= read -r ADP
[[ -n "$NMU" && -n "$NMP" ]] || { echo "ERROR: faltan usuario/clave del Node Manager"; exit 2; }
umask 077
mkdir -p "$dh/nmsecure" || exit 1
rc=0

echo "[$dom] Registrando credencial de Node Manager (usuario ${NMU}, puerto ${DOM_NM_PORT[$dom]} ${DOM_NM_TYPE[$dom]})..."
out="$(CMOPS_U="$NMU" CMOPS_P="$NMP" bash -c 'printf "y\ny\n" | "$@"' _ "$WLST" "${SCRIPT_DIR}/wl-store-cred.py" nm \
        "${DOM_DOMAIN_NAME[$dom]}" "$dh" "$NM_HOST" "${DOM_NM_PORT[$dom]}" "${DOM_NM_TYPE[$dom]}" 2>/dev/null | grep -E '^CRED_')"
echo "  $out"
grep -q '^CRED_OK' <<< "$out" || rc=4

if [[ -n "$ADU" ]]; then
  echo "[$dom] Registrando credencial de consola (usuario ${ADU}, ${DOM_ADMIN_URL[$dom]})..."
  out="$(CMOPS_U="$ADU" CMOPS_P="$ADP" CONFIG_JVM_ARGS="$WLST_SSL_JVM_ARGS" bash -c 'printf "y\ny\n" | "$@"' _ "$WLST" \
          "${SCRIPT_DIR}/wl-store-cred.py" admin "$dh" "${DOM_ADMIN_URL[$dom]}" 2>/dev/null | grep -E '^CRED_')"
  echo "  $out"
  grep -q '^CRED_OK' <<< "$out" || rc=4
else
  echo "[$dom] Credencial de consola omitida (el status usara el Node Manager)"
fi
unset NMP ADP
chmod 600 "$dh"/nmsecure/*.secure 2>/dev/null
exit $rc
