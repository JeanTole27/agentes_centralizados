#!/bin/bash
# =============================================================================
# wl-services.sh  -  Orquestador de dominios WebLogic  (WLPC02SBCC04 / 10.7.8.19)
# -----------------------------------------------------------------------------
# Subcomandos:
#   status      Muestra el estado (proceso Admin + MS via Node Manager)
#   inventory   Toma un snapshot del estado actual (que MS estan RUNNING)
#   stop        Baja: Managed Servers (nmKill) -> AdminServer (SIGTERM)
#   start       Sube: Node Manager (si no esta arriba) -> AdminServer -> Managed
#               Servers (nmStart, solo los del snapshot). Sirve tras reinicio del SO.
#
# Opciones:
#   -d <dominio>   Limitar a un dominio (repetible o CSV)
#   -t <segundos>  Timeout para apagado ordenado del AdminServer (def 120)
#   -y             No pedir confirmacion (modo desatendido)
#
# La logica NO depende del servidor; toda particularidad vive en wl-config.sh.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/wl-config.sh"

TIMEOUT=120
ASSUME_YES=0
SEL_DOMAINS=()
PARALLEL=1          # 1 = secuencial (def). >1 o "all" = dominios en paralelo.
STOP_NM=0           # 1 = en stop, bajar tambien el Node Manager (apagado total).

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
err()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

# El usuario de SO NO esta fijo: cada dominio debe operarse con el usuario que es
# duenio de su DOMAIN_HOME (wlogic12c, wlogic14c, oracle, ...). Si no coincide, se
# aborta ese dominio en stop/start (las senales y el arranque fallarian a medias).
check_owner() {
  local dom="$1" dh owner yo
  dh="$(wl_domain_home "$dom")"; owner="$(stat -c %U "$dh" 2>/dev/null)"; yo="$(id -un)"
  if [[ -n "$owner" && "$owner" != "$yo" ]]; then
    err "  [$dom] el dominio pertenece a '$owner' y se ejecuta como '$yo'; se omite (corregir usuario_so en el inventario)"
    return 1
  fi
  return 0
}

usage() {
  cat <<EOF
Uso: $0 <status|inventory|stop|start> [-d dominio] [-t seg] [-y] [-p N|all]
  -d  dominio (repetible o CSV: -d a,b)
  -t  timeout admin en seg (def ${TIMEOUT})
  -y  sin confirmacion (modo desatendido)
  -p  paralelismo entre DOMINIOS para stop/start: N dominios a la vez, o 'all'.
      Def 1 (secuencial). Cada dominio es independiente; dentro de un dominio
      la operacion sigue siendo ordenada.
  --stop-nm  en stop: ademas baja el Node Manager (apagado total, p.ej. antes
      de reiniciar el SO). Por defecto el NM se deja arriba.
EOF
  exit 1
}

# --------------------------------------------------------------------- parseo
[[ $# -lt 1 ]] && usage
ACTION="$1"; shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d) shift; IFS=',' read -ra parts <<< "$1"; SEL_DOMAINS+=("${parts[@]}");;
    -t) shift; TIMEOUT="$1";;
    -y) ASSUME_YES=1;;
    -p) shift; PARALLEL="$1";;
    --stop-nm) STOP_NM=1;;
    -h|--help) usage;;
    *) err "opcion desconocida: $1"; usage;;
  esac
  shift
done

# dominios objetivo
target_domains() {
  if [[ ${#SEL_DOMAINS[@]} -gt 0 ]]; then
    printf '%s\n' "${SEL_DOMAINS[@]}"
  else
    wl_all_domains
  fi
}

# --------------------------------------------------------- deteccion de Admin
# Devuelve el PID del AdminServer de un dominio (por su DOMAIN_HOME en la linea de proceso)
admin_pid() {
  local dom="$1" dh; dh="$(wl_domain_home "$dom")"
  local aname="${DOM_ADMIN_NAME[$dom]}"
  local cmd
  pgrep -f "weblogic.Name=${aname}[^A-Za-z0-9_]" 2>/dev/null | while read -r pid; do
    # Leer cmdline de forma segura: el proceso puede desaparecer entre pgrep y la
    # lectura (frecuente en arranques paralelos). Todo el bloque de lectura corre
    # en un subshell con stderr silenciado, para que el posible "No such file"
    # del redireccionamiento (<) tampoco se imprima.
    cmd="$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null )" || continue
    [[ -z "$cmd" ]] && continue
    if grep -q "${dh}\b" <<< "$cmd"; then
      echo "$pid"; break
    fi
  done
}

# Devuelve el PID de un servidor (MS o Admin) por su nombre y el DOMAIN_HOME del
# dominio. Sirve para detectar procesos que el Node Manager no controla (MS
# arrancados de forma independiente), que el nmKill no puede detener.
server_pid() {
  local dom="$1" srv="$2" dh; dh="$(wl_domain_home "$dom")"
  local cmd
  pgrep -f "weblogic.Name=${srv}[^A-Za-z0-9_]" 2>/dev/null | while read -r pid; do
    cmd="$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null )" || continue
    [[ -z "$cmd" ]] && continue
    if grep -q "${dh}\b" <<< "$cmd"; then
      echo "$pid"; break
    fi
  done
}

# Envia un nmKill de cierre a un servidor (para que el Node Manager registre el
# estado como SHUTDOWN intencional y NO dispare el auto-restart tras matar el
# proceso). No falla si el servidor ya figura detenido.
nm_kill_close() {
  local dom="$1" srv="$2"
  "$WLST" "${SCRIPT_DIR}/wl-nm-action.py" kill \
      "${DOM_DOMAIN_NAME[$dom]}" "$(wl_domain_home "$dom")" "$NM_HOST" \
      "${DOM_NM_PORT[$dom]}" "${DOM_NM_TYPE[$dom]}" "$srv" \
      </dev/null 2>/dev/null | grep -E '^SERVER=' | sed 's/^/      (cierre NM) /'
}

# Detiene un servidor por señal (SIGTERM -> SIGKILL). Fallback cuando el nmKill
# no lo alcanza porque no fue arrancado por el Node Manager.
# Tras matar el proceso, envia un nmKill de cierre para que el NM lo marque como
# SHUTDOWN y NO lo reinicie (los MS pueden tener AutoRestart=true).
kill_server_by_signal() {
  local dom="$1" srv="$2" pid; pid="$(server_pid "$dom" "$srv")"
  [[ -z "$pid" ]] && return 0   # ya no existe
  log "    [$dom] $srv sigue vivo (pid=$pid); deteniendo por SIGTERM (fallback)"
  kill -TERM "$pid" 2>/dev/null
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 3; waited=$((waited+3))
    if [[ $waited -ge $TIMEOUT ]]; then
      err "    [$dom] $srv no cerro en ${TIMEOUT}s -> SIGKILL"
      kill -KILL "$pid" 2>/dev/null
      break
    fi
  done
  # Cierre ante el Node Manager para evitar el auto-restart.
  nm_kill_close "$dom" "$srv"
  # Verificar que no haya sido reiniciado; si reaparece, reintentar una vez.
  sleep 4
  local pid2; pid2="$(server_pid "$dom" "$srv")"
  if [[ -n "$pid2" ]]; then
    log "    [$dom] $srv reaparecio (pid=$pid2, auto-restart NM); SIGKILL + cierre NM"
    kill -KILL "$pid2" 2>/dev/null
    sleep 2
    nm_kill_close "$dom" "$srv"
  fi
  log "    [$dom] $srv detenido (fallback por señal)"
}

# Propiedades SSL para las conexiones t3s de WLST se toman de wl-config.sh
# (variable WLST_SSL_JVM_ARGS: CustomTrust apuntando al JKS real del entorno).

# ------------------------------------------------------- estado por Node Manager
nm_status() {
  local dom="$1" dh; dh="$(wl_domain_home "$dom")"
  "$WLST" "${SCRIPT_DIR}/wl-nm-status.py" \
      "${DOM_DOMAIN_NAME[$dom]}" "$dh" "$NM_HOST" "${DOM_NM_PORT[$dom]}" "${DOM_NM_TYPE[$dom]}" \
      ${DOM_SERVERS[$dom]} </dev/null 2>/dev/null | grep -E '^(SERVER=|NM_CONNECT_ERROR)'
}

# --------------------------------------------------- estado REAL por AdminServer
# Solo se intenta si existe la credencial de consola cifrada del dominio.
admin_status() {
  local dom="$1" dh; dh="$(wl_domain_home "$dom")"
  [[ -f "${dh}/nmsecure/adminUserConfig.secure" ]] || return 9
  CONFIG_JVM_ARGS="$WLST_SSL_JVM_ARGS" "$WLST" "${SCRIPT_DIR}/wl-admin-status.py" \
      "$dh" "${DOM_ADMIN_URL[$dom]}" ${DOM_SERVERS[$dom]} \
      </dev/null 2>/dev/null | grep -E '^(SERVER=|ADMIN_CONNECT_ERROR)'
}

# Estado de MS: intenta AdminServer (autoritativo) y cae a Node Manager si falla.
srv_status() {
  local dom="$1" out
  out="$(admin_status "$dom")"
  if [[ -n "$out" ]] && ! grep -q 'ADMIN_CONNECT_ERROR' <<< "$out"; then
    echo "$out"
    return 0
  fi
  # fallback: Node Manager
  nm_status "$dom"
}

# =============================================================================
# STATUS
# =============================================================================
do_status() {
  local dom
  while read -r dom; do
    wl_domain_exists "$dom" || { err "dominio no existe: $dom"; continue; }
    echo "==================== $dom ===================="
    local pid; pid="$(admin_pid "$dom")"
    if [[ -n "$pid" ]]; then
      echo "  AdminServer(${DOM_ADMIN_NAME[$dom]}): RUNNING pid=$pid"
    else
      echo "  AdminServer(${DOM_ADMIN_NAME[$dom]}): NOT_RUNNING"
    fi
    srv_status "$dom" | sed 's/^/  /'
  done < <(target_domains)
}

# =============================================================================
# INVENTORY (snapshot de MS RUNNING)
# =============================================================================
do_inventory() {
  mkdir -p "$STATE_DIR"
  local dom stamp; stamp="$(date '+%Y%m%d-%H%M%S')"
  while read -r dom; do
    wl_domain_exists "$dom" || { err "dominio no existe: $dom"; continue; }
    local snap="${STATE_DIR}/${dom}.snapshot"
    : > "$snap"
    srv_status "$dom" | awk -F'[ =]' '/^SERVER=/ && $NF=="RUNNING" {print $2}' >> "$snap"
    local n; n=$(wc -l < "$snap" | tr -d ' ')
    log "snapshot $dom -> $n MS RUNNING guardados en $snap"
  done < <(target_domains)
  log "inventario tomado ($stamp)"
}

# =============================================================================
# STOP  (MS via nmKill -> AdminServer SIGTERM)
# =============================================================================
stop_admin() {
  local dom="$1" pid; pid="$(admin_pid "$dom")"
  [[ -z "$pid" ]] && { log "  [$dom] AdminServer ya detenido"; return 0; }
  log "  [$dom] deteniendo AdminServer pid=$pid (SIGTERM, timeout ${TIMEOUT}s)"
  kill -TERM "$pid" 2>/dev/null
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 3; waited=$((waited+3))
    if [[ $waited -ge $TIMEOUT ]]; then
      err "  [$dom] AdminServer no cerro en ${TIMEOUT}s -> SIGKILL"
      kill -KILL "$pid" 2>/dev/null
      break
    fi
  done
  log "  [$dom] AdminServer detenido"
}

# Baja de UN dominio (Managed Servers via nmKill -> AdminServer SIGTERM).
# Fallback: si un MS sigue vivo tras el nmKill (p.ej. fue arrancado por fuera del
# Node Manager), se detiene por señal (SIGTERM -> SIGKILL).
stop_one_domain() {
  local dom="$1"
  echo "==================== STOP $dom ===================="
  check_owner "$dom" || return 1
  if [[ -n "${DOM_SERVERS[$dom]}" ]]; then
    log "  [$dom] deteniendo Managed Servers via Node Manager (nmKill)"
    "$WLST" "${SCRIPT_DIR}/wl-nm-action.py" kill \
        "${DOM_DOMAIN_NAME[$dom]}" "$(wl_domain_home "$dom")" "$NM_HOST" \
        "${DOM_NM_PORT[$dom]}" "${DOM_NM_TYPE[$dom]}" ${DOM_SERVERS[$dom]} \
        </dev/null 2>/dev/null | grep -E '^(SERVER=|NM_CONNECT_ERROR)' | sed 's/^/    /'
    # Fallback: verificar que ningun MS quedo vivo (nmKill no alcanza MS
    # arrancados por fuera del Node Manager). Dar unos segundos al nmKill.
    sleep 3
    local srv
    for srv in ${DOM_SERVERS[$dom]}; do
      if [[ -n "$(server_pid "$dom" "$srv")" ]]; then
        kill_server_by_signal "$dom" "$srv"
      fi
    done
  fi
  # Orden segun modo:
  #  - Apagado total (--stop-nm): bajar el Node Manager AHORA, antes del
  #    AdminServer. Asi el NM no puede revivir MS con AutoRestart mientras el
  #    AdminServer tarda en cerrar. Luego se baja el AdminServer por señal.
  #  - Modo normal: solo AdminServer; el NM se deja arriba (para poder subir).
  if [[ $STOP_NM -eq 1 ]]; then
    stop_nm "$dom"
    # Con el NM ya abajo, matar cualquier MS que hubiera revivido en el intervalo
    # (ya no puede volver a arrancarlos porque el NM no existe).
    local p
    for srv in ${DOM_SERVERS[$dom]}; do
      p="$(server_pid "$dom" "$srv")"
      if [[ -n "$p" ]]; then
        log "    [$dom] $srv seguia vivo tras bajar NM; SIGKILL definitivo (pid=$p)"
        kill -KILL "$p" 2>/dev/null
      fi
    done
    stop_admin "$dom"
  else
    stop_admin "$dom"
  fi
}

# Detiene el Node Manager del dominio (solo en apagado total, --stop-nm). Lo
# identifica por el proceso weblogic.NodeManager con el DOMAIN_HOME del dominio.
stop_nm() {
  local dom="$1" dh port pid
  dh="$(wl_domain_home "$dom")"; port="${DOM_NM_PORT[$dom]}"
  if ! ss -tln 2>/dev/null | grep -q ":${port}\b"; then
    log "  [$dom] Node Manager ya detenido (puerto ${port})"
    return 0
  fi
  pid="$(pgrep -f "weblogic.NodeManager" 2>/dev/null | while read -r p; do
           if { tr '\0' ' ' < "/proc/$p/cmdline"; } 2>/dev/null | grep -q "${dh}\b"; then echo "$p"; break; fi
         done)"
  [[ -z "$pid" ]] && { log "  [$dom] no se encontro proceso del Node Manager"; return 0; }
  log "  [$dom] deteniendo Node Manager pid=$pid (SIGTERM)"
  kill -TERM "$pid" 2>/dev/null
  local waited=0
  while ss -tln 2>/dev/null | grep -q ":${port}\b"; do
    sleep 3; waited=$((waited+3))
    if [[ $waited -ge 60 ]]; then
      err "  [$dom] Node Manager no cerro -> SIGKILL"
      kill -KILL "$pid" 2>/dev/null; break
    fi
  done
  log "  [$dom] Node Manager detenido"
}

do_stop() { run_domains stop_one_domain; }

# =============================================================================
# START  (AdminServer -> MS del snapshot via nmStart)
# =============================================================================
start_admin() {
  local dom="$1" dh aname pid
  dh="$(wl_domain_home "$dom")"; aname="${DOM_ADMIN_NAME[$dom]}"
  pid="$(admin_pid "$dom")"
  if [[ -n "$pid" ]]; then log "  [$dom] AdminServer ya RUNNING pid=$pid"; return 0; fi
  if [[ -x "${dh}/bin/startWebLogic.sh" ]]; then
    log "  [$dom] arrancando AdminServer ($aname)"
    nohup "${dh}/bin/startWebLogic.sh" > "${dh}/nohup-admin.out" 2>&1 &
    local waited=0
    while [[ -z "$(admin_pid "$dom")" ]]; do
      sleep 5; waited=$((waited+5))
      [[ $waited -ge $TIMEOUT ]] && { err "  [$dom] AdminServer no levanto en ${TIMEOUT}s"; return 1; }
    done
    log "  [$dom] AdminServer arriba"
  else
    err "  [$dom] no existe ${dh}/bin/startWebLogic.sh"
    return 1
  fi
}

# Verifica que el Node Manager del dominio este arriba; si no, lo arranca.
# Idempotente: si ya escucha en su puerto, no hace nada. Necesario tras un
# reinicio del servidor, donde los NM tambien mueren.
ensure_nm() {
  local dom="$1" dh port
  dh="$(wl_domain_home "$dom")"
  port="${DOM_NM_PORT[$dom]}"
  # ya escucha? -> nada que hacer
  if ss -tln 2>/dev/null | grep -q ":${port}\b"; then
    log "  [$dom] Node Manager ya activo (puerto ${port})"
    return 0
  fi
  if [[ ! -x "${dh}/bin/startNodeManager.sh" ]]; then
    err "  [$dom] no existe ${dh}/bin/startNodeManager.sh; no se puede arrancar el Node Manager"
    return 1
  fi
  log "  [$dom] Node Manager no activo; arrancando (startNodeManager.sh)"
  nohup "${dh}/bin/startNodeManager.sh" > "${dh}/nodemanager/nm-start.out" 2>&1 &
  local waited=0
  while ! ss -tln 2>/dev/null | grep -q ":${port}\b"; do
    sleep 3; waited=$((waited+3))
    if [[ $waited -ge 60 ]]; then
      err "  [$dom] Node Manager no quedo en escucha (puerto ${port}) en 60s"
      return 1
    fi
  done
  log "  [$dom] Node Manager activo (puerto ${port})"
  return 0
}

# Sube UN dominio (Node Manager -> AdminServer -> MS del snapshot via nmStart, idempotente).
start_one_domain() {
  local dom="$1"
  echo "==================== START $dom ===================="
  check_owner "$dom" || return 1
  ensure_nm "$dom" || { err "  [$dom] sin Node Manager; se omite el dominio"; return 1; }
  start_admin "$dom" || return 1
  local snap="${STATE_DIR}/${dom}.snapshot"
  local list=""
  if [[ -f "$snap" ]]; then
    list="$(tr '\n' ' ' < "$snap")"
  fi
  if [[ -z "${list// /}" ]]; then
    log "  [$dom] sin snapshot de MS; no se arrancan Managed Servers (usar inventory antes de stop)"
    return 0
  fi

  # Idempotencia: no arrancar los MS que ya estan RUNNING.
  local running_now pending="" s
  running_now="$(srv_status "$dom" | awk -F'[ =]' '/^SERVER=/ && $NF=="RUNNING" {print $2}')"
  for s in $list; do
    if grep -qx "$s" <<< "$running_now"; then
      echo "    SERVER=$s ACTION=start RESULT=ALREADY_RUNNING(skip)"
    else
      pending="$pending $s"
    fi
  done

  if [[ -z "${pending// /}" ]]; then
    log "  [$dom] todos los MS del snapshot ya estan RUNNING; nada que arrancar"
    return 0
  fi

  log "  [$dom] arrancando Managed Servers pendientes:$pending"
  "$WLST" "${SCRIPT_DIR}/wl-nm-action.py" start \
      "${DOM_DOMAIN_NAME[$dom]}" "$(wl_domain_home "$dom")" "$NM_HOST" \
      "${DOM_NM_PORT[$dom]}" "${DOM_NM_TYPE[$dom]}" $pending \
      </dev/null 2>/dev/null | grep -E '^(SERVER=|NM_CONNECT_ERROR)' | sed 's/^/    /'
}

do_start() { run_domains start_one_domain; }

# =============================================================================
# run_domains  -  ejecuta una funcion por-dominio sobre los dominios objetivo,
# de forma secuencial (PARALLEL=1) o en paralelo por dominio (PARALLEL>1|all).
# En paralelo, la salida de cada dominio se bufferiza y se vuelca completa y
# ordenada al terminar, para que no se entremezclen las lineas.
# =============================================================================
run_domains() {
  local worker="$1"
  local -a doms=()
  local d
  while read -r d; do
    wl_domain_exists "$d" || { err "dominio no existe: $d"; continue; }
    doms+=("$d")
  done < <(target_domains)
  [[ ${#doms[@]} -eq 0 ]] && { err "no hay dominios validos"; return 1; }

  # limite de concurrencia
  local limit="$PARALLEL"
  if [[ "$limit" == "all" ]]; then
    limit=${#doms[@]}
  fi
  if ! [[ "$limit" =~ ^[0-9]+$ ]] || [[ "$limit" -lt 1 ]]; then
    limit=1
  fi

  # modo secuencial: comportamiento original
  if [[ "$limit" -le 1 ]]; then
    for d in "${doms[@]}"; do "$worker" "$d"; done
    return 0
  fi

  log "Ejecutando ${ACTION} en paralelo: hasta ${limit} dominios a la vez (${#doms[@]} en total)"
  local tmpd; tmpd="$(mktemp -d /tmp/wlorq.XXXXXX)"
  local -A pid_of=() out_of=()
  local running=0

  for d in "${doms[@]}"; do
    # throttle: esperar si alcanzamos el limite
    while [[ $running -ge $limit ]]; do
      wait -n 2>/dev/null || wait
      running=$((running-1))
    done
    local of="${tmpd}/${d}.out"
    out_of[$d]="$of"
    ( "$worker" "$d" ) > "$of" 2>&1 &
    pid_of[$d]=$!
    running=$((running+1))
  done

  # esperar a todos
  wait

  # volcar salida en el orden original de los dominios
  for d in "${doms[@]}"; do
    cat "${out_of[$d]}" 2>/dev/null
  done
  rm -rf "$tmpd"
}

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  echo "Dominios objetivo:"; target_domains | sed 's/^/  - /'
  read -r -p "Confirmar accion '$ACTION'? (s/N): " ans
  [[ "$ans" =~ ^[sS]$ ]]
}

case "$ACTION" in
  status)    do_status ;;
  inventory) do_inventory ;;
  stop)      if confirm; then do_stop; exit $?; else log "cancelado"; exit 0; fi ;;
  start)     if confirm; then do_start; exit $?; else log "cancelado"; exit 0; fi ;;
  *) usage ;;
esac
