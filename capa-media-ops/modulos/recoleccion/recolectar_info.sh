#!/bin/bash
# =============================================================================
# recolectar_info.sh - Recoleccion de informacion del servidor (SOLO LECTURA)
# -----------------------------------------------------------------------------
# Reemplaza la parte de recoleccion de la iniciativa capa_media (comparativo
# PROD vs DRP). Se ejecuta via `ssh host bash -s < recolectar_info.sh` y emite un
# JSON entre las marcas CMOPS_JSON_BEGIN / CMOPS_JSON_END que el nodo central
# guarda en inventario/hechos/<HOST>.json.
# Compatible con bash 4.1 (Oracle Linux 6) en adelante. No requiere python.
# =============================================================================
export LC_ALL=C
echo "CMOPS: recolectando SO, CPU, memoria, discos, limites, Java y WebLogic..."

esc() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\t'/ }"; s="${s//$'\r'/}"; s="${s//$'\n'/ }"; printf '%s' "$s"; }
num() { local v="$1"; [[ "$v" =~ ^[0-9]+$ ]] && printf '%s' "$v" || printf 'null'; }

so=""
for f in /etc/oracle-release /etc/redhat-release /etc/os-release; do
  if [ -r "$f" ]; then
    if [ "$f" = /etc/os-release ]; then so=$(. "$f"; echo "$PRETTY_NAME"); else so=$(head -1 "$f"); fi
    break
  fi
done
kernel=$(uname -r)
nucleos=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo)
modelo=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')
mem_total_kb=$(awk '/^MemTotal/ {print $2}' /proc/meminfo)
mem_disp_kb=$(awk '/^MemAvailable/ {print $2}' /proc/meminfo)
if [ -z "$mem_disp_kb" ]; then
  mem_disp_kb=$(awk '/^(MemFree|Buffers|Cached):/ {s+=$2} END {print s}' /proc/meminfo)
fi
swap_total_kb=$(awk '/^SwapTotal/ {print $2}' /proc/meminfo)
swap_free_kb=$(awk '/^SwapFree/ {print $2}' /proc/meminfo)
gb() { awk -v k="$1" 'BEGIN { printf "%d", (k/1048576)+0.5 }'; }
uptime_dias=$(awk '{ printf "%d", $1/86400 }' /proc/uptime)

# Discos (excluye pseudo-filesystems)
discos=""
while read -r fs size used avail pct mnt; do
  [ "$fs" = "Filesystem" ] && continue
  discos="${discos}{\"montaje\":\"$(esc "$mnt")\",\"tamano\":\"$(esc "$size")\",\"uso_pct\":$(num "${pct%\%}")},"
done < <(df -hP -x tmpfs -x devtmpfs 2>/dev/null)
discos="[${discos%,}]"

# Limites (del usuario que ejecuta) + lineas de limits.conf de los usuarios de middleware
nofile_s=$(ulimit -Sn); nofile_h=$(ulimit -Hn); nproc_s=$(ulimit -Su); nproc_h=$(ulimit -Hu)
limits_conf=$(grep -hE '^\s*(oracle|wlogic|@oinstall|\*)' /etc/security/limits.conf /etc/security/limits.d/*.conf 2>/dev/null | grep -E 'nofile|nproc' | head -20 | tr '\n' ';')
filemax=$(cat /proc/sys/fs/file-max 2>/dev/null)

# Java instalados
java=""
for j in /opt/jdk* /opt/java/* /usr/java/jdk* /oracle/app/*/jdk* /Oracle/app/*/jdk* /u01/*/jdk*; do
  [ -x "$j/bin/java" ] && java="${java}\"$(esc "$j")\","
done
java="[${java%,}]"

# Dominios WebLogic (config.xml bajo user_projects/domains) y su version
wl=""
for base in /oracle /Oracle /u01 /opt/oracle; do
  [ -d "$base" ] || continue
  while IFS= read -r cfg; do
    dom_dir=$(dirname "$(dirname "$cfg")")
    nombre=$(grep -m1 -oE '<name>[^<]+</name>' "$cfg" | sed -E 's#</?name>##g')
    ver=$(grep -m1 -oE '<domain-version>[^<]+' "$cfg" | sed 's/<domain-version>//')
    ms=$(grep -c '<server>' "$cfg" 2>/dev/null)
    apps=$(grep -c '<app-deployment>' "$cfg" 2>/dev/null)
    ds=$(ls "$dom_dir"/config/jdbc/*-jdbc.xml 2>/dev/null | wc -l)
    # descubrimiento para la orquestacion: quien es el duenio del dominio y como se llega al NM
    prop=$(stat -c %U "$dom_dir" 2>/dev/null)
    mw=${dom_dir%%/user_projects/*}
    adm=$(grep -m1 -oE '<admin-server-name>[^<]+' "$cfg" | sed 's/<admin-server-name>//')
    nmp="$dom_dir/nodemanager/nodemanager.properties"
    nm_port=$(grep -m1 -E '^ListenPort=' "$nmp" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    nm_tipo=$(grep -m1 -E '^SecureListener=' "$nmp" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    case "$nm_tipo" in false) nm_tipo=plain;; true) nm_tipo=ssl;; *) nm_tipo="";; esac
    nombres=$(awk '/<server>/{f=1;next} f&&/<name>/{gsub(/.*<name>|<\/name>.*/,"");printf "%s ",$0;f=0}' "$cfg" 2>/dev/null)
    lista=""; for n in $nombres; do [ "$n" = "$adm" ] || lista="${lista}\"$(esc "$n")\","; done
    wl="${wl}{\"dominio\":\"$(esc "$nombre")\",\"ruta\":\"$(esc "$dom_dir")\",\"version\":\"$(esc "$ver")\",\"servers\":$(num "$ms"),\"despliegues\":$(num "$apps"),\"datasources\":$(num "$ds"),\"propietario\":\"$(esc "$prop")\",\"mw_home\":\"$(esc "$mw")\",\"admin_name\":\"$(esc "$adm")\",\"nm_port\":\"$(esc "$nm_port")\",\"nm_tipo\":\"$(esc "$nm_tipo")\",\"managed_servers\":[${lista%,}]},"
  done < <(find "$base" -maxdepth 8 -path '*user_projects/domains/*/config/config.xml' -type f 2>/dev/null | head -40)
done
wl="[${wl%,}]"

# DNS de bases de datos usadas por los datasources (si existen en /etc/hosts o DNS)
dns=""
for n in bd-tron bd-terceros bd-capi bd-tronec bd-tercec; do
  ip=$(getent hosts "$n" 2>/dev/null | awk '{print $1; exit}')
  dns="${dns}\"$n\":\"$(esc "${ip:-NO_RESUELVE}")\","
done
dns="{${dns%,}}"

echo "CMOPS_JSON_BEGIN"
cat <<EOF
{"hostname":"$(esc "$(hostname)")","so":"$(esc "$so")","kernel":"$(esc "$kernel")","cpu_nucleos":$(num "$nucleos"),"cpu_modelo":"$(esc "$modelo")","ram_total_gb":$(gb "${mem_total_kb:-0}"),"ram_usada_gb":$(gb "$(( ${mem_total_kb:-0} - ${mem_disp_kb:-0} ))"),"swap_total_gb":$(gb "${swap_total_kb:-0}"),"swap_usada_gb":$(gb "$(( ${swap_total_kb:-0} - ${swap_free_kb:-0} ))"),"uptime_dias":$(num "$uptime_dias"),"discos":${discos},"limites":{"nofile_soft":"$(esc "$nofile_s")","nofile_hard":"$(esc "$nofile_h")","nproc_soft":"$(esc "$nproc_s")","nproc_hard":"$(esc "$nproc_h")","file_max":"$(esc "$filemax")","limits_conf":"$(esc "$limits_conf")"},"java":${java},"weblogic":${wl},"dns":${dns}}
EOF
echo "CMOPS_JSON_END"
echo "CMOPS: recoleccion finalizada"
