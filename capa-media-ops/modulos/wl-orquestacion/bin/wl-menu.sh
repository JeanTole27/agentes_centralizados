#!/bin/bash
# =============================================================================
# wl-menu.sh  -  Menu interactivo para BAJAR o SUBIR dominios WebLogic
# -----------------------------------------------------------------------------
# Flujo:
#   1) Escoger accion: BAJAR (stop) o SUBIR (start)
#   2) Escoger dominios: uno, varios (CSV) o todos
#   3) Doble confirmacion mostrando la lista exacta
#
# En BAJAR: toma un snapshot automatico (inventory) ANTES de detener, para que
# el posterior SUBIR restaure exactamente los Managed Servers que estaban arriba.
# En SUBIR: arranca AdminServer + los MS del snapshot (idempotente: salta los
# que ya esten RUNNING).
#
# Si no hay terminal interactiva (cron/pipe), NO ejecuta nada.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/wl-config.sh"

# Requiere terminal interactiva
if [[ ! -t 0 || ! -t 1 ]]; then
  echo "wl-menu.sh requiere terminal interactiva."
  echo "Para modo desatendido use: ${SCRIPT_DIR}/wl-services.sh stop -y  (o start -y)"
  exit 1
fi

# ---------------------------------------------------------- 1) escoger accion
echo
echo " Accion a ejecutar:"
echo "   1) BAJAR  (stop)"
echo "   2) SUBIR  (start)"
echo "   q) salir"
read -r -p " Opcion: " act
case "$act" in
  1) ACTION="stop";  ACC_LABEL="BAJAR" ;;
  2) ACTION="start"; ACC_LABEL="SUBIR" ;;
  q|Q) echo "Saliendo sin cambios."; exit 0 ;;
  *) echo "Opcion invalida."; exit 1 ;;
esac

# --------------------------------------------------------- 2) escoger dominios
mapfile -t LIST < <(wl_all_domains)

print_menu() {
  echo
  echo "   0) TODOS los dominios"
  local i=1 dom
  for dom in "${LIST[@]}"; do
    local n; n=$(echo ${DOM_SERVERS[$dom]} | wc -w)
    printf "  %2d) %-30s (%d managed servers)\n" "$i" "$dom" "$n"
    i=$((i+1))
  done
  echo
  echo " Escoja: un numero, varios separados por coma (ej: 1,3,5), 0=TODOS, q=salir"
}

print_menu
read -r -p " Opcion: " opt
[[ "$opt" =~ ^[qQ]$ ]] && { echo "Saliendo sin cambios."; exit 0; }

SELECTED=()
if [[ -z "$opt" || "$opt" == "0" ]]; then
  SELECTED=("${LIST[@]}")
else
  IFS=',' read -ra nums <<< "$opt"
  for n in "${nums[@]}"; do
    n="$(echo "$n" | tr -d ' ')"
    if ! [[ "$n" =~ ^[0-9]+$ ]] || [[ "$n" -lt 1 || "$n" -gt ${#LIST[@]} ]]; then
      echo "Opcion invalida: '$n'"; exit 1
    fi
    SELECTED+=("${LIST[$((n-1))]}")
  done
fi

# ------------------------------------------------------- 3) doble confirmacion
echo
echo "Accion: ${ACC_LABEL}. Dominios objetivo:"
printf '  - %s\n' "${SELECTED[@]}"
echo
read -r -p " Confirma ${ACC_LABEL}? (s/N): " ans
[[ "$ans" =~ ^[sS]$ ]] || { echo "Cancelado."; exit 0; }

csv="$(IFS=,; echo "${SELECTED[*]}")"

# ---- paralelismo (solo tiene sentido con mas de un dominio) ----
PAR_OPT=()
if [[ ${#SELECTED[@]} -gt 1 ]]; then
  echo
  read -r -p " Ejecutar en PARALELO por dominio (mas rapido)? (s/N): " par
  if [[ "$par" =~ ^[sS]$ ]]; then
    PAR_OPT=(-p all)
    echo "   -> modo paralelo: todos los dominios a la vez"
  else
    echo "   -> modo secuencial: un dominio despues de otro"
  fi
fi

# ---- apagado total: bajar tambien los Node Managers (solo al BAJAR) ----
NM_OPT=()
if [[ "$ACTION" == "stop" ]]; then
  echo
  echo "Apagado TOTAL: bajar tambien los Node Managers?"
  echo "   (util antes de reiniciar el SO; por defecto los NM se dejan arriba)"
  read -r -p " Bajar Node Managers tambien? (s/N): " bnm
  if [[ "$bnm" =~ ^[sS]$ ]]; then
    NM_OPT=(--stop-nm)
    echo "   -> se bajaran tambien los Node Managers"
  else
    echo "   -> los Node Managers quedaran arriba"
  fi
fi

# ---------------------------------------------------------------- ejecutar
if [[ "$ACTION" == "stop" ]]; then
  # snapshot automatico antes de bajar (para poder restaurar identico al subir)
  echo
  echo ">> Tomando snapshot del estado actual antes de bajar..."
  "${SCRIPT_DIR}/wl-services.sh" inventory -d "$csv"
  echo ">> Bajando dominios seleccionados..."
  exec "${SCRIPT_DIR}/wl-services.sh" stop -d "$csv" -y "${PAR_OPT[@]}" "${NM_OPT[@]}"
else
  echo
  echo ">> Subiendo dominios seleccionados (usa el ultimo snapshot)..."
  exec "${SCRIPT_DIR}/wl-services.sh" start -d "$csv" -t 300 -y "${PAR_OPT[@]}"
fi
