#!/bin/bash
# =============================================================================
# wl-bootstrap.sh  -  Genera credenciales cifradas del Node Manager por dominio
# -----------------------------------------------------------------------------
# Crea (una sola vez, o al rotar claves):
#   <DOMAIN_HOME>/nmsecure/nmUserConfig.secure
#   <DOMAIN_HOME>/nmsecure/nmUserKey.secure
#
# Pide, por dominio y de forma OCULTA, el usuario/clave del NODE MANAGER.
# Clave vacia (Enter) = saltar ese dominio.
#
# En este servidor todos los Node Managers son PLAIN y la clave del Node Manager
# puede diferir del admin de consola; cada dominio tiene su propia credencial.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/wl-config.sh"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Genera un script WLST temporal que conecta al NM y almacena las credenciales.
gen_secure() {
  local dom="$1" nmUser="$2" nmPass="$3"
  local dh; dh="$(wl_domain_home "$dom")"
  mkdir -p "${dh}/nmsecure" || return 1

  local tmp; tmp="$(mktemp /tmp/wlboot.XXXXXX.py)"
  cat > "$tmp" <<PYEOF
try:
    nmConnect(username='${nmUser}', password='${nmPass}',
              host='${NM_HOST}', port='${DOM_NM_PORT[$dom]}',
              domainName='${DOM_DOMAIN_NAME[$dom]}', domainDir='${dh}',
              nmType='${DOM_NM_TYPE[$dom]}')
    storeUserConfig(userConfigFile='${dh}/nmsecure/nmUserConfig.secure',
                    userKeyFile='${dh}/nmsecure/nmUserKey.secure',
                    nm='true')
    nmDisconnect()
    print('BOOTSTRAP_OK ${dom}')
except Exception, e:
    print('BOOTSTRAP_ERROR ${dom} ' + str(e))
    exit(5)
PYEOF

  "$WLST" "$tmp" 2>/dev/null | grep -E '^BOOTSTRAP_(OK|ERROR)'
  local rc=$?
  # borrado seguro del script temporal (contiene la clave en claro)
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  return $rc
}

# Genera la credencial de CONSOLA cifrada conectando al AdminServer por t3s.
# Habilita el status autoritativo (Opcion B). Es opcional: si no se genera,
# wl-services.sh status cae al Node Manager.
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
    exit(5)
PYEOF

  CONFIG_JVM_ARGS="$WLST_SSL_JVM_ARGS" \
    "$WLST" "$tmp" 2>/dev/null | grep -E '^BOOTSTRAP_(OK|ERROR)'
  local rc=$?
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  return $rc
}

echo "=== Bootstrap de credenciales Node Manager (${NM_HOST}) ==="
echo "Deje la clave vacia (Enter) para SALTAR un dominio."
echo

for dom in $(wl_all_domains); do
  wl_domain_exists "$dom" || { log "omito $dom (directorio no existe)"; continue; }
  echo "----- Dominio: $dom  (NM puerto ${DOM_NM_PORT[$dom]} ${DOM_NM_TYPE[$dom]}) -----"
  read -r -p "  Usuario Node Manager [$dom] (Enter=weblogic): " nmUser
  [[ -z "$nmUser" ]] && nmUser="weblogic"
  read -r -s -p "  Clave Node Manager [$dom]: " nmPass; echo
  if [[ -z "$nmPass" ]]; then
    log "  saltado $dom (sin clave)"
    continue
  fi
  gen_secure "$dom" "$nmUser" "$nmPass"
  nmPass=""   # limpiar de memoria

  # ---- Credencial de CONSOLA (opcional, habilita status via AdminServer) ----
  read -r -p "  Usuario consola/Admin [$dom] (Enter=weblogic, - =saltar): " adUser
  if [[ "$adUser" == "-" ]]; then
    log "  $dom: sin credencial de consola (status usara Node Manager)"
  else
    [[ -z "$adUser" ]] && adUser="weblogic"
    read -r -s -p "  Clave consola/Admin [$dom]: " adPass; echo
    if [[ -z "$adPass" ]]; then
      log "  $dom: sin credencial de consola (status usara Node Manager)"
    else
      gen_admin_secure "$dom" "$adUser" "$adPass"
      adPass=""
    fi
  fi
done

echo
log "Bootstrap finalizado. Verifique con: ./wl-services.sh status"
