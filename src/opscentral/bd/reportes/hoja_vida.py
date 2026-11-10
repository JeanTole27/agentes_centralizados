#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_hoja_vida.py
════════════════════
Script ejecutable que recrea la **Hoja de Vida de Base de Datos** (spec v2.0)
como un HTML autocontenido, replicando el reporte generado por la skill
`hoja-de-vida-bd`, pero desde un único script Python independiente de los
servidores MCP.

Características:
  • Solo lectura (100% SELECT). No ejecuta DML/DDL.
  • Consultas *corregidas* respecto a la primera corrida (ver sección WORKAROUNDS):
        - Tablespaces con dba_tablespace_usage_metrics (evita ORA-03156) y
          semáforo sobre pct_max_used contra autoextend.
        - version_full en 19c con fallback a version en 11g (ORA-00904).
        - Columnas de fecha del diccionario con TO_CHAR/CAST (ORA-01805/01877).
        - Data Pump con owner_name (no owner) (ORA-00904).
  • Recolección por categoría (funciones collect_*) y render por etapas
    (fs_write inicial + append) para no truncar HTML grande.
  • Toda categoría que falle se marca NOT_COLLECTED con el error real; el
    reporte nunca aborta por una sección.

Uso:
    python generar_hoja_vida.py --alias prod_capi1
    python generar_hoja_vida.py --alias prod_capi1 --rac-node 1 --output-dir ../
    python generar_hoja_vida.py --alias tron --env test

Requisitos:
    pip install oracledb python-dotenv
    Credenciales en config/.env (perfil activo) — igual que los servidores MCP.
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import datetime
import html
from pathlib import Path
from typing import Any, Optional

try:  # import diferido: Ops Central construye el HTML sin oracledb (usa su propia conexión)
    import oracledb
except ImportError:  # pragma: no cover - solo afecta la ejecución por línea de comandos
    oracledb = None


# ══════════════════════════════════════════════════════════════════════════════
#  RUTAS Y CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

# PAQUETE PORTABLE: todas las rutas son relativas a la carpeta de este script.
# La carpeta contiene su propia copia de config/ y escribe los reportes en
# reportes/ (dentro del mismo paquete). Así se puede extraer y ejecutar en
# cualquier equipo sin depender del resto del proyecto.
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT            # compatibilidad con el resto del código
CONFIG_DIR = PACKAGE_ROOT / "config"
REPORTS_DIR = PACKAGE_ROOT / "reportes"
DEFAULT_INVENTORY = CONFIG_DIR / "databases.json"
DEFAULT_ENV_FILE = CONFIG_DIR / ".env"

QUERY_TIMEOUT_MS = 30_000          # 30 s por consulta (igual que executor MCP)
MAX_ROWS = 500                      # límite de filas por consulta
COT_TZ = "-05:00"                   # Bogotá (UTC-5)

# Umbrales de semáforo (spec §6.1)
TS_WARN, TS_CRIT = 80.0, 95.0       # pct_max_used tablespaces / datafiles
INVALID_WARN, INVALID_CRIT = 20, 100
PATCH_WARN_DAYS, PATCH_CRIT_DAYS = 365, 730
ASM_WARN, ASM_CRIT = 80.0, 90.0

# Orden de severidad (para overall_status = MAX del detalle)
_SEV_ORDER = {
    "OK": 0, "NO_DATA": 1, "UNKNOWN": 2, "NOT_COLLECTED": 3,
    "WARNING": 4, "CRITICAL": 5,
}


def _log(msg: str) -> None:
    """Log a stderr (stdout queda limpio)."""
    print(f"[HV] {msg}", file=sys.stderr, flush=True)


# ══════════════════════════════════════════════════════════════════════════════
#  CARGA DE CONFIGURACIÓN Y CONEXIÓN
# ══════════════════════════════════════════════════════════════════════════════

def load_env(env_file: Path = DEFAULT_ENV_FILE) -> None:
    """Carga variables de config/.env (si python-dotenv está disponible)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        _log("python-dotenv no instalado — usando variables de entorno del sistema.")
        return
    if env_file.exists():
        load_dotenv(env_file, override=False)
        _log(f"Cargado .env desde: {env_file}")
    else:
        _log(f"No se encontró {env_file} — usando variables del sistema.")


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict[str, dict]:
    """Carga databases.json → {alias: {dsn, environment, conn_type}}."""
    if not path.exists():
        raise FileNotFoundError(f"Inventario no encontrado: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    inv = {}
    for row in data.get("databases", []):
        inv[row["alias"]] = {
            "alias": row["alias"],
            "dsn": row["dsn"],
            "environment": row.get("environment", ""),
            "conn_type": row.get("conn_type", "direct"),
        }
    return inv


def _read_active_profile() -> str:
    """Lee el perfil activo desde config/.active_profile o ACTIVE_PROFILE."""
    state_file = CONFIG_DIR / ".active_profile"
    try:
        if state_file.exists():
            content = state_file.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError:
        pass
    return os.getenv("ACTIVE_PROFILE", "").strip()


def _load_profiles() -> dict:
    """Carga config/profiles.json (o {} si no existe)."""
    path = CONFIG_DIR / "profiles.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("profiles", {})
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_credentials(alias: str, environment: str) -> tuple[str, str]:
    """
    Resuelve (user, password) replicando la lógica del ProfileManager:
      1. perfil activo → by_alias → by_environment → default
      2. ORACLE_USER_{ENV} / ORACLE_PASS_{ENV} (legacy por ambiente)
      3. ORACLE_USER / ORACLE_PASS (globales)
    """
    profiles = _load_profiles()
    active = _read_active_profile()

    def _env_pair(user_env: str, pass_env: str) -> Optional[tuple[str, str]]:
        u = os.getenv(user_env, "").strip()
        p = os.getenv(pass_env, "").strip()
        return (u, p) if u and p else None

    if active and active in profiles:
        creds = profiles[active].get("credentials", {})
        # 1. by_alias
        by_alias = creds.get("by_alias", {})
        if alias.lower() in {k.lower(): k for k in by_alias}:
            key = {k.lower(): k for k in by_alias}[alias.lower()]
            ref = by_alias[key]
            pair = _env_pair(ref.get("user_env", ""), ref.get("pass_env", ""))
            if pair:
                return pair
        # 2. by_environment
        by_env = creds.get("by_environment", {})
        if environment and environment.lower() in {k.lower(): k for k in by_env}:
            key = {k.lower(): k for k in by_env}[environment.lower()]
            ref = by_env[key]
            pair = _env_pair(ref.get("user_env", ""), ref.get("pass_env", ""))
            if pair:
                return pair
        # 3. default del perfil
        default = creds.get("default", {})
        pair = _env_pair(default.get("user_env", ""), default.get("pass_env", ""))
        if pair:
            return pair

    # Legacy por ambiente
    if environment:
        pair = _env_pair(f"ORACLE_USER_{environment.upper()}",
                         f"ORACLE_PASS_{environment.upper()}")
        if pair:
            return pair

    # Globales
    return (os.getenv("ORACLE_USER", "").strip(),
            os.getenv("ORACLE_PASS", "").strip())


def init_thick_if_configured() -> None:
    """Inicializa Instant Client (thick) si ORACLE_CLIENT_DIR está definido."""
    client_dir = os.getenv("ORACLE_CLIENT_DIR", "").strip()
    if client_dir and oracledb is not None and Path(client_dir).is_dir():
        try:
            oracledb.init_oracle_client(lib_dir=client_dir)
            _log(f"Oracle Client (thick mode): {client_dir}")
        except Exception as e:  # noqa: BLE001
            _log(f"No se pudo iniciar thick mode ({e}) — usando thin mode.")
    else:
        _log("Usando thin mode (sin Instant Client).")


class OracleClient:
    """Conexión única a un alias con ejecución de queries de solo lectura."""

    def __init__(self, alias: str, dsn: str, user: str, password: str):
        self.alias = alias
        self.dsn = dsn
        if oracledb is None:
            raise RuntimeError("falta el paquete 'oracledb' (pip install oracledb)")
        self._conn = oracledb.connect(user=user, password=password, dsn=dsn)
        try:
            self._conn.call_timeout = QUERY_TIMEOUT_MS
        except Exception:  # noqa: BLE001
            pass

    def query(self, sql: str, params: dict | None = None,
              max_rows: int = MAX_ROWS) -> list[dict]:
        """Ejecuta un SELECT y devuelve lista de dicts (columnas en minúscula)."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [c[0].lower() for c in cur.description]
            rows = cur.fetchmany(max_rows)
            out = []
            for row in rows:
                d = {}
                for k, v in zip(cols, row):
                    if hasattr(v, "read"):  # LOB
                        try:
                            v = v.read()
                        except Exception:  # noqa: BLE001
                            v = "<LOB>"
                    d[k] = v
                out.append(d)
            return out

    def query_one(self, sql: str, params: dict | None = None) -> Optional[dict]:
        rows = self.query(sql, params, max_rows=1)
        return rows[0] if rows else None

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


class Section:
    """Resultado de una categoría: datos + estado + posible error."""

    def __init__(self, data: Any = None, status: str = "OK",
                 error: str = "", note: str = ""):
        self.data = data
        self.status = status
        self.error = error
        self.note = note

    @classmethod
    def not_collected(cls, error: str, note: str = "") -> "Section":
        return cls(data=None, status="NOT_COLLECTED", error=error, note=note)


def safe_collect(client: OracleClient, fn, *args, **kwargs) -> Section:
    """Ejecuta una función collect_* capturando errores → NOT_COLLECTED."""
    try:
        result = fn(client, *args, **kwargs)
        if isinstance(result, Section):
            return result
        return Section(data=result, status="OK")
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        _log(f"  · {fn.__name__}: NOT_COLLECTED ({msg})")
        return Section.not_collected(error=msg)


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES DE FORMATO
# ══════════════════════════════════════════════════════════════════════════════

def esc(v: Any) -> str:
    """Escapa a HTML. None → 'N/A'."""
    if v is None:
        return "N/A"
    return html.escape(str(v))


def num(v: Any, decimals: int = 2) -> str:
    """Formatea número con separador de miles y N decimales."""
    if v is None:
        return "N/A"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return esc(v)
    if decimals == 0:
        return f"{int(round(f)):,}"
    return f"{f:,.{decimals}f}"


def badge(status: str) -> str:
    """Devuelve el <span> de semáforo para un estado dado."""
    cls = {
        "OK": "b-ok", "VALID": "b-ok", "SUCCESS": "b-ok", "COMPLETED": "b-ok",
        "WARNING": "b-warn",
        "CRITICAL": "b-crit",
    }.get(status, "b-na")
    return f'<span class="badge {cls}">{esc(status)}</span>'


def worst(*statuses: str) -> str:
    """Devuelve el peor estado según _SEV_ORDER."""
    valid = [s for s in statuses if s in _SEV_ORDER]
    if not valid:
        return "UNKNOWN"
    return max(valid, key=lambda s: _SEV_ORDER[s])


def ts_alert(pct: Optional[float]) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct >= TS_CRIT:
        return "CRITICAL"
    if pct >= TS_WARN:
        return "WARNING"
    return "OK"


def asm_alert(pct: Optional[float]) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct >= ASM_CRIT:
        return "CRITICAL"
    if pct >= ASM_WARN:
        return "WARNING"
    return "OK"


# ══════════════════════════════════════════════════════════════════════════════
#  RECOLECCIÓN POR CATEGORÍA  (collect_*)
#  Todas las consultas son de solo lectura y aplican los workarounds Oracle.
# ══════════════════════════════════════════════════════════════════════════════

def collect_instance(client: OracleClient) -> dict:
    """Cat.1 — v$instance × v$database (con fallback version 11g)."""
    # version_full existe en 12c+; en 11g solo version → intentar y hacer fallback.
    try:
        inst = client.query_one("""
            SELECT instance_name, host_name, version_full, version, status,
                   database_status, logins,
                   TO_CHAR(startup_time,'YYYY-MM-DD HH24:MI') AS startup_time,
                   ROUND(SYSDATE - startup_time, 2) AS uptime_days
            FROM v$instance
        """)
    except Exception:  # noqa: BLE001 — 11g sin version_full
        inst = client.query_one("""
            SELECT instance_name, host_name, version, version AS version_full,
                   status, database_status, logins,
                   TO_CHAR(startup_time,'YYYY-MM-DD HH24:MI') AS startup_time,
                   ROUND(SYSDATE - startup_time, 2) AS uptime_days
            FROM v$instance
        """)
    db = client.query_one("""
        SELECT name, db_unique_name, dbid, open_mode, log_mode,
               flashback_on, force_logging, protection_mode, database_role,
               platform_name,
               TO_CHAR(created,'YYYY-MM-DD HH24:MI') AS created
        FROM v$database
    """)
    return {"instance": inst or {}, "database": db or {}}


def collect_osstat(client: OracleClient) -> list[dict]:
    """Cat.1 — recursos OS (v$osstat)."""
    rows = client.query("""
        SELECT stat_name, value
        FROM v$osstat
        WHERE stat_name IN ('NUM_CPUS','NUM_CPU_CORES','NUM_CPU_SOCKETS',
                            'PHYSICAL_MEMORY_BYTES')
    """)
    return rows


def collect_parameters(client: OracleClient) -> list[dict]:
    """Cat.1 — parámetros de inicialización clave."""
    rows = client.query("""
        SELECT name, value, display_value
        FROM v$parameter
        WHERE name IN ('processes','sessions','sga_max_size','sga_target',
                       'memory_target','memory_max_target','pga_aggregate_target',
                       'cluster_database','compatible','db_recovery_file_dest_size',
                       'control_management_pack_access','statistics_level',
                       'undo_management','undo_tablespace','undo_retention')
        ORDER BY name
    """)
    return rows


def collect_components(client: OracleClient) -> list[dict]:
    """Cat.1 — componentes registrados (dba_registry)."""
    return client.query("""
        SELECT comp_id, comp_name, version, status
        FROM dba_registry
        ORDER BY comp_id
    """)


def collect_patches(client: OracleClient) -> Section:
    """Cat.2 — parches. 12c+: dba_registry_sqlpatch. 11g: dba_registry_history
    (dba_registry_sqlpatch no existe en 11g → ORA-00942)."""
    note = ""
    try:
        rows = client.query("""
            SELECT patch_id, patch_type, action, status,
                   TO_CHAR(CAST(action_time AS DATE),'YYYY-MM-DD') AS action_time,
                   description
            FROM dba_registry_sqlpatch
            ORDER BY action_time DESC
        """)
    except Exception:  # noqa: BLE001 — 11g sin dba_registry_sqlpatch (ORA-00942)
        rows = client.query("""
            SELECT * FROM (
                SELECT NULL AS patch_id, NULL AS patch_type,
                       action, NULL AS status,
                       TO_CHAR(CAST(action_time AS DATE),'YYYY-MM-DD') AS action_time,
                       comments AS description
                FROM dba_registry_history
                ORDER BY action_time DESC
            ) WHERE ROWNUM <= 50
        """)
        note = "Origen: dba_registry_history (Oracle 11g; dba_registry_sqlpatch no disponible)."
    status = "OK"
    # Semáforo: días desde el último parche
    if rows:
        last = rows[0].get("action_time")
        if last:
            try:
                d = datetime.datetime.strptime(last, "%Y-%m-%d").date()
                days = (datetime.date.today() - d).days
                if days > PATCH_CRIT_DAYS:
                    status = "CRITICAL"
                elif days > PATCH_WARN_DAYS:
                    status = "WARNING"
                extra = f"Último parche hace ~{days} días ({last})."
                note = f"{note} {extra}".strip() if note else extra
            except ValueError:
                pass
    else:
        status = "NO_DATA"
        note = note or "Sin registros de parches."
    return Section(data=rows, status=status, note=note)


def collect_scheduler_jobs(client: OracleClient) -> Section:
    """Cat.3 — scheduler jobs: conteo + top por failure_count (ORA-01805 workaround)."""
    summary = client.query_one("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN enabled='TRUE' THEN 1 ELSE 0 END) AS enabled_cnt,
               SUM(CASE WHEN state='BROKEN' THEN 1 ELSE 0 END) AS broken_cnt,
               SUM(failure_count) AS total_failures
        FROM dba_scheduler_jobs
    """)
    top = client.query("""
        SELECT * FROM (
            SELECT owner, job_name, state, failure_count, enabled,
                   TO_CHAR(CAST(last_start_date AS DATE),'YYYY-MM-DD HH24:MI') AS last_start
            FROM dba_scheduler_jobs
            WHERE failure_count > 0
            ORDER BY failure_count DESC
        ) WHERE ROWNUM <= 15
    """)
    total_failures = (summary or {}).get("total_failures") or 0
    status = "WARNING" if total_failures and total_failures > 0 else "OK"
    return Section(data={"summary": summary or {}, "top": top}, status=status)


def collect_tablespaces(client: OracleClient) -> Section:
    """
    Cat.4 — Tablespaces vía dba_tablespace_usage_metrics (evita ORA-03156 en BD
    grandes) cruzado con el máximo autoextend real. Semáforo sobre pct_max_used.
    """
    rows = client.query("""
        WITH files AS (
            SELECT tablespace_name,
                   SUM(bytes)/1024/1024                     AS alloc_mb,
                   SUM(GREATEST(bytes, DECODE(autoextensible,'YES',maxbytes,bytes)))
                       /1024/1024                            AS max_mb
            FROM dba_data_files
            GROUP BY tablespace_name
        )
        SELECT ts.tablespace_name,
               ts.contents,
               ROUND(f.alloc_mb, 2)                          AS alloc_mb,
               ROUND(f.max_mb, 2)                            AS max_mb,
               ROUND(m.used_space * ts.block_size/1024/1024, 2) AS used_mb,
               ROUND(m.used_percent, 2)                      AS pct_used,
               ROUND(m.used_space * ts.block_size/1024/1024
                     / NULLIF(f.max_mb,0) * 100, 2)          AS pct_max_used
        FROM dba_tablespace_usage_metrics m
        JOIN dba_tablespaces ts ON ts.tablespace_name = m.tablespace_name
        LEFT JOIN files f       ON f.tablespace_name  = m.tablespace_name
        ORDER BY pct_max_used DESC NULLS LAST
    """)
    overall = "OK"
    for r in rows:
        pmu = r.get("pct_max_used")
        if pmu is None:
            pmu = r.get("pct_used")
        r["alert_level"] = ts_alert(pmu)
        overall = worst(overall, r["alert_level"])
    return Section(data=rows, status=overall)


def collect_db_size(client: OracleClient) -> dict:
    """Cat.4 — tamaño total = datafiles + tempfiles."""
    row = client.query_one("""
        SELECT ROUND((
            (SELECT NVL(SUM(bytes),0) FROM dba_data_files) +
            (SELECT NVL(SUM(bytes),0) FROM dba_temp_files)
        )/1024/1024/1024, 2) AS total_gb FROM dual
    """)
    return row or {}


def collect_segments(client: OracleClient) -> list[dict]:
    """Cat.4/12 — segmentos por owner (Top 12 por tamaño)."""
    return client.query("""
        SELECT * FROM (
            SELECT owner, ROUND(SUM(bytes)/1024/1024/1024, 2) AS gb
            FROM dba_segments
            GROUP BY owner
            ORDER BY SUM(bytes) DESC
        ) WHERE ROWNUM <= 12
    """)


def collect_asm(client: OracleClient) -> Section:
    """Cat.4 — ASM diskgroups [EXT]."""
    rows = client.query("""
        SELECT name, state, type,
               ROUND(total_mb/1024, 2) AS total_gb,
               ROUND(free_mb/1024, 2)  AS free_gb,
               ROUND((total_mb-free_mb)/NULLIF(total_mb,0)*100, 2) AS pct_used
        FROM v$asm_diskgroup
        ORDER BY name
    """)
    overall = "OK"
    for r in rows:
        r["alert_level"] = asm_alert(r.get("pct_used"))
        overall = worst(overall, r["alert_level"])
    if not rows:
        return Section(data=[], status="NO_DATA",
                       note="Sin diskgroups ASM (posible almacenamiento en filesystem).")
    return Section(data=rows, status=overall)


def collect_redo(client: OracleClient) -> dict:
    """Cat.4 — redo logs (v$log) + switches últimas 24h."""
    logs = client.query("""
        SELECT group#, thread#, sequence#, ROUND(bytes/1024/1024, 2) AS size_mb,
               members, archived, status
        FROM v$log ORDER BY thread#, group#
    """)
    switches = client.query("""
        SELECT * FROM (
            SELECT TO_CHAR(first_time,'YYYY-MM-DD HH24') AS hour, COUNT(*) AS switches
            FROM v$log_history
            WHERE first_time > SYSDATE - 1
            GROUP BY TO_CHAR(first_time,'YYYY-MM-DD HH24')
            ORDER BY hour DESC
        ) WHERE ROWNUM <= 24
    """)
    return {"logs": logs, "switches": switches}


def collect_temp(client: OracleClient) -> dict:
    """Cat.5 — TEMP total + por sesión."""
    total = client.query("""
        SELECT tablespace_name,
               ROUND(tablespace_size/1024/1024, 2) AS total_mb,
               ROUND(allocated_space/1024/1024, 2) AS allocated_mb,
               ROUND(free_space/1024/1024, 2)      AS free_mb
        FROM dba_temp_free_space
    """)
    by_session = client.query("""
        SELECT * FROM (
            SELECT s.sid, s.username, s.program,
                   ROUND(t.blocks * (SELECT value FROM v$parameter
                        WHERE name='db_block_size')/1024/1024, 2) AS temp_mb
            FROM v$tempseg_usage t JOIN v$session s ON t.session_addr = s.saddr
            ORDER BY t.blocks DESC
        ) WHERE ROWNUM <= 20
    """)
    return {"total": total, "by_session": by_session}


def collect_undo(client: OracleClient) -> dict:
    """Cat.6 — configuración + uso por segmento UNDO."""
    config = client.query("""
        SELECT name, value FROM v$parameter
        WHERE name IN ('undo_management','undo_tablespace','undo_retention')
        ORDER BY name
    """)
    usage = client.query("""
        SELECT tablespace_name, status,
               ROUND(SUM(bytes)/1024/1024, 2) AS size_mb, COUNT(*) AS extents
        FROM dba_undo_extents
        GROUP BY tablespace_name, status
        ORDER BY tablespace_name, status
    """)
    return {"config": config, "usage": usage}


def collect_rman(client: OracleClient, days_back: int = 7) -> Section:
    """Cat.7 — RMAN backups recientes (fecha con TO_CHAR)."""
    rows = client.query("""
        SELECT input_type, status,
               TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS start_time,
               TO_CHAR(end_time,'YYYY-MM-DD HH24:MI')   AS end_time,
               ROUND(elapsed_seconds/60, 2)             AS duration_min,
               ROUND(input_bytes/1024/1024/1024, 2)     AS input_gb,
               ROUND(output_bytes/1024/1024/1024, 2)    AS output_gb
        FROM v$rman_backup_job_details
        WHERE start_time > SYSDATE - :days
        ORDER BY start_time DESC
    """, {"days": days_back})
    failed = [r for r in rows if r.get("status") not in ("COMPLETED", "RUNNING")]
    status = "CRITICAL" if failed else "OK"
    return Section(data={"backups": rows, "total": len(rows),
                         "failed": len(failed)}, status=status)


def collect_archiving_fra(client: OracleClient) -> dict:
    """Cat.7 — archiving mode + FRA."""
    arch = client.query_one("""
        SELECT log_mode, force_logging FROM v$database
    """)
    fra = client.query("""
        SELECT name,
               ROUND(space_limit/1024/1024/1024, 2)        AS limit_gb,
               ROUND(space_used/1024/1024/1024, 2)         AS used_gb,
               ROUND(space_reclaimable/1024/1024/1024, 2)  AS reclaimable_gb,
               ROUND(space_used/NULLIF(space_limit,0)*100, 2) AS pct_used
        FROM v$recovery_file_dest
    """)
    return {"archiving": arch or {}, "fra": fra}


def collect_top_sql(client: OracleClient) -> list[dict]:
    """Cat.8 — Top SQL por elapsed_time."""
    return client.query("""
        SELECT * FROM (
            SELECT sql_id, executions,
                   ROUND(elapsed_time/1e6, 2) AS elapsed_sec,
                   ROUND(cpu_time/1e6, 2)     AS cpu_sec,
                   buffer_gets, disk_reads,
                   SUBSTR(sql_text, 1, 120)   AS sql_text
            FROM v$sql
            WHERE executions > 0
            ORDER BY elapsed_time DESC
        ) WHERE ROWNUM <= 12
    """)


def collect_wait_events(client: OracleClient) -> list[dict]:
    """Cat.8 — wait events significativos."""
    return client.query("""
        SELECT * FROM (
            SELECT event, wait_class, total_waits,
                   ROUND(time_waited/100, 2)   AS time_waited_sec,
                   ROUND(average_wait/100, 4)  AS avg_wait_sec
            FROM v$system_event
            WHERE wait_class NOT IN ('Idle')
            ORDER BY time_waited DESC
        ) WHERE ROWNUM <= 12
    """)


def collect_sga_pga(client: OracleClient) -> dict:
    """Cat.8 — SGA / PGA."""
    sga = client.query_one("SELECT ROUND(SUM(value)/1024/1024,2) AS sga_mb FROM v$sga")
    pga = client.query_one("""
        SELECT ROUND(value/1024/1024,2) AS pga_target_mb
        FROM v$pgastat WHERE name='aggregate PGA auto target'
    """)
    return {"sga_mb": (sga or {}).get("sga_mb"),
            "pga_target_mb": (pga or {}).get("pga_target_mb")}


def collect_awr(client: OracleClient) -> Section:
    """Cat.9 — verificar acceso a AWR antes de marcar N/A (regla AWR)."""
    pack = client.query_one("""
        SELECT value FROM v$parameter WHERE name='control_management_pack_access'
    """)
    pack_val = (pack or {}).get("value", "")
    if "DIAGNOSTIC" not in (pack_val or "").upper():
        return Section(data={"pack": pack_val}, status="NO_DATA",
                       note="control_management_pack_access sin DIAGNOSTIC → AWR no licenciado.")
    # Intentar dba_hist_snapshot (ORA-00942 real → NOT_COLLECTED)
    snap = client.query_one("""
        SELECT COUNT(*) AS snaps,
               TO_CHAR(MIN(begin_interval_time),'YYYY-MM-DD HH24:MI') AS min_snap,
               TO_CHAR(MAX(end_interval_time),'YYYY-MM-DD HH24:MI')   AS max_snap
        FROM dba_hist_snapshot
    """)
    return Section(data={"pack": pack_val, "snapshots": snap or {}}, status="OK")


def collect_sessions(client: OracleClient) -> dict:
    """Cat.10 — resumen de sesiones + activas de usuario + bloqueos."""
    summary = client.query_one("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) AS active_cnt,
               SUM(CASE WHEN type='BACKGROUND' THEN 1 ELSE 0 END) AS background_cnt
        FROM v$session
    """)
    active = client.query("""
        SELECT * FROM (
            SELECT sid, username, program, machine, event, seconds_in_wait
            FROM v$session
            WHERE status='ACTIVE' AND type='USER'
            ORDER BY last_call_et DESC
        ) WHERE ROWNUM <= 20
    """)
    blocking = client.query("""
        SELECT s1.sid AS blocking_sid, s1.username AS blocking_user,
               s2.sid AS waiting_sid, s2.username AS waiting_user,
               s2.seconds_in_wait AS wait_seconds, s2.event AS wait_event
        FROM v$session s1 JOIN v$session s2 ON s2.blocking_session = s1.sid
        WHERE s2.blocking_session IS NOT NULL
        ORDER BY s2.seconds_in_wait DESC
    """)
    return {"summary": summary or {}, "active": active, "blocking": blocking}


def collect_security(client: OracleClient) -> dict:
    """Cat.11 — distribución de cuentas, roles DBA, db_links (agregado)."""
    accounts = client.query("""
        SELECT account_status, COUNT(*) AS cnt
        FROM dba_users
        GROUP BY account_status
        ORDER BY cnt DESC
    """)
    dba_roles = client.query("""
        SELECT grantee FROM dba_role_privs
        WHERE granted_role='DBA' ORDER BY grantee
    """)
    db_links = client.query("""
        SELECT owner, db_link, host,
               TO_CHAR(CAST(created AS DATE),'YYYY-MM-DD') AS created
        FROM dba_db_links ORDER BY owner, db_link
    """)
    return {"accounts": accounts, "dba_roles": dba_roles, "db_links": db_links}


def collect_object_summary(client: OracleClient) -> list[dict]:
    """Cat.12 — resumen por tipo de objeto (Top 20)."""
    return client.query("""
        SELECT * FROM (
            SELECT object_type, COUNT(*) AS cnt
            FROM dba_objects
            GROUP BY object_type
            ORDER BY cnt DESC
        ) WHERE ROWNUM <= 20
    """)


def collect_invalid_objects(client: OracleClient) -> Section:
    """Cat.13 — objetos inválidos: por tipo + destacados no-SYNONYM."""
    by_type = client.query("""
        SELECT object_type, COUNT(*) AS cnt
        FROM dba_objects WHERE status='INVALID'
        GROUP BY object_type ORDER BY cnt DESC
    """)
    total = sum(r.get("cnt", 0) for r in by_type)
    highlights = client.query("""
        SELECT * FROM (
            SELECT owner, object_name, object_type,
                   TO_CHAR(CAST(last_ddl_time AS DATE),'YYYY-MM-DD') AS last_ddl
            FROM dba_objects
            WHERE status='INVALID' AND object_type <> 'SYNONYM'
            ORDER BY owner, object_type
        ) WHERE ROWNUM <= 25
    """)
    if total > INVALID_CRIT:
        status = "CRITICAL"
    elif total > INVALID_WARN:
        status = "WARNING"
    else:
        status = "OK"
    return Section(data={"by_type": by_type, "total": total,
                         "highlights": highlights}, status=status)


def collect_mviews(client: OracleClient) -> Section:
    """Cat.14 — MViews con compile_state distinto de VALID."""
    rows = client.query("""
        SELECT * FROM (
            SELECT owner, mview_name, compile_state, last_refresh_type
            FROM dba_mviews
            WHERE compile_state <> 'VALID'
            ORDER BY owner, mview_name
        ) WHERE ROWNUM <= 50
    """)
    if not rows:
        return Section(data=[], status="OK",
                       note="No se detectaron MViews fuera de estado VALID.")
    return Section(data=rows, status="WARNING")


def collect_datapump(client: OracleClient) -> Section:
    """Cat.15 — Data Pump: usar owner_name (no owner) → workaround ORA-00904."""
    rows = client.query("""
        SELECT owner_name, job_name, operation, job_mode, state
        FROM dba_datapump_jobs
        ORDER BY owner_name, job_name
    """)
    if not rows:
        return Section(data=[], status="OK",
                       note="Sin jobs Data Pump activos.")
    return Section(data=rows, status="OK")


# ══════════════════════════════════════════════════════════════════════════════
#  RENDER HTML  (por etapas: cada bloque devuelve un fragmento HTML válido)
# ══════════════════════════════════════════════════════════════════════════════

CSS = """
  :root{
    --verde900:#14532d;--verde800:#166534;--verde700:#15803d;--verde100:#dcfce7;
    --ambar:#f59e0b;--rojo:#dc2626;--gris:#6b7280;--gris100:#f3f4f6;--gris200:#e5e7eb;
    --texto:#1f2937;--fondo:#f8fafc;
  }
  *{box-sizing:border-box;}
  body{font-family:'Segoe UI',Tahoma,Verdana,sans-serif;margin:0;background:var(--fondo);color:var(--texto);line-height:1.5;}
  header{background:linear-gradient(135deg,var(--verde900),var(--verde700));color:#fff;padding:28px 40px;border-top:5px solid var(--ambar);}
  header h1{margin:0 0 6px;font-size:1.7rem;}
  header .sub{opacity:.9;font-size:.95rem;}
  main{max-width:1180px;margin:0 auto;padding:24px 32px 64px;}
  h2{color:var(--verde800);border-bottom:2px solid var(--verde100);padding-bottom:6px;margin-top:40px;font-size:1.3rem;}
  h3{color:var(--verde700);font-size:1.05rem;margin-top:22px;}
  table{border-collapse:collapse;width:100%;margin:14px 0;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:.88rem;}
  th,td{border:1px solid var(--gris200);padding:7px 10px;text-align:left;vertical-align:top;}
  th{background:var(--verde800);color:#fff;font-weight:600;}
  tr:nth-child(even){background:var(--gris100);}
  .badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:700;color:#fff;}
  .b-ok{background:var(--verde700);} .b-warn{background:var(--ambar);} .b-crit{background:var(--rojo);}
  .b-na{background:var(--gris);}
  .cards{display:flex;flex-wrap:wrap;gap:14px;margin:20px 0;}
  .card{flex:1 1 180px;background:#fff;border-radius:8px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid var(--verde700);}
  .card .lbl{font-size:.78rem;color:var(--gris);text-transform:uppercase;}
  .card .val{font-size:1.25rem;font-weight:700;color:var(--verde800);margin-top:4px;}
  .card.warn{border-left-color:var(--ambar);} .card.crit{border-left-color:var(--rojo);}
  .toc{background:#fff;border-radius:8px;padding:16px 22px;box-shadow:0 1px 3px rgba(0,0,0,.08);columns:2;}
  .toc a{color:var(--verde700);text-decoration:none;font-size:.9rem;}
  .toc a:hover{text-decoration:underline;}
  .mono{font-family:'Consolas','Courier New',monospace;font-size:.85rem;}
  .top{font-size:.78rem;float:right;}
  .top a{color:var(--gris);text-decoration:none;}
  footer{max-width:1180px;margin:0 auto;padding:20px 32px;color:var(--gris);font-size:.82rem;border-top:1px solid var(--gris200);}
  .note{background:var(--verde100);border-left:4px solid var(--verde700);padding:8px 14px;margin:10px 0;font-size:.85rem;border-radius:0 6px 6px 0;}
  .nc{background:#fff7ed;border-left:4px solid var(--ambar);padding:8px 14px;margin:10px 0;font-size:.85rem;border-radius:0 6px 6px 0;}
"""

TOC_ENTRIES = [
    ("cat0", "0. Metadata y Resumen de Salud"),
    ("cat1", "1. BD e Instancia"),
    ("cat2", "2. Parches"),
    ("cat3", "3. Scheduler / Jobs"),
    ("cat4", "4. Almacenamiento"),
    ("cat5", "5. TEMP"),
    ("cat6", "6. UNDO"),
    ("cat7", "7. Backups y Recuperación"),
    ("cat8", "8. Rendimiento"),
    ("cat9", "9. AWR"),
    ("cat10", "10. Sesiones"),
    ("cat11", "11. Seguridad"),
    ("cat12", "12. Objetos"),
    ("cat13", "13. Salud de Objetos"),
    ("cat14", "14. Estructuras Avanzadas"),
    ("cat15", "15. Data Pump"),
]


def _top_link() -> str:
    return '<span class="top"><a href="#top">[Top]</a></span>'


def _nc_block(section: Section, label: str) -> str:
    """Bloque NOT_COLLECTED con el error real."""
    return (f'<div class="nc">{label}: <strong>NOT_COLLECTED</strong> — '
            f'{esc(section.error)}</div>')


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Construye una tabla HTML. Las celdas ya deben venir escapadas/HTML-safe."""
    out = ["<table>", "  <tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"]
    for r in rows:
        out.append("  <tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


# ── Etapa 1: cabecera + cards + toc + cat0 ────────────────────────────────────

def render_head(meta: dict, health_rows: list[tuple[str, str, str]],
                overall: str, cards: list[tuple[str, str, str]]) -> str:
    db_name = meta["db_name"]
    title = f"Hoja de Vida — {db_name} ({meta['alias']}) — {meta['date']}"
    card_html = []
    for lbl, val, klass in cards:
        cls = f" {klass}" if klass else ""
        card_html.append(
            f'  <div class="card{cls}"><div class="lbl">{esc(lbl)}</div>'
            f'<div class="val">{val}</div></div>')
    toc_html = "\n".join(
        f'  <a href="#{aid}">{esc(txt)}</a><br>' for aid, txt in TOC_ENTRIES)

    health_html = ["<table>",
                   "  <tr><th>Dimensión</th><th>Estado</th><th>Evidencia</th></tr>"]
    for dim, st, ev in health_rows:
        health_html.append(f"  <tr><td>{esc(dim)}</td><td>{badge(st)}</td>"
                           f"<td>{ev}</td></tr>")
    health_html.append(f"  <tr><td><strong>overall_status</strong></td>"
                       f"<td>{badge(overall)}</td>"
                       f"<td>Peor nivel del detalle recolectado</td></tr>")
    health_html.append("</table>")

    rac_line = ""
    if meta.get("rac_note"):
        rac_line = f" · {esc(meta['rac_note'])}"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>Hoja de Vida de Base de Datos — {esc(db_name)}</h1>
  <div class="sub">Alias inventario: <strong>{esc(meta['alias'])}</strong> · Ambiente: <strong>{esc(meta['environment'].upper())}</strong>{rac_line} · Generado {esc(meta['date'])} (COT, {COT_TZ})</div>
</header>
<main>

<a name="top"></a>
<div class="cards">
{chr(10).join(card_html)}
</div>

<div class="toc">
{toc_html}
</div>

<h2 id="cat0">0. Metadata del Reporte y Resumen de Salud {_top_link()}</h2>
<table>
  <tr><th>Atributo</th><th>Valor</th></tr>
  <tr><td>Documento</td><td>Hoja de Vida de Base de Datos (spec v2.0) — generado por script</td></tr>
  <tr><td>Base de datos</td><td>{esc(db_name)} (db_unique_name {esc(meta.get('db_unique_name','N/A'))})</td></tr>
  <tr><td>Alias de inventario</td><td class="mono">{esc(meta['alias'])}</td></tr>
  <tr><td>DSN</td><td class="mono">{esc(meta['dsn'])}</td></tr>
  <tr><td>Ambiente</td><td>{esc(meta['environment'].upper())}</td></tr>
  <tr><td>Fecha de generación</td><td>{esc(meta['date'])} (COT, {COT_TZ})</td></tr>
  <tr><td>DBID</td><td class="mono">{esc(meta.get('dbid','N/A'))}</td></tr>
  <tr><td>Herramienta</td><td class="mono">{esc(meta.get('herramienta') or 'generar_hoja_vida.py')}</td></tr>
</table>

<h3>Resumen de salud (semáforos)</h3>
{chr(10).join(health_html)}
<div class="note">Referencia cruzada: rankings y plan de acción priorizado → <em>reporte-analisis-bd-spec</em>.</div>
"""


# ── Categorías 1-3 ────────────────────────────────────────────────────────────

def render_cat1(inst_sec: Section, os_sec: Section, param_sec: Section,
                comp_sec: Section) -> str:
    parts = [f'<h2 id="cat1">1. Base de Datos e Instancia {_top_link()}</h2>']

    if inst_sec.status == "OK":
        i = inst_sec.data["instance"]
        d = inst_sec.data["database"]
        ver = i.get("version_full") or i.get("version")
        parts.append(_table(["Atributo", "Valor"], [
            ["DB Name", esc(d.get("name"))],
            ["DB Unique Name", esc(d.get("db_unique_name"))],
            ["Instancia", f"{esc(i.get('instance_name'))} @ {esc(i.get('host_name'))}"],
            ["Versión", esc(ver)],
            ["Rol / Open mode", f"{esc(d.get('database_role'))} / {esc(d.get('open_mode'))}"],
            ["Log mode", esc(d.get("log_mode"))],
            ["Force logging", esc(d.get("force_logging"))],
            ["Flashback", esc(d.get("flashback_on"))],
            ["Protection mode", esc(d.get("protection_mode"))],
            ["Plataforma", esc(d.get("platform_name"))],
            ["Creada", esc(d.get("created"))],
            ["Último arranque", f"{esc(i.get('startup_time'))} (uptime ~{num(i.get('uptime_days'))} días)"],
            ["Status", f"{esc(i.get('status'))} / {esc(i.get('database_status'))} / logins {esc(i.get('logins'))}"],
        ]))
    else:
        parts.append(_nc_block(inst_sec, "Metadata de instancia"))

    parts.append(f"<h3>Recursos del sistema operativo (v$osstat)</h3>")
    if os_sec.status == "OK" and os_sec.data:
        rows = [[esc(r.get("stat_name")), num(r.get("value"), 0)] for r in os_sec.data]
        parts.append(_table(["Métrica", "Valor"], rows))
    else:
        parts.append(_nc_block(os_sec, "v$osstat") if os_sec.status == "NOT_COLLECTED"
                     else '<div class="note">v$osstat sin datos.</div>')

    parts.append(f"<h3>Parámetros de inicialización (clave)</h3>")
    if param_sec.status == "OK" and param_sec.data:
        rows = [[esc(r.get("name")), esc(r.get("display_value") or r.get("value"))]
                for r in param_sec.data]
        parts.append(_table(["Parámetro", "Valor"], rows))
    else:
        parts.append(_nc_block(param_sec, "v$parameter"))

    parts.append(f"<h3>Componentes registrados (dba_registry)</h3>")
    if comp_sec.status == "OK" and comp_sec.data:
        rows = [[esc(r.get("comp_id")), esc(r.get("comp_name")),
                 esc(r.get("version")), badge(r.get("status") or "UNKNOWN")]
                for r in comp_sec.data]
        parts.append(_table(["Comp ID", "Nombre", "Versión", "Estado"], rows))
    else:
        parts.append(_nc_block(comp_sec, "dba_registry"))

    return "\n".join(parts)


def render_cat2(patch_sec: Section) -> str:
    parts = [f'<h2 id="cat2">2. Parches {_top_link()}</h2>']
    if patch_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(patch_sec, "dba_registry_sqlpatch"))
    elif not patch_sec.data:
        parts.append('<div class="note">Sin registros en dba_registry_sqlpatch.</div>')
    else:
        rows = [[f'<span class="mono">{esc(r.get("patch_id"))}</span>',
                 esc(r.get("patch_type")), esc(r.get("action")),
                 badge(r.get("status") or "UNKNOWN"),
                 esc(r.get("action_time")), esc(r.get("description"))]
                for r in patch_sec.data]
        parts.append(_table(
            ["Patch ID", "Tipo", "Acción", "Estado", "Fecha", "Descripción"], rows))
    if patch_sec.note:
        parts.append(f'<div class="note">{esc(patch_sec.note)}</div>')
    return "\n".join(parts)


def render_cat3(job_sec: Section) -> str:
    parts = [f'<h2 id="cat3">3. Scheduler / Jobs {_top_link()}</h2>']
    if job_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(job_sec, "dba_scheduler_jobs"))
        return "\n".join(parts)
    s = job_sec.data["summary"]
    tf = s.get("total_failures") or 0
    tf_cell = f'<span class="badge b-warn">{num(tf,0)}</span>' if tf else num(tf, 0)
    parts.append(_table(
        ["Total jobs", "Habilitados", "Broken", "Fallas acumuladas"],
        [[num(s.get("total"), 0), num(s.get("enabled_cnt"), 0),
          num(s.get("broken_cnt"), 0), tf_cell]]))
    if job_sec.data["top"]:
        parts.append("<h3>Top jobs por failure_count</h3>")
        rows = [[esc(r.get("owner")), esc(r.get("job_name")), esc(r.get("state")),
                 num(r.get("failure_count"), 0), esc(r.get("enabled")),
                 esc(r.get("last_start"))] for r in job_sec.data["top"]]
        parts.append(_table(
            ["Owner", "Job", "Estado", "Fallas", "Habilitado", "Último inicio"], rows))
    return "\n".join(parts)


# ── Categoría 4 ───────────────────────────────────────────────────────────────

def render_cat4(ts_sec: Section, size: dict, seg_sec: Section,
                asm_sec: Section, redo_sec: Section) -> str:
    parts = [f'<h2 id="cat4">4. Almacenamiento {_top_link()}</h2>']
    total_gb = size.get("total_gb")
    parts.append(f'<div class="note">Tamaño total datafiles + tempfiles: '
                 f'<strong>{num(total_gb)} GB</strong>. '
                 f'Semáforo de tablespaces calculado sobre <strong>pct_max_used</strong> '
                 f'(contra el máximo autoextend).</div>')

    parts.append("<h3>Tablespaces (todos, sin Top-N)</h3>")
    if ts_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(ts_sec, "dba_tablespace_usage_metrics"))
    else:
        rows = []
        for r in ts_sec.data:
            rows.append([
                esc(r.get("tablespace_name")), esc(r.get("contents")),
                num(r.get("alloc_mb")), num(r.get("max_mb")),
                num(r.get("used_mb")), num(r.get("pct_used")),
                num(r.get("pct_max_used")), badge(r.get("alert_level") or "UNKNOWN"),
            ])
        parts.append(_table(
            ["Tablespace", "Tipo", "Alloc MB", "Max MB", "Usado MB",
             "% Usado", "% Max Used", "Estado"], rows))

    parts.append("<h3>Segmentos por owner (Top 12 por tamaño)</h3>")
    if seg_sec.status == "OK" and seg_sec.data:
        rows = [[esc(r.get("owner")), num(r.get("gb"))] for r in seg_sec.data]
        parts.append(_table(["Owner", "GB"], rows))
    else:
        parts.append(_nc_block(seg_sec, "dba_segments"))

    parts.append("<h3>ASM Diskgroups [EXT]</h3>")
    if asm_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(asm_sec, "v$asm_diskgroup"))
    elif asm_sec.status == "NO_DATA":
        parts.append(f'<div class="note">{esc(asm_sec.note)}</div>')
    else:
        rows = [[esc(r.get("name")), esc(r.get("state")), esc(r.get("type")),
                 num(r.get("total_gb")), num(r.get("free_gb")),
                 num(r.get("pct_used")), badge(r.get("alert_level") or "UNKNOWN")]
                for r in asm_sec.data]
        parts.append(_table(
            ["Diskgroup", "Estado", "Tipo", "Total GB", "Libre GB",
             "% Usado", "Alerta"], rows))

    parts.append("<h3>Redo Logs (v$log)</h3>")
    if redo_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(redo_sec, "v$log"))
    else:
        logs = redo_sec.data["logs"]
        rows = [[num(r.get("group#"), 0), num(r.get("thread#"), 0),
                 num(r.get("sequence#"), 0), num(r.get("size_mb")),
                 num(r.get("members"), 0), esc(r.get("archived")),
                 esc(r.get("status"))] for r in logs]
        parts.append(_table(
            ["Grupo", "Thread", "Seq", "Size MB", "Miembros", "Archived", "Estado"],
            rows))
    return "\n".join(parts)


# ── Categorías 5-6 ────────────────────────────────────────────────────────────

def render_cat5(temp_sec: Section) -> str:
    parts = [f'<h2 id="cat5">5. TEMP {_top_link()}</h2>']
    if temp_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(temp_sec, "dba_temp_free_space"))
        return "\n".join(parts)
    rows = [[esc(r.get("tablespace_name")), num(r.get("total_mb")),
             num(r.get("allocated_mb")), num(r.get("free_mb"))]
            for r in temp_sec.data["total"]]
    parts.append(_table(["Tablespace", "Total MB", "Asignado MB", "Libre MB"], rows))
    if temp_sec.data["by_session"]:
        rows = [[num(r.get("sid"), 0), esc(r.get("username")),
                 esc(r.get("program")), num(r.get("temp_mb"))]
                for r in temp_sec.data["by_session"]]
        parts.append("<h3>Consumo TEMP por sesión</h3>")
        parts.append(_table(["SID", "Usuario", "Programa", "TEMP MB"], rows))
    else:
        parts.append('<div class="note">Sin sesiones consumiendo TEMP al momento del snapshot.</div>')
    return "\n".join(parts)


def render_cat6(undo_sec: Section) -> str:
    parts = [f'<h2 id="cat6">6. UNDO {_top_link()}</h2>']
    if undo_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(undo_sec, "dba_undo_extents"))
        return "\n".join(parts)
    cfg_rows = [[esc(r.get("name")), esc(r.get("value"))]
                for r in undo_sec.data["config"]]
    parts.append(_table(["Parámetro", "Valor"], cfg_rows))
    if undo_sec.data["usage"]:
        parts.append("<h3>Uso por segmento UNDO</h3>")
        rows = [[esc(r.get("tablespace_name")), esc(r.get("status")),
                 num(r.get("size_mb")), num(r.get("extents"), 0)]
                for r in undo_sec.data["usage"]]
        parts.append(_table(["Tablespace", "Estado", "Size MB", "Extents"], rows))
    return "\n".join(parts)


# ── Categoría 7 ───────────────────────────────────────────────────────────────

def render_cat7(rman_sec: Section, arch_sec: Section) -> str:
    parts = [f'<h2 id="cat7">7. Backups y Recuperación {_top_link()}</h2>']
    if rman_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(rman_sec, "v$rman_backup_job_details"))
    else:
        d = rman_sec.data
        failed_cell = (f'<span class="badge b-ok">0</span>' if d["failed"] == 0
                       else f'<span class="badge b-crit">{d["failed"]}</span>')
        parts.append(_table(["Métrica (7 días)", "Valor"], [
            ["Total backups", num(d["total"], 0)],
            ["Fallidos", failed_cell],
        ]))
        if d["backups"]:
            parts.append("<h3>Backups recientes</h3>")
            rows = [[esc(r.get("input_type")), esc(r.get("start_time")),
                     esc(r.get("end_time")), num(r.get("duration_min")),
                     num(r.get("input_gb")), badge(r.get("status") or "UNKNOWN")]
                    for r in d["backups"][:12]]
            parts.append(_table(
                ["Tipo", "Inicio", "Fin", "Dur (min)", "Entrada GB", "Estado"], rows))

    parts.append("<h3>Archiving y FRA</h3>")
    if arch_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(arch_sec, "v$recovery_file_dest"))
    else:
        a = arch_sec.data["archiving"]
        rows = [["Log mode", esc(a.get("log_mode"))],
                ["Force logging", esc(a.get("force_logging"))]]
        for fra in arch_sec.data["fra"]:
            rows.append([f"FRA ({esc(fra.get('name'))})",
                         f"límite {num(fra.get('limit_gb'))} GB, usado "
                         f"{num(fra.get('used_gb'))} GB ({num(fra.get('pct_used'))}%), "
                         f"reclaimable {num(fra.get('reclaimable_gb'))} GB"])
        parts.append(_table(["Atributo", "Valor"], rows))
    return "\n".join(parts)


# ── Categorías 8-9 ────────────────────────────────────────────────────────────

def render_cat8(sql_sec: Section, we_sec: Section, mem: dict) -> str:
    parts = [f'<h2 id="cat8">8. Rendimiento {_top_link()}</h2>']
    parts.append(f'<div class="note">SGA {num(mem.get("sga_mb"))} MB · '
                 f'PGA target {num(mem.get("pga_target_mb"))} MB.</div>')

    parts.append("<h3>Top SQL por elapsed time</h3>")
    if sql_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(sql_sec, "v$sql"))
    else:
        rows = [[f'<span class="mono">{esc(r.get("sql_id"))}</span>',
                 num(r.get("executions"), 0), num(r.get("elapsed_sec")),
                 num(r.get("cpu_sec")), num(r.get("buffer_gets"), 0),
                 num(r.get("disk_reads"), 0), esc(r.get("sql_text"))]
                for r in sql_sec.data]
        parts.append(_table(
            ["SQL ID", "Ejec.", "Elapsed (s)", "CPU (s)", "Buffer gets",
             "Disk reads", "Texto (preview)"], rows))

    parts.append("<h3>Wait events significativos</h3>")
    if we_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(we_sec, "v$system_event"))
    else:
        rows = [[esc(r.get("event")), esc(r.get("wait_class")),
                 num(r.get("total_waits"), 0), num(r.get("time_waited_sec")),
                 num(r.get("avg_wait_sec"), 4)] for r in we_sec.data]
        parts.append(_table(
            ["Evento", "Clase", "Total waits", "Tiempo (s)", "Avg (s)"], rows))
    return "\n".join(parts)


def render_cat9(awr_sec: Section) -> str:
    parts = [f'<h2 id="cat9">9. AWR {_top_link()}</h2>']
    if awr_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(awr_sec, "dba_hist_snapshot"))
    elif awr_sec.status == "NO_DATA":
        parts.append(f'<div class="note">{esc(awr_sec.note)}</div>')
    else:
        d = awr_sec.data
        snap = d.get("snapshots", {})
        parts.append(_table(["Atributo", "Valor"], [
            ["control_management_pack_access", esc(d.get("pack"))],
            ["Snapshots", num(snap.get("snaps"), 0)],
            ["Rango", f"{esc(snap.get('min_snap'))} → {esc(snap.get('max_snap'))}"],
        ]))
    return "\n".join(parts)


# ── Categorías 10-11 ──────────────────────────────────────────────────────────

def render_cat10(sess_sec: Section) -> str:
    parts = [f'<h2 id="cat10">10. Sesiones {_top_link()}</h2>']
    if sess_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(sess_sec, "v$session"))
        return "\n".join(parts)
    s = sess_sec.data["summary"]
    nblocks = len(sess_sec.data["blocking"])
    block_cell = (f'<span class="badge b-ok">0</span>' if nblocks == 0
                  else f'<span class="badge b-crit">{nblocks}</span>')
    parts.append(_table(["Métrica", "Valor"], [
        ["Sesiones totales", num(s.get("total"), 0)],
        ["Sesiones activas", num(s.get("active_cnt"), 0)],
        ["Sesiones background", num(s.get("background_cnt"), 0)],
        ["Cadenas de bloqueo", block_cell],
    ]))
    if sess_sec.data["active"]:
        parts.append("<h3>Sesiones activas de usuario</h3>")
        rows = [[num(r.get("sid"), 0), esc(r.get("username")),
                 esc(r.get("program")), esc(r.get("machine")),
                 esc(r.get("event")), num(r.get("seconds_in_wait"), 0)]
                for r in sess_sec.data["active"]]
        parts.append(_table(
            ["SID", "Usuario", "Programa", "Máquina", "Evento", "Espera (s)"], rows))
    if sess_sec.data["blocking"]:
        parts.append("<h3>Cadenas de bloqueo</h3>")
        rows = [[num(r.get("blocking_sid"), 0), esc(r.get("blocking_user")),
                 num(r.get("waiting_sid"), 0), esc(r.get("waiting_user")),
                 num(r.get("wait_seconds"), 0), esc(r.get("wait_event"))]
                for r in sess_sec.data["blocking"]]
        parts.append(_table(
            ["SID bloqueante", "Usuario", "SID en espera", "Usuario",
             "Espera (s)", "Evento"], rows))
    return "\n".join(parts)


def render_cat11(sec_sec: Section) -> str:
    parts = [f'<h2 id="cat11">11. Seguridad {_top_link()}</h2>']
    if sec_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(sec_sec, "dba_users / dba_role_privs / dba_db_links"))
        return "\n".join(parts)
    d = sec_sec.data
    parts.append("<h3>Distribución de cuentas (dba_users por account_status)</h3>")
    total_accounts = sum(r.get("cnt", 0) for r in d["accounts"])
    rows = [[esc(r.get("account_status")), num(r.get("cnt"), 0)]
            for r in d["accounts"]]
    rows.append([f"<strong>Total</strong>", f"<strong>{num(total_accounts,0)}</strong>"])
    parts.append(_table(["Estado", "Cuentas"], rows))

    parts.append("<h3>Usuarios con rol DBA</h3>")
    if d["dba_roles"]:
        rows = [[esc(r.get("grantee"))] for r in d["dba_roles"]]
        parts.append(_table(["Grantee"], rows))
    else:
        parts.append('<div class="note">Sin grants directos del rol DBA.</div>')

    parts.append("<h3>Database Links (dba_db_links)</h3>")
    if d["db_links"]:
        rows = [[esc(r.get("owner")), esc(r.get("db_link")),
                 esc(r.get("host")), esc(r.get("created"))]
                for r in d["db_links"]]
        parts.append(_table(["Owner", "DB Link", "Host/Servicio", "Creado"], rows))
    else:
        parts.append('<div class="note">Sin database links.</div>')
    return "\n".join(parts)


# ── Categorías 12-15 + cierre ────────────────────────────────────────────────

def render_cat12(obj_sec: Section) -> str:
    parts = [f'<h2 id="cat12">12. Objetos {_top_link()}</h2>']
    if obj_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(obj_sec, "dba_objects"))
        return "\n".join(parts)
    parts.append("<h3>Resumen por tipo de objeto (Top 20)</h3>")
    rows = [[esc(r.get("object_type")), num(r.get("cnt"), 0)] for r in obj_sec.data]
    parts.append(_table(["Tipo", "Cantidad"], rows))
    return "\n".join(parts)


def render_cat13(inv_sec: Section) -> str:
    parts = [f'<h2 id="cat13">13. Salud de Objetos {_top_link()}</h2>']
    if inv_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(inv_sec, "dba_objects (INVALID)"))
        return "\n".join(parts)
    d = inv_sec.data
    rows = [[esc(r.get("object_type")), num(r.get("cnt"), 0)] for r in d["by_type"]]
    total_badge = ""
    if inv_sec.status in ("WARNING", "CRITICAL"):
        total_badge = " " + badge(inv_sec.status)
    rows.append([f"<strong>Total</strong>",
                 f"<strong>{num(d['total'],0)}</strong>{total_badge}"])
    parts.append(_table(["Tipo", "Inválidos"], rows))
    if d["highlights"]:
        parts.append("<h3>Objetos inválidos destacados (no-SYNONYM)</h3>")
        rows = [[esc(r.get("owner")), esc(r.get("object_name")),
                 esc(r.get("object_type")), esc(r.get("last_ddl"))]
                for r in d["highlights"]]
        parts.append(_table(["Owner", "Objeto", "Tipo", "Último DDL"], rows))
    return "\n".join(parts)


def render_cat14(mv_sec: Section) -> str:
    parts = [f'<h2 id="cat14">14. Estructuras Avanzadas {_top_link()}</h2>']
    if mv_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(mv_sec, "dba_mviews"))
    elif not mv_sec.data:
        parts.append(f'<div class="note">{esc(mv_sec.note)}</div>')
    else:
        rows = [[esc(r.get("owner")), esc(r.get("mview_name")),
                 esc(r.get("compile_state")), esc(r.get("last_refresh_type"))]
                for r in mv_sec.data]
        parts.append(_table(
            ["Owner", "MView", "Compile state", "Último refresh"], rows))
    return "\n".join(parts)


def render_cat15(dp_sec: Section) -> str:
    parts = [f'<h2 id="cat15">15. Data Pump {_top_link()}</h2>']
    if dp_sec.status == "NOT_COLLECTED":
        parts.append(_nc_block(dp_sec, "dba_datapump_jobs"))
    elif not dp_sec.data:
        parts.append(f'<div class="note">{esc(dp_sec.note)}</div>')
    else:
        rows = [[esc(r.get("owner_name")), esc(r.get("job_name")),
                 esc(r.get("operation")), esc(r.get("job_mode")),
                 esc(r.get("state"))] for r in dp_sec.data]
        parts.append(_table(
            ["Owner", "Job", "Operación", "Modo", "Estado"], rows))
    return "\n".join(parts)


def render_footer(meta: dict, not_collected: list[str]) -> str:
    nc = "; ".join(not_collected) if not_collected else "ninguno"
    return f"""
</main>
<footer>
  Hoja de Vida generada por script (spec v2.0) · Base {esc(meta['db_name'])} / {esc(meta['alias'])} · Ambiente {esc(meta['environment'].upper())} · {esc(meta['date'])} COT ({COT_TZ})<br>
  Secciones NOT_COLLECTED en esta corrida: {esc(nc)}. Documento de hechos crudos; el plan de acción priorizado vive en reporte-analisis-bd-spec.
</footer>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
#  ORQUESTACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def generate_report(alias: str, inv_entry: dict, output_dir: Path,
                    rac_note: str = "") -> Path:
    """Conecta con las credenciales del paquete portable, arma el HTML y lo escribe."""
    dsn = inv_entry["dsn"]
    environment = inv_entry.get("environment", "")
    user, password = resolve_credentials(alias, environment)
    if not user or not password:
        raise RuntimeError(
            f"No se resolvieron credenciales para alias '{alias}'. "
            "Verifica config/.env y el perfil activo en config/.active_profile."
        )

    _log(f"Conectando a {alias} ({dsn}) como {user} …")
    client = OracleClient(alias, dsn, user, password)

    try:
        fname, contenido, _ = construir(client, alias, inv_entry, rac_note)
    finally:
        client.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / fname
    _log(f"Escribiendo reporte → {out_path}")
    out_path.write_text(contenido, encoding="utf-8")
    _log(f"OK — reporte generado: {out_path}")
    return out_path


ETAPAS = ["Instancia y versión", "Memoria SGA/PGA", "Almacenamiento y tablespaces", "Seguridad y usuarios",
          "Backup y Data Guard", "Generar HTML"]


def construir(client, alias: str, inv_entry: dict, rac_note: str = "",
              progreso=None) -> tuple[str, str, dict]:
    """Recolecta y arma el HTML con un cliente ya conectado (cualquier objeto con
    query()/query_one()). Devuelve (nombre_archivo, html, resumen)."""
    dsn = inv_entry.get("dsn", "")
    environment = (inv_entry.get("environment") or "").lower()
    # ── Recolección por categoría (secuencial; cada una tolera fallos), agrupada en las
    #    etapas que muestra Ops Central (el orden no cambia el resultado) ──
    avance = progreso or (lambda _etapa: None)
    _log("Recolectando categorías …")
    avance(ETAPAS[0])
    inst = safe_collect(client, collect_instance)
    os_s = safe_collect(client, collect_osstat)
    params = safe_collect(client, collect_parameters)
    comps = safe_collect(client, collect_components)
    patches = safe_collect(client, collect_patches)
    jobs = safe_collect(client, collect_scheduler_jobs)
    avance(ETAPAS[1])
    mem = safe_collect(client, collect_sga_pga)
    topsql = safe_collect(client, collect_top_sql)
    waits = safe_collect(client, collect_wait_events)
    awr = safe_collect(client, collect_awr)
    sess = safe_collect(client, collect_sessions)
    avance(ETAPAS[2])
    ts = safe_collect(client, collect_tablespaces)
    size = safe_collect(client, collect_db_size)
    segs = safe_collect(client, collect_segments)
    asm = safe_collect(client, collect_asm)
    redo = safe_collect(client, collect_redo)
    temp = safe_collect(client, collect_temp)
    undo = safe_collect(client, collect_undo)
    avance(ETAPAS[3])
    secu = safe_collect(client, collect_security)
    objs = safe_collect(client, collect_object_summary)
    invalids = safe_collect(client, collect_invalid_objects)
    mviews = safe_collect(client, collect_mviews)
    avance(ETAPAS[4])
    rman = safe_collect(client, collect_rman)
    arch = safe_collect(client, collect_archiving_fra)
    datapump = safe_collect(client, collect_datapump)

    # ── Metadata ──
    db = inst.data["database"] if inst.status == "OK" else {}
    db_name = db.get("name") or alias.upper()
    now = datetime.datetime.now()
    meta = {
        "alias": alias,
        "dsn": dsn,
        "environment": environment,
        "db_name": db_name,
        "db_unique_name": db.get("db_unique_name"),
        "dbid": db.get("dbid"),
        "date": now.strftime("%Y-%m-%d"),
        "rac_note": rac_note,
        "herramienta": inv_entry.get("herramienta", ""),
    }

    # ── Semáforos para el resumen de salud ──
    ts_status = ts.status
    rman_status = rman.status
    block_status = "OK"
    if sess.status == "OK" and sess.data["blocking"]:
        block_status = "CRITICAL"
    inv_status = invalids.status
    patch_status = patches.status
    job_status = jobs.status

    overall = worst(ts_status, rman_status, block_status, inv_status,
                    patch_status, job_status)

    def _ev_ts() -> str:
        if ts.status == "NOT_COLLECTED":
            return "No recolectado"
        crit = [r["tablespace_name"] for r in ts.data if r.get("alert_level") == "CRITICAL"]
        warn = [r["tablespace_name"] for r in ts.data if r.get("alert_level") == "WARNING"]
        return f"{len(crit)} CRITICAL, {len(warn)} WARNING (sobre pct_max_used)"

    health_rows = [
        ("Almacenamiento (tablespaces)", ts_status, _ev_ts()),
        ("Backups RMAN (7d)", rman_status,
         f"{rman.data['total']} backups, {rman.data['failed']} fallidos"
         if rman.status != "NOT_COLLECTED" else "No recolectado"),
        ("Sesiones bloqueantes", block_status,
         f"{len(sess.data['blocking'])} cadenas" if sess.status == "OK" else "No recolectado"),
        ("Objetos inválidos", inv_status,
         f"{invalids.data['total']} inválidos" if invalids.status != "NOT_COLLECTED" else "No recolectado"),
        ("Parches (RU)", patch_status, patches.note or "Ver detalle"),
        ("Jobs scheduler", job_status,
         f"{jobs.data['summary'].get('total_failures') or 0} fallas acumuladas"
         if jobs.status != "NOT_COLLECTED" else "No recolectado"),
    ]

    # ── Cards del resumen ──
    ver = ""
    if inst.status == "OK":
        i = inst.data["instance"]
        ver = i.get("version_full") or i.get("version") or "N/A"
    role_mode = "N/A"
    if db:
        role_mode = f"{db.get('database_role','?')} / {db.get('open_mode','?')}"
    n_sessions = sess.data["summary"].get("total") if sess.status == "OK" else None
    ts_crit = ts_warn = 0
    if ts.status != "NOT_COLLECTED":
        ts_crit = sum(1 for r in ts.data if r.get("alert_level") == "CRITICAL")
        ts_warn = sum(1 for r in ts.data if r.get("alert_level") == "WARNING")
    overall_klass = {"CRITICAL": "crit", "WARNING": "warn"}.get(overall, "")

    cards = [
        ("Versión", esc(ver), ""),
        ("Rol / Modo", esc(role_mode), ""),
        ("Estado general", esc(overall), overall_klass),
        ("Tamaño BD", f"{num((size.data or {}).get('total_gb'))} GB"
         if size.status == "OK" else "N/A", ""),
        ("Sesiones", num(n_sessions, 0) if n_sessions is not None else "N/A", ""),
        ("Tablespaces CRIT", str(ts_crit), "crit" if ts_crit else ""),
        ("Tablespaces WARN", str(ts_warn), "warn" if ts_warn else ""),
        ("Backups 7d (fallidos)",
         f"{rman.data['total']} ({rman.data['failed']})"
         if rman.status != "NOT_COLLECTED" else "N/A", ""),
    ]

    # ── NOT_COLLECTED tracker ──
    nc_map = {
        "Instancia": inst, "OS": os_s, "Parámetros": params, "Componentes": comps,
        "Parches": patches, "Jobs": jobs, "Tablespaces": ts, "Segmentos": segs,
        "ASM": asm, "Redo": redo, "TEMP": temp, "UNDO": undo, "RMAN": rman,
        "Archiving/FRA": arch, "Top SQL": topsql, "Wait events": waits,
        "AWR": awr, "Sesiones": sess, "Seguridad": secu, "Objetos": objs,
        "Inválidos": invalids, "MViews": mviews, "Data Pump": datapump,
    }
    not_collected = [f"{k} ({v.error})" for k, v in nc_map.items()
                     if v.status == "NOT_COLLECTED"]

    # ── Nombre de archivo + carpeta ──
    suffix = "" if environment == "prod" else f"-{environment}"
    fname = f"hoja-de-vida-database-{db_name.lower()}-{now.strftime('%Y%m%d')}{suffix}.html"

    # ── Armado por etapas (mismo orden que la escritura incremental original) ──
    avance(ETAPAS[5])
    partes: list[str] = []
    # Etapa 1
    partes.append(render_head(meta, health_rows, overall, cards))
    # Etapa 2: cat 1-3
    partes.append("\n" + render_cat1(inst, os_s, params, comps))
    partes.append("\n" + render_cat2(patches))
    partes.append("\n" + render_cat3(jobs))
    # Etapa 3: cat 4-6
    partes.append("\n" + render_cat4(ts, size.data or {}, segs, asm, redo))
    partes.append("\n" + render_cat5(temp))
    partes.append("\n" + render_cat6(undo))
    # Etapa 4: cat 7
    partes.append("\n" + render_cat7(rman, arch))
    # Etapa 5: cat 8-9
    partes.append("\n" + render_cat8(topsql, waits, mem.data or {}))
    partes.append("\n" + render_cat9(awr))
    # Etapa 6: cat 10-11
    partes.append("\n" + render_cat10(sess))
    partes.append("\n" + render_cat11(secu))
    # Etapa 7: cat 12-15 + cierre
    partes.append("\n" + render_cat12(objs))
    partes.append("\n" + render_cat13(invalids))
    partes.append("\n" + render_cat14(mviews))
    partes.append("\n" + render_cat15(datapump))
    partes.append(render_footer(meta, not_collected))

    _log(f"overall_status={overall} · NOT_COLLECTED={len(not_collected)}")
    resumen = {"overall_status": overall, "db_name": db_name, "not_collected": not_collected,
               "salud": [{"dimension": d, "estado": s} for d, s, _ in health_rows]}
    return fname, "".join(partes), resumen


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def _dedup_physical(entries: list[dict]) -> list[dict]:
    """
    Deduplica una lista de entradas del inventario a BD *físicas* únicas.

    Identidad de BD física = (environment, IP, service) del DSN. Así:
      • Los nodos RAC (mismo service en distintas IPs del cluster) NO colapsan
        entre sí por IP distinta, pero se conserva el primer alias por
        (env, service) para no emitir un reporte por nodo.
      • BD distintas que comparten service en hosts separados (p. ej. terceros
        en varias IPs) se tratan según (env, service): se conserva una.
    Se prioriza el primer alias encontrado (orden del inventario).

    Nota: para RAC un solo reporte por BD física es lo correcto (spec §6.6).
    El criterio usado es (environment, service) — el representante es el primer
    alias de ese grupo, típicamente el nodo 1.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for e in entries:
        dsn = e.get("dsn", "")
        service = dsn.split("/")[-1].strip().lower() if "/" in dsn else dsn.lower()
        env = (e.get("environment") or "").lower()
        key = (env, service)
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera la Hoja de Vida de Base de Datos (HTML) desde un script Python.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  # Una sola BD\n"
            "  python generar_hoja_vida.py --alias prod_capi1\n"
            "  python generar_hoja_vida.py --alias prod_capi1 --rac-node 1\n"
            "  # Todas las BD de un ambiente (dedup de nodos RAC)\n"
            "  python generar_hoja_vida.py --env prod\n"
            "  python generar_hoja_vida.py --env test\n"
            "  # Todas las BD del inventario\n"
            "  python generar_hoja_vida.py --all\n"
        ),
    )
    parser.add_argument("--alias", default="",
                        help="Alias de una BD en config/databases.json (ej. prod_capi1).")
    parser.add_argument("--env", default="",
                        help="Genera TODAS las BD de este ambiente (prod/test/…). "
                             "Con --alias, solo fuerza el ambiente de esa BD.")
    parser.add_argument("--all", action="store_true",
                        help="Genera TODAS las BD del inventario (todos los ambientes).")
    parser.add_argument("--output-dir", default="",
                        help="Carpeta de salida. Default: reportes/HV_{env}_{YYYYMMDD}/ (dentro del paquete)")
    parser.add_argument("--rac-node", default="",
                        help="Número/etiqueta de nodo RAC (solo documental; solo aplica con --alias).")
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY),
                        help="Ruta al inventario JSON (default: config/databases.json).")
    args = parser.parse_args()

    # Exigir exactamente un modo de selección.
    if not (args.alias or args.env or args.all):
        _log("ERROR: indica un modo de selección: --alias <alias>, "
             "--env <ambiente> o --all.")
        return 2
    if args.alias and args.all:
        _log("ERROR: --alias y --all son mutuamente excluyentes.")
        return 2

    load_env()
    init_thick_if_configured()

    inventory = load_inventory(Path(args.inventory))
    now = datetime.datetime.now()

    # ── Modo 1 BD (--alias) ───────────────────────────────────────────────
    if args.alias:
        if args.alias not in inventory:
            _log(f"ERROR: alias '{args.alias}' no está en el inventario "
                 f"({args.inventory}).")
            near = [a for a in inventory if args.alias.lower() in a.lower()]
            if near:
                _log(f"¿Quisiste decir?: {', '.join(near[:10])}")
            return 2

        entry = inventory[args.alias]
        environment = args.env.strip() or entry.get("environment", "")
        entry["environment"] = environment
        output_dir = (Path(args.output_dir) if args.output_dir
                      else REPORTS_DIR / f"HV_{environment}_{now.strftime('%Y%m%d')}")
        rac_note = f"Nodo RAC {args.rac_node}" if args.rac_node else ""
        try:
            out_path = generate_report(args.alias, entry, output_dir, rac_note)
        except Exception as e:  # noqa: BLE001
            _log(f"ERROR generando reporte: {e}")
            return 1
        print(str(out_path))
        return 0

    # ── Modo masivo (--env / --all) ───────────────────────────────────────
    targets = list(inventory.values())
    if args.env:
        env_l = args.env.strip().lower()
        targets = [e for e in targets if (e.get("environment") or "").lower() == env_l]
        if not targets:
            _log(f"ERROR: no hay BD con environment='{args.env}' en el inventario.")
            return 2
        label = args.env.strip()
    else:
        label = "all"

    # Deduplicar por BD física (evita repetir nodos RAC que comparten IP+service).
    unique = _dedup_physical(targets)
    _log(f"Modo masivo ({label}): {len(targets)} entradas → "
         f"{len(unique)} BD físicas tras dedup RAC.")

    ok, failed = 0, 0
    fail_detail: list[str] = []
    generated: list[str] = []

    for entry in unique:
        alias = entry["alias"]
        environment = entry.get("environment", "")
        out_dir = (Path(args.output_dir) if args.output_dir
                   else REPORTS_DIR / f"HV_{environment}_{now.strftime('%Y%m%d')}")
        _log(f"→ [{ok + failed + 1}/{len(unique)}] {alias} ({entry['dsn']}) …")
        try:
            out_path = generate_report(alias, dict(entry), out_dir, "")
            ok += 1
            generated.append(str(out_path))
        except Exception as e:  # noqa: BLE001
            failed += 1
            msg = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
            fail_detail.append(f"{alias}: {msg}")
            _log(f"   FALLO: {msg}")

    # Resumen del lote (a stderr) + rutas generadas (a stdout).
    _log("═" * 60)
    _log(f"RESUMEN LOTE ({label}): {ok} OK · {failed} fallidos "
         f"de {len(unique)} BD físicas.")
    if fail_detail:
        _log("Fallidos:")
        for d in fail_detail:
            _log(f"  · {d}")
    for g in generated:
        print(g)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
