#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_reporte_analisis.py
═══════════════════════════
Reporte de Análisis de Base de Datos (spec reporte-analisis-bd v1.0) generado por
script, sin modelo de lenguaje: toma los hechos crudos de la BD y los convierte en
rankings Top-N, veredictos, registro de alertas y un plan de acción priorizado con
reglas fijas.

  • Solo lectura (100 % SELECT). Mismo visual verde que la Hoja de Vida.
  • Independiente de la Hoja de Vida: reconsulta la BD.
  • Sin histórico persistido (piloto): todo resumen estadístico queda UNKNOWN
    — nunca NORMAL —, y el soporte de parches se evalúa solo por antigüedad.
  • Archivo: reporte-analisis-database-{db_name}-{YYYYMMDD}[-env].html

Uso (paquete portable):
    python generar_reporte_analisis.py --alias prod_capi1
    python generar_reporte_analisis.py --env prod
    python generar_reporte_analisis.py --alias prod_capi1 --muestra   # formato, sin conexión
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

try:
    from . import comun as c
    from . import hoja_vida as hv
except ImportError:  # paquete portable
    import reportes_comun as c  # type: ignore
    import generar_hoja_vida as hv  # type: ignore

esc, num, badge, tabla, Section = c.esc, c.num, c.badge, c.tabla, c.Section

# ══════════════════════════════════════════════════════════════════════════════
#  UMBRALES (spec §6.1 / §7.1)
# ══════════════════════════════════════════════════════════════════════════════
DBA_DORMIDO_WARN, DBA_DORMIDO_CRIT = 90, 180          # días sin login
SGA_GAIN_WARN, SGA_GAIN_CRIT = 5.0, 15.0              # next_tier_gain_pct
SES_WARN, SES_CRIT = 80.0, 90.0                       # % de sessions/processes
STALE_WARN = 10                                       # tablas con estadísticas obsoletas
REBUILD_WARN = 1                                      # índices con blevel > 3
ALERT_CRIT_CODES = ("ORA-00600", "ORA-07445", "ORA-04031", "ORA-00257")
SIN_HISTORICO = "UNKNOWN — historial insuficiente (1/12 snapshots)"

# finding_type → prioridad (§7.1). Sin histórico, todo hallazgo es SNAPSHOT_UNICO y
# se prioriza de forma conservadora como si fuera persistente.
PRIORIDAD = {("PERSISTENT_DEVIATION", "CRITICAL"): "URGENTE", ("PERSISTENT_DEVIATION", "WARNING"): "ALTA",
             ("SNAPSHOT_UNICO", "CRITICAL"): "URGENTE", ("SNAPSHOT_UNICO", "WARNING"): "ALTA",
             ("TRANSIENT_SPIKE", "CRITICAL"): "MEDIA", ("TRANSIENT_SPIKE", "WARNING"): "BAJA"}
ORDEN_PRIORIDAD = {"URGENTE": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}

# dominio → acción de Ops Central que atiende el hallazgo (referencia para el plan de acción)
ACCION_SUGERIDA = {"STORAGE": "bd.ts.agregar_df", "BACKUP": "bd.salud.rman", "JOBS": "bd.job.reintentar",
                   "PERF": "bd.rep.sql_costosas", "SEC": "bd.usr.estado", "OBJ": "bd.inv.listar",
                   "STATS": "bd.stats.recolectar", "ALERTLOG": "bd.salud.alertlog", "SES": "bd.ses.flota",
                   "PATCH": None, "ASM": "bd.rep.diskgroups"}

# ══════════════════════════════════════════════════════════════════════════════
#  CONSULTAS (solo lectura)
# ══════════════════════════════════════════════════════════════════════════════
SQL = {
    "jobs_total": """
        SELECT COUNT(*) AS total_fallidos FROM dba_scheduler_jobs WHERE failure_count > 0""",
    "jobs_fallidos": """
        SELECT * FROM (
            SELECT owner, job_name, state, enabled, run_count, failure_count,
                   ROUND(100 * failure_count / NULLIF(run_count, 0), 1) AS failure_pct,
                   TO_CHAR(CAST(last_start_date AS DATE),'YYYY-MM-DD HH24:MI') AS last_start
            FROM dba_scheduler_jobs
            WHERE failure_count > 0
            ORDER BY failure_count DESC
        ) WHERE ROWNUM <= 25""",
    "seg_tamano": """
        SELECT * FROM (
            SELECT owner, segment_name, partition_name, segment_type, tablespace_name,
                   ROUND(bytes/1024/1024/1024, 2) AS gb, extents
            FROM dba_segments
            ORDER BY bytes DESC
        ) WHERE ROWNUM <= 100""",
    "seg_extents": """
        SELECT * FROM (
            SELECT owner, segment_name, partition_name, segment_type, tablespace_name,
                   ROUND(bytes/1024/1024/1024, 2) AS gb, extents
            FROM dba_segments
            ORDER BY extents DESC
        ) WHERE ROWNUM <= 100""",
    "esquemas": """
        SELECT * FROM (
            SELECT owner, ROUND(SUM(bytes)/1024/1024/1024, 2) AS gb,
                   ROUND(100 * RATIO_TO_REPORT(SUM(bytes)) OVER (), 2) AS pct_of_database_total
            FROM dba_segments
            GROUP BY owner
            ORDER BY SUM(bytes) DESC
        ) WHERE ROWNUM <= 20""",
    "indices": """
        SELECT * FROM (
            SELECT i.owner, i.index_name, i.table_name, i.tablespace_name,
                   ROUND(s.bytes/1024/1024/1024, 2) AS gb, i.blevel, i.leaf_blocks
            FROM dba_indexes i
            JOIN dba_segments s ON s.owner = i.owner AND s.segment_name = i.index_name
                               AND s.segment_type = 'INDEX'
            ORDER BY s.bytes DESC
        ) WHERE ROWNUM <= 25""",
    "redo_dia": """
        SELECT TO_CHAR(first_time,'YYYY-MM-DD') AS dia, COUNT(*) AS switches
        FROM v$log_history
        WHERE first_time > SYSDATE - 7
        GROUP BY TO_CHAR(first_time,'YYYY-MM-DD')
        ORDER BY dia""",
    "rman_7d": """
        SELECT input_type, status,
               TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS start_time,
               ROUND(elapsed_seconds/60, 1) AS duration_min,
               ROUND(output_bytes/1024/1024/1024, 2) AS output_gb
        FROM v$rman_backup_job_details
        WHERE start_time > SYSDATE - 7
        ORDER BY start_time DESC""",
    "sets_30d": """
        SELECT backup_type, incremental_level, COUNT(*) AS sets,
               TO_CHAR(MAX(completion_time),'YYYY-MM-DD HH24:MI') AS ultimo
        FROM v$backup_set
        WHERE completion_time > SYSDATE - 30
        GROUP BY backup_type, incremental_level
        ORDER BY backup_type, incremental_level""",
    "datafile_7d": """
        SELECT COUNT(*) AS sets_d, TO_CHAR(MAX(completion_time),'YYYY-MM-DD HH24:MI') AS ultimo_d
        FROM v$backup_set
        WHERE backup_type = 'D' AND completion_time > SYSDATE - 7""",
    "log_mode": """
        SELECT log_mode, force_logging, flashback_on FROM v$database""",
    "heatmap": """
        SELECT TO_CHAR(completion_time,'YYYY-MM-DD') AS dia, COUNT(*) AS archivos,
               ROUND(SUM(blocks * block_size)/1024/1024/1024, 2) AS gb
        FROM v$archived_log
        WHERE completion_time > SYSDATE - 14 AND dest_id = 1
        GROUP BY TO_CHAR(completion_time,'YYYY-MM-DD')
        ORDER BY dia""",
    "sga_advice": """
        SELECT sga_size, sga_size_factor, estd_db_time, estd_physical_reads
        FROM v$sga_target_advice
        ORDER BY sga_size_factor""",
    "pga_over": """
        SELECT value AS over_allocation_count FROM v$pgastat WHERE name = 'over allocation count'""",
    "pga_advice": """
        SELECT ROUND(pga_target_for_estimate/1024/1024) AS pga_mb, pga_target_factor,
               estd_pga_cache_hit_percentage AS hit_pct, estd_overalloc_count
        FROM v$pga_target_advice
        ORDER BY pga_target_factor""",
    "top_sql": """
        SELECT * FROM (
            SELECT sql_id, parsing_schema_name, executions,
                   ROUND(elapsed_time/1e6, 2) AS elapsed_sec,
                   ROUND(cpu_time/1e6, 2) AS cpu_sec,
                   ROUND(elapsed_time/1e6/NULLIF(executions,0), 4) AS elapsed_por_exec,
                   buffer_gets, disk_reads,
                   SUBSTR(sql_text, 1, 100) AS sql_text
            FROM v$sqlarea
            WHERE executions > 0
            ORDER BY elapsed_time DESC
        ) WHERE ROWNUM <= 50""",
    "waits": """
        SELECT * FROM (
            SELECT event, wait_class, total_waits,
                   ROUND(time_waited/100, 2) AS time_waited_sec,
                   ROUND(average_wait*10, 2) AS avg_wait_ms
            FROM v$system_event
            WHERE wait_class <> 'Idle'
            ORDER BY time_waited DESC
        ) WHERE ROWNUM <= 20""",
    "sta": """
        SELECT * FROM (
            SELECT t.task_name, f.type AS tipo_hallazgo, SUBSTR(f.message, 1, 200) AS mensaje,
                   r.type AS tipo_recomendacion, r.benefit
            FROM dba_advisor_tasks t
            JOIN dba_advisor_findings f ON f.task_id = t.task_id
            LEFT JOIN dba_advisor_recommendations r ON r.task_id = f.task_id AND r.finding_id = f.finding_id
            WHERE t.advisor_name = 'SQL Tuning Advisor' AND t.execution_end > SYSDATE - 30
            ORDER BY r.benefit DESC NULLS LAST
        ) WHERE ROWNUM <= 20""",
    "dba_login": """
        SELECT u.username, u.account_status,
               TO_CHAR(CAST(u.last_login AS DATE),'YYYY-MM-DD') AS last_login,
               ROUND(SYSDATE - CAST(u.last_login AS DATE)) AS dias_sin_login
        FROM dba_role_privs p
        JOIN dba_users u ON u.username = p.grantee
        WHERE p.granted_role = 'DBA'
        ORDER BY dias_sin_login DESC NULLS FIRST""",
    "roles_huerfanos": """
        SELECT r.role
        FROM dba_roles r
        WHERE r.oracle_maintained = 'N'
          AND NOT EXISTS (SELECT 1 FROM dba_role_privs p WHERE p.granted_role = r.role)
        ORDER BY r.role""",
    "registro": """
        SELECT comp_id, comp_name, version, status FROM dba_registry ORDER BY comp_id""",
    "invalidos_owner": """
        SELECT owner, object_type, COUNT(*) AS cnt
        FROM dba_objects
        WHERE status = 'INVALID'
        GROUP BY owner, object_type
        ORDER BY cnt DESC""",
    "rebuild": """
        SELECT * FROM (
            SELECT owner, index_name, table_name, blevel, leaf_blocks,
                   TO_CHAR(last_analyzed,'YYYY-MM-DD') AS last_analyzed
            FROM dba_indexes
            WHERE blevel > 3
            ORDER BY blevel DESC, leaf_blocks DESC
        ) WHERE ROWNUM <= 50""",
    "stale_total": """
        SELECT COUNT(*) AS total
        FROM dba_tab_statistics s
        WHERE s.stale_stats = 'YES'
          AND s.owner NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')""",
    "stale": """
        SELECT * FROM (
            SELECT s.owner, s.table_name, s.num_rows,
                   TO_CHAR(s.last_analyzed,'YYYY-MM-DD') AS last_analyzed
            FROM dba_tab_statistics s
            WHERE s.stale_stats = 'YES' AND s.object_type = 'TABLE'
              AND s.owner NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
            ORDER BY s.num_rows DESC NULLS LAST
        ) WHERE ROWNUM <= 50""",
    "alertlog": """
        SELECT * FROM (
            SELECT REGEXP_SUBSTR(message_text, 'ORA-[0-9]{5}') AS codigo, COUNT(*) AS cnt,
                   TO_CHAR(MAX(CAST(originating_timestamp AS DATE)),'YYYY-MM-DD HH24:MI') AS ultimo
            FROM v$diag_alert_ext
            WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '7' DAY
              AND message_text LIKE '%ORA-%'
            GROUP BY REGEXP_SUBSTR(message_text, 'ORA-[0-9]{5}')
            ORDER BY cnt DESC
        ) WHERE ROWNUM <= 20""",
    "limites": """
        SELECT resource_name, current_utilization, max_utilization, TRIM(limit_value) AS limit_value
        FROM v$resource_limit
        WHERE resource_name IN ('sessions', 'processes')""",
    "sesiones_usuario": """
        SELECT * FROM (
            SELECT username, COUNT(*) AS total,
                   SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS activas
            FROM v$session
            WHERE type = 'USER' AND username IS NOT NULL
            GROUP BY username
            ORDER BY COUNT(*) DESC
        ) WHERE ROWNUM <= 15""",
}


def _q(client, nombre: str, params: dict | None = None, max_rows: int = 500) -> list[dict]:
    return c.q(client, SQL[nombre], params, max_rows)


def _q1(client, nombre: str) -> dict:
    return c.q1(client, SQL[nombre])


# ══════════════════════════════════════════════════════════════════════════════
#  RECOLECCIÓN (cada función es una sección; safe_collect la vuelve NOT_COLLECTED)
# ══════════════════════════════════════════════════════════════════════════════

def col_jobs(client) -> dict:
    return {"top": _q(client, "jobs_fallidos"), "total": _q1(client, "jobs_total").get("total_fallidos") or 0}


def col_storage(client) -> dict:
    ts = hv.collect_tablespaces(client)
    return {"tablespaces": ts.data or [], "seg_tamano": _q(client, "seg_tamano"),
            "seg_extents": _q(client, "seg_extents"), "esquemas": _q(client, "esquemas"),
            "indices": _q(client, "indices")}


def col_redo(client) -> list[dict]:
    return _q(client, "redo_dia")


def col_backup(client) -> dict:
    return {"rman": _q(client, "rman_7d"), "sets": _q(client, "sets_30d"),
            "d7": _q1(client, "datafile_7d"), "db": _q1(client, "log_mode")}


def col_heatmap(client) -> list[dict]:
    return _q(client, "heatmap")


def col_memoria(client) -> dict:
    return {"sga": _q(client, "sga_advice"), "pga": _q(client, "pga_advice"),
            "over": c.a_float(_q1(client, "pga_over").get("over_allocation_count"))}


def col_top_sql(client) -> list[dict]:
    return _q(client, "top_sql")


def col_waits(client) -> list[dict]:
    return _q(client, "waits")


def col_sta(client) -> list[dict]:
    return _q(client, "sta")


def col_seguridad(client) -> dict:
    return {"dba": _q(client, "dba_login"), "huerfanos": _q(client, "roles_huerfanos"),
            "registro": _q(client, "registro")}


def col_objetos(client) -> dict:
    return {"invalidos": _q(client, "invalidos_owner"), "rebuild": _q(client, "rebuild")}


def col_stale(client) -> dict:
    return {"total": _q1(client, "stale_total").get("total") or 0, "top": _q(client, "stale")}


def col_alertlog(client) -> list[dict]:
    return [r for r in _q(client, "alertlog") if r.get("codigo")]


def col_sesiones(client) -> dict:
    return {"limites": _q(client, "limites"), "usuarios": _q(client, "sesiones_usuario")}


# ══════════════════════════════════════════════════════════════════════════════
#  VEREDICTOS → REGISTRO DE ALERTAS
# ══════════════════════════════════════════════════════════════════════════════

class Registro:
    """alert_registry: cada hallazgo WARNING/CRITICAL con id {DOMINIO}-{NNN}."""

    def __init__(self, ts: str):
        self.alertas: list[dict] = []
        self._n: dict[str, int] = defaultdict(int)
        self.ts = ts

    def agregar(self, dominio: str, metrica: str, valor: Any, umbral: str, severidad: str,
                observacion: str, recomendacion: str, owner: str | None = None,
                finding_type: str = "SNAPSHOT_UNICO") -> None:
        self._n[dominio] += 1
        self.alertas.append({
            "id": f"{dominio}-{self._n[dominio]:03d}", "dominio": dominio, "metrica": metrica,
            "valor": valor, "umbral": umbral, "severidad": severidad, "observacion": observacion,
            "recomendacion": recomendacion, "finding_type": finding_type,
            "prioridad": PRIORIDAD.get((finding_type, severidad), "MEDIA"), "owner": owner,
            "accion": ACCION_SUGERIDA.get(dominio), "timestamp": self.ts, "first_seen_snapshot_id": None,
        })


def _sga_gain(sga: list[dict]) -> float | None:
    """Ganancia % de DB time al pasar del tamaño actual (factor 1) al siguiente escalón."""
    actual = next((r for r in sga if c.a_float(r.get("sga_size_factor")) == 1.0), None)
    mayores = [r for r in sga if (c.a_float(r.get("sga_size_factor")) or 0) > 1.0]
    if not actual or not mayores or not c.a_float(actual.get("estd_db_time")):
        return None
    sig = min(mayores, key=lambda r: c.a_float(r["sga_size_factor"]))
    t0, t1 = c.a_float(actual["estd_db_time"]), c.a_float(sig.get("estd_db_time")) or 0
    return round(100 * (t0 - t1) / t0, 2)


def evaluar(s: dict[str, Section], reg: Registro) -> dict:
    """Aplica los umbrales a cada sección. Devuelve datos derivados para el render."""
    der: dict[str, Any] = {}
    if s["jobs"].status == "OK":
        for j in s["jobs"].data["top"]:
            sev = "CRITICAL" if (c.a_float(j.get("failure_pct")) or 0) >= 100 or j.get("state") == "BROKEN" \
                else "WARNING"
            reg.agregar("JOBS", f"failure_count {j['owner']}.{j['job_name']}", j.get("failure_count"),
                        "> 0 (CRITICAL si BROKEN o 100 % de fallas)", sev,
                        f"Job en estado {j.get('state')} con {j.get('failure_count')} fallas "
                        f"de {j.get('run_count')} ejecuciones.",
                        "Revisar dba_scheduler_job_run_details, corregir la causa y reintentar el job.",
                        owner=j.get("owner"))
    if s["storage"].status == "OK":
        for t in s["storage"].data["tablespaces"]:
            nivel = t.get("alert_level")
            if nivel in ("WARNING", "CRITICAL"):
                reg.agregar("STORAGE", f"pct_max_used {t['tablespace_name']}", t.get("pct_max_used"),
                            f"WARNING ≥ {hv.TS_WARN} %, CRITICAL ≥ {hv.TS_CRIT} %", nivel,
                            f"Tablespace {t['tablespace_name']} al {num(t.get('pct_max_used'))} % de su "
                            "máximo (autoextend incluido).",
                            "Agregar datafile o ampliar maxsize; revisar los segmentos que más crecen.")
    if s["backup"].status == "OK":
        b = s["backup"].data
        d7 = int(b["d7"].get("sets_d") or 0)
        noarch = (b["db"].get("log_mode") or "").upper() == "NOARCHIVELOG"
        der["backup_valido_7d"] = d7 > 0
        if not d7:
            reg.agregar("BACKUP", "has_valid_backup_last_7d (backup_type='D')", "false",
                        "≥ 1 backup de datafiles en 7 días", "CRITICAL" if noarch else "WARNING",
                        "No hay backup de datafiles (tipo D) en los últimos 7 días"
                        + (" y la base está en NOARCHIVELOG." if noarch else "; solo archivelogs/controlfile."),
                        "Ejecutar un backup incremental nivel 0/1 y validar la programación de RMAN.")
        fallidos = [r for r in b["rman"] if r.get("status") not in ("COMPLETED", "RUNNING",
                                                                   "COMPLETED WITH WARNINGS")]
        if fallidos:
            reg.agregar("BACKUP", "rman_jobs_fallidos_7d", len(fallidos), "0", "WARNING",
                        f"{len(fallidos)} trabajo(s) RMAN sin completar en 7 días.",
                        "Revisar el log de RMAN de cada trabajo fallido.")
    if s["memoria"].status == "OK":
        m = s["memoria"].data
        gain = _sga_gain(m["sga"])
        der["sga_gain"] = gain
        if gain is not None and gain >= SGA_GAIN_WARN:
            reg.agregar("PERF", "sga_target_advice.next_tier_gain_pct", gain,
                        f"WARNING {SGA_GAIN_WARN}-{SGA_GAIN_CRIT} %, CRITICAL > {SGA_GAIN_CRIT} %",
                        "CRITICAL" if gain > SGA_GAIN_CRIT else "WARNING",
                        f"Subir la SGA al siguiente escalón reduciría el DB time un {gain} %.",
                        "Evaluar aumento de sga_target en la próxima ventana.")
        if (m["over"] or 0) > 0:
            reg.agregar("PERF", "pga over allocation count", int(m["over"]), "0", "CRITICAL",
                        "La PGA se sobreasignó por encima de pga_aggregate_target.",
                        "Aumentar pga_aggregate_target según v$pga_target_advice.")
    if s["seguridad"].status == "OK":
        for u in s["seguridad"].data["dba"]:
            dias = c.a_float(u.get("dias_sin_login"))
            if dias is not None and dias > DBA_DORMIDO_WARN:
                reg.agregar("SEC", f"days_since_last_login {u['username']}", int(dias),
                            f"WARNING > {DBA_DORMIDO_WARN}, CRITICAL > {DBA_DORMIDO_CRIT}",
                            "CRITICAL" if dias > DBA_DORMIDO_CRIT else "WARNING",
                            f"Cuenta con rol DBA sin ingresar hace {int(dias)} días.",
                            "Revocar el rol DBA o bloquear la cuenta si ya no se usa.", owner=u["username"])
    if s["objetos"].status == "OK":
        o = s["objetos"].data
        total = sum(int(r.get("cnt") or 0) for r in o["invalidos"])
        der["invalidos_total"] = total
        if total > hv.INVALID_WARN:
            reg.agregar("OBJ", "objetos_invalidos", total, f"WARNING > {hv.INVALID_WARN}, "
                        f"CRITICAL > {hv.INVALID_CRIT}", "CRITICAL" if total > hv.INVALID_CRIT else "WARNING",
                        f"{total} objetos inválidos fuera de SYS.", "Recompilar y revisar la línea base.")
        por_owner: dict[str, int] = defaultdict(int)
        for r in o["invalidos"]:
            por_owner[r["owner"]] += int(r.get("cnt") or 0)
        der["invalidos_owner"] = dict(por_owner)
        if len(o["rebuild"]) >= REBUILD_WARN:
            reg.agregar("OBJ", "indices blevel > 3", len(o["rebuild"]), "0", "WARNING",
                        f"{len(o['rebuild'])} índice(s) con blevel > 3.",
                        "Evaluar rebuild/coalesce en ventana de mantenimiento.")
    if s["stale"].status == "OK" and int(s["stale"].data["total"]) >= STALE_WARN:
        owners = sorted({r["owner"] for r in s["stale"].data["top"]})
        reg.agregar("STATS", "tables_with_stale_statistics", s["stale"].data["total"], f"< {STALE_WARN}",
                    "WARNING", f"{s['stale'].data['total']} tablas con estadísticas obsoletas "
                    f"({', '.join(owners[:5])}).", "Recolectar estadísticas de los esquemas afectados.")
    if s["alertlog"].status == "OK":
        for r in s["alertlog"].data:
            sev = "CRITICAL" if r["codigo"] in ALERT_CRIT_CODES else "WARNING"
            reg.agregar("ALERTLOG", f"{r['codigo']} en 7 días", r.get("cnt"), "0", sev,
                        f"{r['codigo']} aparece {r.get('cnt')} vez/veces; último {r.get('ultimo')}.",
                        "Revisar el alert log y los trace asociados.")
    if s["sesiones"].status == "OK":
        for r in s["sesiones"].data["limites"]:
            lim = c.a_float(r.get("limit_value"))
            act = c.a_float(r.get("current_utilization"))
            if lim and act is not None:
                pct = round(100 * act / lim, 1)
                r["pct_usage"] = pct
                if pct >= SES_WARN:
                    reg.agregar("SES", f"{r['resource_name']} pct_usage", pct,
                                f"WARNING ≥ {SES_WARN} %, CRITICAL ≥ {SES_CRIT} %",
                                "CRITICAL" if pct >= SES_CRIT else "WARNING",
                                f"{r['resource_name']} en {int(act)} de {int(lim)}.",
                                "Revisar pools de conexión de las aplicaciones o ampliar el parámetro.")
    if s["parches"].status in ("WARNING", "CRITICAL"):
        reg.agregar("PATCH", "dias_desde_ultimo_parche", s["parches"].note, f"WARNING > {hv.PATCH_WARN_DAYS} "
                    f"días, CRITICAL > {hv.PATCH_CRIT_DAYS}", s["parches"].status, s["parches"].note,
                    "Planear la aplicación del último Release Update soportado.")
    return der


def correlaciones(alertas: list[dict], der: dict) -> list[dict]:
    """§7.3: hallazgos de distintos dominios sobre el mismo owner → un hallazgo raíz."""
    por_owner: dict[str, set[str]] = defaultdict(set)
    for a in alertas:
        if a.get("owner"):
            por_owner[a["owner"]].add(a["dominio"])
    for owner, cnt in (der.get("invalidos_owner") or {}).items():
        if cnt:
            por_owner[owner].add("OBJ")
    return [{"owner": o, "dominios": sorted(d)} for o, d in sorted(por_owner.items()) if len(d) >= 2]


# ══════════════════════════════════════════════════════════════════════════════
#  RENDER
# ══════════════════════════════════════════════════════════════════════════════
TOC = [("cat0", "0. Metadata del Análisis"), ("cat1", "1. Jobs — Análisis de Fallos"),
       ("cat2", "2. Storage — Rankings y Tendencias"), ("cat3", "3. Backups — Ventanas y Riesgo"),
       ("cat4", "4. Rendimiento"), ("cat5", "5. Seguridad"), ("cat6", "6. Objetos"),
       ("cat7", "7. Sesiones"), ("cat8", "8. Parches"), ("cat9", "9. Resumen, Plan de Acción y Alertas")]

CSS_EXTRA = """
  .p-URGENTE{background:var(--rojo);} .p-ALTA{background:var(--ambar);} .p-MEDIA{background:#2563eb;}
  .p-BAJA{background:var(--gris);}
  .heat{display:inline-block;min-width:44px;text-align:center;padding:4px 6px;margin:2px;border-radius:4px;font-size:.78rem;color:#fff;}
  .fuente{font-size:.78rem;color:var(--gris);margin:-4px 0 8px;}
"""


def _h2(aid: str, titulo: str, fuente: str) -> str:
    return (f'<a name="{aid}"></a><h2 id="{aid}">{esc(titulo)} {hv._top_link()}</h2>'
            f'<div class="fuente">Fuente: {esc(fuente)}</div>')


def _prio(p: str) -> str:
    return f'<span class="badge p-{esc(p)}">{esc(p)}</span>'


def _filas(rows: list[dict], cols: list[tuple[str, str, str]]) -> list[list[str]]:
    """cols = (clave, encabezado, formato) con formato '' | 'n0' | 'n2' | 'mono'."""
    out = []
    for r in rows:
        fila = []
        for k, _, f in cols:
            v = r.get(k)
            if f == "n0":
                fila.append(num(v, 0))
            elif f == "n2":
                fila.append(num(v))
            elif f == "mono":
                fila.append(f'<span class="mono">{esc(v)}</span>')
            else:
                fila.append(esc(v) if v is not None else "")
        out.append(fila)
    return out


def _t(rows: list[dict], cols: list[tuple[str, str, str]], vacio_txt: str = "Sin registros.") -> str:
    if not rows:
        return c.vacio(vacio_txt)
    return tabla([esc(h) for _, h, _ in cols], _filas(rows, cols))


def _sec(section: Section, etiqueta: str, fn) -> str:
    return c.nc(section, etiqueta) if section.status == "NOT_COLLECTED" else fn(section.data)


def render(meta: dict, s: dict[str, Section], reg: Registro, der: dict, overall: str,
           raiz: list[dict], nc_list: list[str]) -> str:
    alertas = sorted(reg.alertas, key=lambda a: (ORDEN_PRIORIDAD[a["prioridad"]], a["id"]))
    n_urg = sum(1 for a in alertas if a["prioridad"] == "URGENTE")
    n_alta = sum(1 for a in alertas if a["prioridad"] == "ALTA")
    klass = {"CRITICAL": "crit", "WARNING": "warn"}.get(overall, "")
    p: list[str] = []
    p.append(f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reporte de Análisis — {esc(meta['db_name'])} — {esc(meta['date'])}</title>
<style>{c.CSS_VERDE}{CSS_EXTRA}</style>
</head>
<body>
<header>
  <h1>Reporte de Análisis de Base de Datos — {esc(meta['db_name'])}</h1>
  <div class="sub">Alias: <strong>{esc(meta['alias'])}</strong> · Ambiente: <strong>{esc(meta['environment'].upper())}</strong> · snapshot base: {esc(meta['source_hv_report_name'] or 'reconsulta directa')} · Estado combinado: <strong>{esc(overall)}</strong> · Generado {esc(meta['date'])} (COT, {c.COT_TZ})</div>
</header>
<main>
<a name="top"></a>
{meta.get('aviso', '')}
<div class="cards">
  <div class="card {klass}"><div class="lbl">Estado combinado</div><div class="val">{esc(overall)}</div></div>
  <div class="card{' crit' if n_urg else ''}"><div class="lbl">Prioridad URGENTE</div><div class="val">{n_urg}</div></div>
  <div class="card{' warn' if n_alta else ''}"><div class="lbl">Prioridad ALTA</div><div class="val">{n_alta}</div></div>
  <div class="card"><div class="lbl">Alertas registradas</div><div class="val">{len(alertas)}</div></div>
  <div class="card"><div class="lbl">Secciones NOT_COLLECTED</div><div class="val">{len(nc_list)}</div></div>
</div>
<div class="toc">
{chr(10).join(f'  <a href="#{a}">{esc(t)}</a><br>' for a, t in TOC)}
</div>
""")
    # 0. metadata
    p.append(_h2("cat0", "0. Metadata del Análisis", "analysis_report_metadata"))
    p.append(tabla(["Atributo", "Valor"], [
        ["Documento", "Reporte de Análisis de Base de Datos (spec v1.0) — generado por script, sin LLM"],
        ["Base de datos", esc(meta["db_name"])], ["Alias", f'<span class="mono">{esc(meta["alias"])}</span>'],
        ["DSN", f'<span class="mono">{esc(meta["dsn"])}</span>'], ["Ambiente", esc(meta["environment"].upper())],
        ["Fecha de generación", f"{esc(meta['date'])} (COT, {c.COT_TZ})"],
        ["source_hv_report_name", esc(meta["source_hv_report_name"] or "N/A — análisis independiente")],
        ["Histórico (metric_history)", esc(SIN_HISTORICO)],
        ["key_changes_since_last_report", "Vacío — no hay snapshot anterior de esta BD para comparar."],
        ["Herramienta", f'<span class="mono">{esc(meta["herramienta"])}</span>'],
    ]))
    # 1. jobs
    p.append(_h2("cat1", "1. Jobs — Análisis de Fallos", "scheduler_jobs_failed — Hoja de Vida §3"))
    p.append(_sec(s["jobs"], "Jobs", lambda d: _t(d["top"], [
        ("owner", "Owner", ""), ("job_name", "Job", "mono"), ("state", "Estado", ""),
        ("enabled", "Habilitado", ""), ("run_count", "Ejecuciones", "n0"), ("failure_count", "Fallas", "n0"),
        ("failure_pct", "% falla", "n2"), ("last_start", "Último inicio", "")], "Sin jobs con fallas.")
        + f'<div class="note">Top-25 por failure_count. Total de jobs con fallas: <strong>{num(d["total"], 0)}</strong>.</div>'))
    # 2. storage
    p.append(_h2("cat2", "2. Storage — Rankings y Tendencias",
                 "storage_statistical_summary, top_100_segments_* , top_schemas, top_indexes — Hoja de Vida §4"))

    def _storage(d):
        ts_rows = [[esc(t.get("tablespace_name")), num(t.get("max_mb"), 0), num(t.get("used_mb"), 0),
                    num(t.get("pct_max_used")), badge(t.get("alert_level", "UNKNOWN"))] for t in d["tablespaces"]]
        out = ["<h3>Tablespaces (pct_max_used, autoextend incluido)</h3>",
               tabla(["Tablespace", "Máx (MB)", "Usado (MB)", "% máx usado", "Estado"], ts_rows)
               if ts_rows else c.vacio("Sin tablespaces."),
               "<h3>Resumen estadístico</h3>",
               tabla(["Métrica", "Actual", "Media", "σ", "Veredicto"],
                     [["Tamaño total de segmentos (GB)", num(sum(c.a_float(e.get('gb')) or 0 for e in d['esquemas'])),
                       "N/A", "N/A", badge("UNKNOWN") + f" {esc(SIN_HISTORICO)}"]]),
               "<h3>Top esquemas por tamaño</h3>",
               _t(d["esquemas"], [("owner", "Owner", ""), ("gb", "GB", "n2"), ("pct_of_database_total", "% del total", "n2")]),
               "<h3>Top 100 segmentos por tamaño</h3>",
               _t(d["seg_tamano"], [("owner", "Owner", ""), ("segment_name", "Segmento", "mono"),
                                     ("partition_name", "Partición", ""), ("segment_type", "Tipo", ""),
                                     ("tablespace_name", "Tablespace", ""), ("gb", "GB", "n2"), ("extents", "Extents", "n0")]),
               "<h3>Top 100 segmentos por extents</h3>",
               _t(d["seg_extents"], [("owner", "Owner", ""), ("segment_name", "Segmento", "mono"),
                                      ("segment_type", "Tipo", ""), ("extents", "Extents", "n0"), ("gb", "GB", "n2")]),
               "<h3>Top índices por tamaño</h3>",
               _t(d["indices"], [("owner", "Owner", ""), ("index_name", "Índice", "mono"), ("table_name", "Tabla", "mono"),
                                  ("tablespace_name", "Tablespace", ""), ("gb", "GB", "n2"), ("blevel", "blevel", "n0"),
                                  ("leaf_blocks", "Leaf blocks", "n0")])]
        return "\n".join(out)
    p.append(_sec(s["storage"], "Storage", _storage))
    p.append("<h3>Cambios de redo por día (7 días)</h3>")
    p.append(_sec(s["redo"], "Redo", lambda d: _t(d, [("dia", "Día", ""), ("switches", "Switches", "n0")])))
    # 3. backups
    p.append(_h2("cat3", "3. Backups — Ventanas y Riesgo",
                 "rman_backup_jobs_window_7d, backup_sets_by_type_30d, rman_archivelog_heatmap — Hoja de Vida §7"))

    def _backup(d):
        ok = der.get("backup_valido_7d")
        riesgo = badge("OK") if ok else badge("CRITICAL" if (d["db"].get("log_mode") or "") == "NOARCHIVELOG" else "WARNING")
        return "\n".join([
            "<h3>Riesgo de recuperación (sobre backup_type='D' real)</h3>",
            tabla(["Indicador", "Valor"], [
                ["has_valid_backup_last_7d", f"{riesgo} {'sí' if ok else 'no'} — {num(d['d7'].get('sets_d'), 0)} sets D, "
                                             f"último {esc(d['d7'].get('ultimo_d'))}"],
                ["log_mode", esc(d["db"].get("log_mode"))], ["force_logging", esc(d["db"].get("force_logging"))],
                ["flashback_on", esc(d["db"].get("flashback_on"))]]),
            '<div class="note">Se evalúa la existencia real de backup de datafiles (v$backup_set, tipo D), '
            "no el estado del job wrapper de RMAN (§7.2).</div>",
            "<h3>Trabajos RMAN — ventana 7 días</h3>",
            _t(d["rman"], [("start_time", "Inicio", ""), ("input_type", "Tipo", ""), ("status", "Estado", ""),
                           ("duration_min", "Duración (min)", "n2"), ("output_gb", "Salida (GB)", "n2")],
               "Sin trabajos RMAN en 7 días."),
            "<h3>Backup sets por tipo — 30 días</h3>",
            _t(d["sets"], [("backup_type", "Tipo (D=datafile, I=incremental, L=archivelog)", ""),
                           ("incremental_level", "Nivel", ""), ("sets", "Sets", "n0"), ("ultimo", "Último", "")])])
    p.append(_sec(s["backup"], "Backups", _backup))
    p.append("<h3>Heatmap de archivelogs (14 días)</h3>")

    def _heat(d):
        if not d:
            return c.vacio("Sin archivelogs en la ventana.")
        mx = max(c.a_float(r.get("gb")) or 0 for r in d) or 1
        celdas = []
        for r in d:
            g = c.a_float(r.get("gb")) or 0
            color = "var(--rojo)" if g >= 0.85 * mx else "var(--ambar)" if g >= 0.5 * mx else "var(--verde700)"
            celdas.append(f'<span class="heat" style="background:{color}" title="{esc(r.get("archivos"))} archivos">'
                          f'{esc(str(r.get("dia"))[5:])}<br>{num(g, 1)} GB</span>')
        return "<div>" + "".join(celdas) + "</div>"
    p.append(_sec(s["heatmap"], "Heatmap archivelog", _heat))
    # 4. rendimiento
    p.append(_h2("cat4", "4. Rendimiento", "sga/pga_target_advice, top_sql_by_elapsed, wait_events, "
                                           "sql_tuning_advisor_recommendations — Hoja de Vida §8"))

    def _mem(d):
        g = der.get("sga_gain")
        sga_v = badge("UNKNOWN") if g is None else badge(
            "CRITICAL" if g > SGA_GAIN_CRIT else "WARNING" if g >= SGA_GAIN_WARN else "OK")
        pga_v = badge("CRITICAL" if (d["over"] or 0) > 0 else "OK")
        return "\n".join([
            tabla(["Veredicto", "Valor", "Estado"], [
                ["SGA next_tier_gain_pct", f"{num(g)} %" if g is not None else "N/A", sga_v],
                ["PGA over allocation count", num(d["over"], 0), pga_v]]),
            "<h3>v$sga_target_advice</h3>",
            _t(d["sga"], [("sga_size", "SGA (MB)", "n0"), ("sga_size_factor", "Factor", "n2"),
                          ("estd_db_time", "DB time estimado", "n0"), ("estd_physical_reads", "Lecturas físicas", "n0")]),
            "<h3>v$pga_target_advice</h3>",
            _t(d["pga"], [("pga_mb", "PGA (MB)", "n0"), ("pga_target_factor", "Factor", "n2"),
                          ("hit_pct", "Cache hit %", "n0"), ("estd_overalloc_count", "Overalloc estimado", "n0")])])
    p.append(_sec(s["memoria"], "SGA/PGA", _mem))
    p.append("<h3>Top 50 SQL por elapsed time</h3>")
    p.append(_sec(s["top_sql"], "Top SQL", lambda d: _t(d, [
        ("sql_id", "sql_id", "mono"), ("parsing_schema_name", "Esquema", ""), ("executions", "Ejec.", "n0"),
        ("elapsed_sec", "Elapsed (s)", "n2"), ("cpu_sec", "CPU (s)", "n2"), ("elapsed_por_exec", "s/ejec", "n2"),
        ("buffer_gets", "Buffer gets", "n0"), ("disk_reads", "Disk reads", "n0"), ("sql_text", "Texto", "mono")])
        + '<div class="note">Drill-down de origen por sql_id → reporte <em>Sentencias costosas</em>.</div>'))
    p.append("<h3>Top 20 wait events (sin Idle)</h3>")
    p.append(_sec(s["waits"], "Wait events", lambda d: _t(d, [
        ("event", "Evento", ""), ("wait_class", "Clase", ""), ("total_waits", "Esperas", "n0"),
        ("time_waited_sec", "Tiempo (s)", "n2"), ("avg_wait_ms", "Prom. (ms)", "n2")])))
    p.append("<h3>SQL Tuning Advisor (30 días)</h3>")
    p.append(_sec(s["sta"], "SQL Tuning Advisor", lambda d: _t(d, [
        ("task_name", "Tarea", "mono"), ("tipo_hallazgo", "Hallazgo", ""), ("mensaje", "Mensaje", ""),
        ("tipo_recomendacion", "Recomendación", ""), ("benefit", "Beneficio", "n0")],
        "Sin tareas de SQL Tuning Advisor en 30 días.")))
    # 5. seguridad
    p.append(_h2("cat5", "5. Seguridad", "dba_privilege_login_frequency, roles_cleanup_candidates, "
                                         "database_registry_baseline_check — Hoja de Vida §11"))

    def _seg(d):
        filas = []
        for u in d["dba"]:
            dias = c.a_float(u.get("dias_sin_login"))
            st = "UNKNOWN" if dias is None else "CRITICAL" if dias > DBA_DORMIDO_CRIT else \
                "WARNING" if dias > DBA_DORMIDO_WARN else "OK"
            filas.append([esc(u.get("username")), esc(u.get("account_status")), esc(u.get("last_login")),
                          num(dias, 0) if dias is not None else "sin registro", badge(st)])
        return "\n".join([
            "<h3>Cuentas con rol DBA — frecuencia de login</h3>",
            tabla(["Cuenta", "Estado", "Último login", "Días sin login", "Veredicto"], filas)
            if filas else c.vacio("Sin cuentas con rol DBA."),
            "<h3>Roles candidatos a limpieza (sin asignar)</h3>",
            _t(d["huerfanos"], [("role", "Rol", "mono")], "Sin roles huérfanos."),
            "<h3>Componentes registrados vs baseline</h3>",
            _t(d["registro"], [("comp_id", "Id", "mono"), ("comp_name", "Componente", ""), ("version", "Versión", ""),
                               ("status", "Estado", "")]),
            f'<div class="nc">database_registry_baseline_check: {badge("UNKNOWN")} — no hay baseline curado configurado.</div>'])
    p.append(_sec(s["seguridad"], "Seguridad", _seg))
    # 6. objetos
    p.append(_h2("cat6", "6. Objetos", "invalid_objects_trend, indexes_candidate_for_rebuild, "
                                       "tables_with_stale_statistics, alert_log_errors_summary — Hoja de Vida §12-13"))
    p.append(_sec(s["objetos"], "Objetos", lambda d: "\n".join([
        "<h3>Objetos inválidos por owner y tipo</h3>",
        _t(d["invalidos"], [("owner", "Owner", ""), ("object_type", "Tipo", ""), ("cnt", "Cantidad", "n0")],
           "Sin objetos inválidos."),
        f'<div class="note">Tendencia: {esc(SIN_HISTORICO)}.</div>',
        "<h3>Índices candidatos a rebuild (blevel &gt; 3)</h3>",
        _t(d["rebuild"], [("owner", "Owner", ""), ("index_name", "Índice", "mono"), ("table_name", "Tabla", "mono"),
                          ("blevel", "blevel", "n0"), ("leaf_blocks", "Leaf blocks", "n0"),
                          ("last_analyzed", "Último análisis", "")], "Sin índices con blevel > 3.")])))
    p.append("<h3>Tablas con estadísticas obsoletas</h3>")
    p.append(_sec(s["stale"], "Estadísticas", lambda d: _t(d["top"], [
        ("owner", "Owner", ""), ("table_name", "Tabla", "mono"), ("num_rows", "Filas", "n0"),
        ("last_analyzed", "Último análisis", "")], "Sin tablas con STALE_STATS = YES.")
        + f'<div class="note">Total de tablas con estadísticas obsoletas: <strong>{num(d["total"], 0)}</strong> (se listan 50).</div>'))
    p.append("<h3>Alert log — errores ORA agregados (7 días)</h3>")
    p.append(_sec(s["alertlog"], "Alert log", lambda d: _t(d, [
        ("codigo", "Código", "mono"), ("cnt", "Ocurrencias", "n0"), ("ultimo", "Último", "")],
        "Sin errores ORA en 7 días.")))
    # 7. sesiones
    p.append(_h2("cat7", "7. Sesiones", "sessions_statistical_summary — Hoja de Vida §10"))
    p.append(_sec(s["sesiones"], "Sesiones", lambda d: "\n".join([
        _t(d["limites"], [("resource_name", "Recurso", ""), ("current_utilization", "Actual", "n0"),
                          ("max_utilization", "Máximo histórico", "n0"), ("limit_value", "Límite", ""),
                          ("pct_usage", "% uso", "n2")]),
        "<h3>Sesiones por usuario</h3>",
        _t(d["usuarios"], [("username", "Usuario", ""), ("total", "Sesiones", "n0"), ("activas", "Activas", "n0")]),
        f'<div class="note">Resumen estadístico: {esc(SIN_HISTORICO)}.</div>'])))
    # 8. parches
    p.append(_h2("cat8", "8. Parches", "patch_support_status — Hoja de Vida §2"))

    def _parches(rows):
        ult = rows[0] if rows else {}
        return "\n".join([
            tabla(["Indicador", "Valor"], [
                ["Último parche", f"{esc(ult.get('description'))} ({esc(ult.get('action_time'))})" if ult else "N/A"],
                ["Antigüedad", f"{badge(s['parches'].status)} {esc(s['parches'].note)}"],
                ["patch_support_status", f"{badge('UNKNOWN')} sin tabla de referencia oracle_release_lifecycle"]])])
    p.append(_sec(s["parches"], "Parches", _parches))
    # 9. resumen, plan y alertas
    p.append(_h2("cat9", "9. Resumen Ejecutivo, Plan de Acción y Registro de Alertas",
                 "executive_summary, action_plan, alert_registry"))
    resumen = [f"<li><strong>overall_status combinado:</strong> {badge(overall)} — peor nivel entre los hallazgos "
               "de este documento" + (" y la Hoja de Vida" if meta.get("hv_overall") else "") + ".</li>"]
    for a in alertas[:5]:
        resumen.append(f"<li>{_prio(a['prioridad'])} <span class='mono'>{esc(a['id'])}</span> — {esc(a['observacion'])}</li>")
    if not alertas:
        resumen.append("<li>Sin hallazgos WARNING/CRITICAL en las secciones recolectadas.</li>")
    p.append("<h3>Resumen ejecutivo</h3><ul>" + "".join(resumen) + "</ul>")
    if raiz:
        p.append("<h3>Correlación de causa raíz (mismo owner)</h3>")
        p.append(tabla(["Owner", "Dominios con hallazgos"],
                       [[f'<span class="mono">{esc(r["owner"])}</span>', esc(", ".join(r["dominios"]))] for r in raiz]))
        p.append('<div class="note">Agrupado por owner; no se infiere causalidad (§7.3).</div>')
    p.append("<h3>Plan de acción priorizado</h3>")
    if alertas:
        p.append(tabla(["#", "Prioridad", "Alerta", "Recomendación", "Acción en Ops Central"], [
            [str(i), _prio(a["prioridad"]), f'<span class="mono">{esc(a["id"])}</span> {esc(a["metrica"])}',
             esc(a["recomendacion"]), f'<span class="mono">{esc(a["accion"] or "—")}</span>']
            for i, a in enumerate(alertas, 1)]))
    else:
        p.append(c.vacio("Sin acciones pendientes."))
    p.append("<h3>Registro de alertas (alert_registry)</h3>")
    if alertas:
        p.append(tabla(["id", "Dominio", "Métrica", "Valor", "Umbral", "Severidad", "finding_type", "Prioridad",
                        "Observación", "timestamp", "first_seen_snapshot_id"], [
            [f'<span class="mono">{esc(a["id"])}</span>', esc(a["dominio"]), esc(a["metrica"]), esc(a["valor"]),
             esc(a["umbral"]), badge(a["severidad"]), esc(a["finding_type"]), _prio(a["prioridad"]),
             esc(a["observacion"]), esc(a["timestamp"]), "N/A (primer snapshot)"] for a in alertas]))
    else:
        p.append(c.vacio("Registro vacío."))
    p.append('<div class="note">Priorización determinística: finding_type × severidad → prioridad (§7.1). '
             'Sin histórico, todo hallazgo es SNAPSHOT_UNICO y se prioriza como persistente. '
             'No se crean tickets Jira automáticos en el piloto.</div>')
    p.append(f"""
</main>
<footer>
  Reporte de Análisis de Base de Datos · {esc(meta['db_name'])} · generado {esc(meta['date'])} (COT, {c.COT_TZ}) por script de solo lectura.<br>
  Secciones NOT_COLLECTED ({len(nc_list)}): {esc('; '.join(nc_list) or 'ninguna')}<br>
  UNKNOWN por falta de histórico/configuración: resumen estadístico, tendencias, soporte de parches, baseline de registro.
</footer>
</body>
</html>
""")
    return "\n".join(p)


ETAPAS = ["Jobs fallidos", "Segmentos por tamaño", "Top SQL y esperas", "Veredictos memoria/backup/parches",
          "Priorización", "Generar HTML"]


def construir(client, alias: str, inv_entry: dict, source_hv_report_name: str = "",
              hv_overall: str = "", progreso=None) -> tuple[str, str, dict]:
    """Recolecta, evalúa y arma el HTML. Devuelve (nombre_archivo, html, resumen)."""
    avance = progreso or (lambda _etapa: None)
    environment = (inv_entry.get("environment") or "").lower()
    c.log(f"Análisis {alias}: recolectando …")

    def sc(fn):
        return c.safe_collect(client, fn)
    avance(ETAPAS[0])
    inst = sc(hv.collect_instance)
    s = {"jobs": sc(col_jobs)}
    avance(ETAPAS[1])
    s.update(storage=sc(col_storage), redo=sc(col_redo))
    avance(ETAPAS[2])
    s.update(top_sql=sc(col_top_sql), waits=sc(col_waits), sta=sc(col_sta), memoria=sc(col_memoria),
             sesiones=sc(col_sesiones))
    avance(ETAPAS[3])
    s.update(backup=sc(col_backup), heatmap=sc(col_heatmap), parches=sc(hv.collect_patches),
             seguridad=sc(col_seguridad), objetos=sc(col_objetos), stale=sc(col_stale),
             alertlog=sc(col_alertlog))
    avance(ETAPAS[4])
    db = inst.data["database"] if inst.status == "OK" else {}
    db_name = db.get("name") or alias.upper()
    now = c.ahora()
    reg = Registro(now.strftime("%Y-%m-%dT%H:%M:%S") + c.COT_TZ)
    der = evaluar(s, reg)
    overall = c.worst("OK", *[a["severidad"] for a in reg.alertas], *([hv_overall] if hv_overall else []))
    raiz = correlaciones(reg.alertas, der)
    nc_list = [f"{k} ({v.error})" for k, v in {"Instancia": inst, **s}.items() if v.status == "NOT_COLLECTED"]
    meta = {"alias": alias, "dsn": inv_entry.get("dsn", ""), "environment": environment, "db_name": db_name,
            "date": now.strftime("%Y-%m-%d"), "source_hv_report_name": source_hv_report_name,
            "hv_overall": hv_overall, "aviso": inv_entry.get("aviso", ""),
            "herramienta": inv_entry.get("herramienta") or "generar_reporte_analisis.py"}
    avance(ETAPAS[5])
    html_txt = render(meta, s, reg, der, overall, raiz, nc_list)
    fname = f"reporte-analisis-database-{db_name.lower()}-{now.strftime('%Y%m%d')}{c.sufijo_ambiente(environment)}.html"
    alertas = sorted(reg.alertas, key=lambda a: (ORDEN_PRIORIDAD[a["prioridad"]], a["id"]))
    resumen = {"overall_status": overall, "db_name": db_name, "not_collected": nc_list,
               "alertas": len(alertas), "urgentes": sum(1 for a in alertas if a["prioridad"] == "URGENTE"),
               "plan": [{k: a[k] for k in ("id", "prioridad", "severidad", "observacion", "accion")}
                        for a in alertas[:10]],
               "correlaciones": raiz}
    c.log(f"Análisis {alias}: overall={overall} · alertas={len(alertas)} · NOT_COLLECTED={len(nc_list)}")
    return fname, html_txt, resumen


def main() -> int:
    return c.cli_por_base("Genera el Reporte de Análisis de Base de Datos (HTML) por script.", construir)


if __name__ == "__main__":
    sys.exit(main())
