#!/bin/bash
# =============================================================================
# wl-bootstrap-admin.sh  -  Genera SOLO la credencial de CONSOLA cifrada
# -----------------------------------------------------------------------------
# Complemento de wl-bootstrap.sh para cuando la credencial del Node Manager ya
# existe y solo falta habilitar el status via AdminServer (Opcion B).
#
# Crea por dominio:
#   <DOMAIN_HOME>/nmsecure/adminUserConfig.secure
#   <DOMAIN_HOME>/nmsecure/adminUserKey.secure
#
# Usuario '-' = saltar dominio.  Clave vacia = saltar dominio.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/wl-config.sh"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

gen_admin_secure() {
  local dom="$1" adUser="$2" adPass="$3"
  local dh; dh="$(wl_domain_home "$dom")"
  mkdir -p "${dh}/nmsecure" || return 1

  local tmp; tmp="$(mktemp /tmp/wlboot.XXXXXX.py)"
  cat > "$tmp" <<PYEOF
try:
    connect(username='${adUser}', password='${adPass}', url='${DOM_ADMIN_URL[$dom]}')
    storeUserConfig(userConfigFile='${dh}/nmsecure/adminUserConfig.secure',
                    userKeyFile='${dh}/nmsecure/adminUserKey.secure')
    disconnect()
    print('BOOTSTRAP_OK ${dom}')
except Exception, e:
    print('BOOTSTRAP_ERROR ${dom} ' + str(e))
    exit("5")
PYEOF

  CONFIG_JVM_ARGS="$WLST_SSL_JVM_ARGS" \
    "$WLST" "$tmp" 2>/dev/null | grep -E '^BOOTSTRAP_(OK|ERROR)'
  local rc=$?
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  return $rc
}

echo "=== Bootstrap credencial de CONSOLA (Opcion B) - ${NM_HOST} ==="
echo "Usuario '-' o clave vacia = SALTAR el dominio."
echo

for dom in $(wl_all_domains); do
  wl_domain_exists "$dom" || { log "omito $dom (directorio no existe)"; continue; }
  echo "----- Dominio: $dom  (Admin ${DOM_ADMIN_URL[$dom]}) -----"
  read -r -p "  Usuario consola/Admin [$dom] (Enter=weblogic, - =saltar): " adUser
  if [[ "$adUser" == "-" ]]; then
    log "  $dom: saltado"; continue
  fi
  [[ -z "$adUser" ]] && adUser="weblogic"
  read -r -s -p "  Clave consola/Admin [$dom]: " adPass; echo
  if [[ -z "$adPass" ]]; then
    log "  $dom: saltado (sin clave)"; continue
  fi
  gen_admin_secure "$dom" "$adUser" "$adPass"
  adPass=""
done

echo
log "Listo. Verifique con: ./wl-services.sh status"
