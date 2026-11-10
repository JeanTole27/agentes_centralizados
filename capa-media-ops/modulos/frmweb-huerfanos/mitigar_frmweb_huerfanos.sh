#!/bin/bash
#===============================================================================
# mitigar_frmweb_huerfanos.sh
#
# Objetivo : Mitigar el alto consumo de CPU en AP17SBCC04 (10.1.5.129) causado
#            por procesos frmweb huerfanos (formulario TRON, modulo MIG) atrapados
#            en bucle infinito, SIN reiniciar los managed servers WLS_FORMS1_1/2/3.
#
# Seguridad: Solo termina procesos frmweb que en el momento exacto de la ejecucion
#            cumplan AMBAS condiciones:
#              - Estado R (Running, consumiendo CPU)
#              - CERO conexiones TCP ESTABLISHED (sin sesion de usuario detras)
#            Cualquier proceso con una conexion activa es SALTADO automaticamente.
#
# Metodo   : SIGTERM (cierre ordenado) por lotes con pausa y monitoreo de load.
#            Barrido final con SIGKILL solo para los que sobrevivan y sigan
#            cumpliendo las condiciones de huerfano.
#
# EJECUTAR COMO: wlogic12c   (sudo su - wlogic12c)
# USO          : ./mitigar_frmweb_huerfanos.sh [--dry-run] [--yes] [TAMANO_LOTE]
#                --yes       : no pide confirmacion (uso desatendido desde Capa Media Ops).
#                --dry-run   : solo valida y muestra el resumen de huerfanos, NO mata nada.
#                TAMANO_LOTE : opcional, por defecto 20. Usar 10 para mas prudencia.
#                Ejemplos:
#                  ./mitigar_frmweb_huerfanos.sh --dry-run    (solo diagnostico)
#                  ./mitigar_frmweb_huerfanos.sh              (mitigacion, lote 20)
#                  ./mitigar_frmweb_huerfanos.sh 10           (mitigacion, lote 10)
#
# NOTA: Mitigacion TEMPORAL. La causa raiz (modulo MIG de TRON sin timeout) sigue
#       activa; los huerfanos reapareceran. Acciones permanentes pendientes:
#         - Configurar FORMS_TIMEOUT / heartBeat / sessionTimeout en formsweb.cfg
#         - Revisar el modulo MIG (posible LOOP sin salida)
#         - Reinicio rolling de WLS_FORMS1_1/2/3 en ventana de mantenimiento
#===============================================================================

set -u

# --- Parsing de argumentos: acepta --dry-run y/o TAMANO_LOTE en cualquier orden ---
DRY_RUN=0
ASSUME_YES=0
LOTE=20
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    ''|*[!0-9]*)  : ;;              # ignora no-numericos que no sean el flag
    *)            LOTE="$arg" ;;    # numerico -> tamano de lote
  esac
done

PAUSA_LOTE=15      # segundos de pausa cada LOTE procesos
ESPERA_BARRIDO=30  # segundos de espera antes del SIGKILL de barrido

log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Devuelve 0 (exito) si el PID es huerfano AHORA: existe, estado R, sin ESTABLISHED
es_huerfano_ahora(){
  local pid="$1"
  [ -d "/proc/$pid" ] || return 1
  local st
  st=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)
  [ "$st" = "R" ] || return 1
  local estab
  estab=$(ss -tanp 2>/dev/null | grep "pid=$pid," | grep -c ESTAB)
  [ "$estab" -eq 0 ] || return 1
  return 0
}

lista_frmweb_R(){
  ps -eo pid,stat,args | grep -i "[f]rmweb" | awk '$2 ~ /^R/ {print $1}'
}

# Devuelve el nombre del managed server padre de un frmweb (o "otro" si no se reconoce)
managed_server_de(){
  local pid="$1" ppid name
  ppid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
  # Se identifica el managed server por -Dweblogic.Name del proceso padre
  # (antes se usaban PPID fijos, que cambian en cada reinicio).
  name=$(ps -o args= -p "$ppid" 2>/dev/null | grep -oP 'weblogic.Name=\K[^ ]+')
  echo "${name:-otro($ppid)}"
}

#--- Pre-validacion: cuenta e informa huerfanos ANTES de actuar ---
# Recorre los frmweb en R, clasifica huerfano (sin ESTABLISHED) vs con-conexion,
# los agrupa por managed server y exige confirmacion. Aborta si no hay huerfanos.
prevalidar(){
  log "=== PRE-VALIDACION: buscando procesos frmweb huerfanos ==="
  local total_R=0 huerf=0 conx=0 pid ms estab
  declare -A H_MS   # huerfanos por managed server
  declare -A C_MS   # con conexion por managed server

  for pid in $(lista_frmweb_R); do
    total_R=$((total_R+1))
    [ -d "/proc/$pid" ] || continue
    ms=$(managed_server_de "$pid")
    estab=$(ss -tanp 2>/dev/null | grep "pid=$pid," | grep -c ESTAB)
    if [ "$estab" -eq 0 ]; then
      huerf=$((huerf+1)); H_MS[$ms]=$(( ${H_MS[$ms]:-0} + 1 ))
    else
      conx=$((conx+1)); C_MS[$ms]=$(( ${C_MS[$ms]:-0} + 1 ))
    fi
  done

  echo ""
  echo "  ------------------------------------------------------------"
  echo "  RESUMEN DE PRE-VALIDACION"
  echo "  ------------------------------------------------------------"
  printf "  %-18s %-12s %-14s\n" "MANAGED SERVER" "HUERFANOS" "CON_CONEXION"
  local key
  for key in $(printf '%s\n' "${!H_MS[@]}" "${!C_MS[@]}" | sort -u); do
    printf "  %-18s %-12s %-14s\n" "$key" "${H_MS[$key]:-0}" "${C_MS[$key]:-0}"
  done
  echo "  ------------------------------------------------------------"
  printf "  %-18s %-12s %-14s\n" "TOTAL" "$huerf" "$conx"
  echo "  ------------------------------------------------------------"
  echo "  frmweb en estado R evaluados: $total_R"
  echo ""

  # Guardar el conteo en variables globales para el resto del script
  HUERFANOS_DETECTADOS=$huerf

  # Modo solo-validacion: informa y termina sin matar nada
  if [ "$DRY_RUN" -eq 1 ]; then
    log "MODO --dry-run: solo validacion, no se termina ningun proceso."
    if [ "$huerf" -eq 0 ]; then
      log "No hay procesos frmweb huerfanos en este momento."
    else
      log "Se detectaron $huerf huerfanos (ejecute sin --dry-run para mitigarlos)."
    fi
    log "=== FIN (dry-run, sin acciones) $(date) ==="
    exit 0
  fi

  if [ "$huerf" -eq 0 ]; then
    log "No se detectaron procesos frmweb huerfanos. No hay nada que mitigar."
    log "=== FIN (sin acciones) $(date) ==="
    exit 0
  fi

  echo "  Se terminaran $huerf procesos huerfanos (los $conx con conexion activa NO se tocan)."
  if [ "$ASSUME_YES" -eq 1 ]; then
    log "Confirmacion automatica (--yes)."
    echo ""
    return 0
  fi
  if [ ! -t 0 ]; then
    log "Sin terminal interactiva y sin --yes: se aborta por seguridad."
    exit 1
  fi
  read -r -p "  Desea continuar con la mitigacion? (s/N): " ok
  if [ "${ok:-N}" != "s" ]; then
    log "Abortado por el usuario tras la pre-validacion."
    exit 1
  fi
  echo ""
}

#--- Verificacion de usuario ---
USUARIO_ESPERADO="${USUARIO_ESPERADO:-wlogic12c}"
if [ "$(id -un)" != "$USUARIO_ESPERADO" ]; then
  log "ADVERTENCIA: no se esta ejecutando como $USUARIO_ESPERADO (usuario actual: $(id -un))."
  log "La validacion de sockets/estado puede fallar por permisos."
  if [ "$ASSUME_YES" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then
    log "Se continua (modo desatendido/dry-run)."
  else
    read -r -p "Continuar de todos modos? (s/N): " resp
    [ "${resp:-N}" = "s" ] || { log "Abortado por el usuario."; exit 1; }
  fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "=== INICIO VALIDACION (--dry-run, no se matara ningun proceso) ==="
else
  log "=== INICIO MITIGACION (lote=$LOTE) ==="
fi
uptime
total_R_inicial=$(lista_frmweb_R | wc -l)
log "frmweb en estado R al inicio: $total_R_inicial"

#--- Pre-validacion previa (aborta si no hay huerfanos o si el usuario no confirma) ---
prevalidar
log "Pre-validacion OK: $HUERFANOS_DETECTADOS huerfanos detectados. Iniciando mitigacion..."

#--- Fase 1: SIGTERM por lotes ---
muertos=0
saltados=0
for pid in $(lista_frmweb_R); do
  if es_huerfano_ahora "$pid"; then
    if kill -15 "$pid" 2>/dev/null; then
      muertos=$((muertos+1))
    fi
    if [ $((muertos % LOTE)) -eq 0 ] && [ "$muertos" -gt 0 ]; then
      log "--- $muertos SIGTERM enviados; pausa ${PAUSA_LOTE}s ---"
      sleep "$PAUSA_LOTE"
      uptime
    fi
  else
    saltados=$((saltados+1))
    log "SALTADO $pid (ya no es huerfano: con conexion / no-R / inexistente)"
  fi
done

log "=== Fase 1 completa: SIGTERM a $muertos procesos, $saltados saltados ==="
log "Esperando ${ESPERA_BARRIDO}s antes del barrido con SIGKILL..."
sleep "$ESPERA_BARRIDO"

#--- Fase 2: barrido SIGKILL solo a sobrevivientes que siguen siendo huerfanos ---
forzados=0
for pid in $(lista_frmweb_R); do
  if es_huerfano_ahora "$pid"; then
    kill -9 "$pid" 2>/dev/null && forzados=$((forzados+1))
  fi
done
log "=== Fase 2 completa: SIGKILL a $forzados procesos que sobrevivieron ==="

#--- Resultado final ---
log "=== RESULTADO FINAL ==="
uptime
total_R_final=$(ps -eo stat,args | grep -i "[f]rmweb" | grep -c "^R")
total_S_final=$(ps -eo stat,args | grep -i "[f]rmweb" | grep -c "^S")
log "frmweb en R restantes: $total_R_final"
log "frmweb en S restantes: $total_S_final"
log "Total terminados: $muertos via SIGTERM + $forzados via SIGKILL (barrido)"
log ""
log "NOTA: El load average es una media movil; tardara 2-3 minutos en reflejar el alivio."
log "Recuerde: mitigacion temporal. Programar reinicio de managed servers + timeout en formsweb.cfg."
log "=== FIN $(date) ==="
