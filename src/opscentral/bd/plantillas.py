"""Consultas de solo lectura por plantilla (cuenta OPS_CONSULTA).

Nadie escribe SQL en la interfaz ni en el chat: solo se usan estas plantillas,
con variables de enlace. Al cargar el módulo, cada plantilla pasa por la
política de solo lectura (SqlPolicyEngine del kit Oracle si está disponible).
"""
from __future__ import annotations

from .politica import validar_lectura

_SIN_ORACLE = "(SELECT u.username FROM dba_users u WHERE u.oracle_maintained = 'Y')"

_TOPSQL = """
        SELECT sql_id, plan_hash_value, parsing_schema_name AS esquema, executions,
               ROUND(elapsed_time / 1000000) AS elapsed_s, ROUND(cpu_time / 1000000) AS cpu_s,
               buffer_gets, disk_reads,
               ROUND(elapsed_time / NULLIF(executions, 0) / 1000, 1) AS ms_por_ejec,
               SUBSTR(sql_text, 1, 300) AS texto
          FROM gv$sql
         WHERE parsing_schema_name NOT IN {sin_oracle}
         ORDER BY {orden} DESC
         FETCH FIRST 25 ROWS ONLY"""

PLANTILLAS: dict[str, str] = {
    "salud_instancia": """
        SELECT i.instance_name, i.host_name, i.version, i.status, i.startup_time,
               d.database_role, d.open_mode,
               (SELECT COUNT(*) FROM v$session WHERE type = 'USER') AS sesiones,
               (SELECT TO_NUMBER(value) FROM v$parameter WHERE name = 'processes') AS procesos
          FROM v$instance i CROSS JOIN v$database d""",
    "tablespaces": """
        SELECT m.tablespace_name,
               ROUND(m.used_space * t.block_size / 1048576) AS usado_mb,
               ROUND(m.tablespace_size * t.block_size / 1048576) AS maximo_mb,
               ROUND(m.used_percent, 1) AS pct_usado
          FROM dba_tablespace_usage_metrics m
          JOIN dba_tablespaces t ON t.tablespace_name = m.tablespace_name
         ORDER BY m.used_percent DESC""",
    "diskgroups": """
        SELECT name, state, type, total_mb, free_mb, usable_file_mb,
               ROUND(100 * (1 - free_mb / NULLIF(total_mb, 0)), 1) AS pct_usado
          FROM v$asm_diskgroup
         ORDER BY name""",
    "invalidos": """
        SELECT o.owner, o.object_name, o.object_type, o.last_ddl_time
          FROM dba_objects o
         WHERE o.status = 'INVALID'
           AND o.owner NOT IN (SELECT u.username FROM dba_users u WHERE u.oracle_maintained = 'Y')
         ORDER BY o.owner, o.object_type, o.object_name""",
    # Solo columnas que existen en la tabla del equipo DBA (copia de DBA_OBJECTS + FECHA_REGISTRO,
    # cargada con INSERT ... SELECT FROM dba_objects WHERE status='INVALID'). No tiene ACTIVO ni
    # JUSTIFICACION: estar en la tabla = estar en la línea base; retirar = borrar la fila.
    "linea_base": """
        SELECT owner, object_name, object_type, fecha_registro
          FROM adminbd.tbl_base_objetos_invalidos""",
    "bloqueos": """
        SELECT s.inst_id, s.sid, s.serial# AS serial, s.username, s.status, s.machine, s.program, s.module,
               s.sql_id, s.event, s.seconds_in_wait, s.last_call_et,
               s.blocking_instance, s.blocking_session, s.final_blocking_instance, s.final_blocking_session,
               o.owner || '.' || o.object_name AS objeto
          FROM gv$session s
          LEFT JOIN dba_objects o ON o.object_id = s.row_wait_obj#
         WHERE s.blocking_session IS NOT NULL
            OR (s.inst_id, s.sid) IN (SELECT b.blocking_instance, b.blocking_session
                                        FROM gv$session b WHERE b.blocking_session IS NOT NULL)""",
    "sesiones_activas": """
        SELECT inst_id, sid, serial# AS serial, username, machine, module, sql_id, event, last_call_et
          FROM gv$session
         WHERE status = 'ACTIVE' AND type = 'USER'
         ORDER BY last_call_et DESC
         FETCH FIRST 200 ROWS ONLY""",
    "sql_texto": """
        SELECT sql_id, plan_hash_value, executions, elapsed_time, sql_fulltext
          FROM gv$sql
         WHERE sql_id = :sql_id
         FETCH FIRST 1 ROWS ONLY""",
    "usuario_estado": """
        SELECT username, account_status, lock_date, expiry_date, profile, last_login
          FROM dba_users
         WHERE username = :usuario""",
    "usuarios_por_expirar": """
        SELECT username, account_status, expiry_date, profile,
               ROUND(expiry_date - SYSDATE, 1) AS dias_restantes
          FROM dba_users
         WHERE oracle_maintained = 'N'
           AND expiry_date IS NOT NULL
           AND expiry_date < SYSDATE + 15
           AND account_status IN ('OPEN', 'EXPIRED(GRACE)')
         ORDER BY expiry_date""",

    # ------------------------------------------------------------ sesiones
    "idle_tx": """
        SELECT s.inst_id, s.sid, s.serial# AS serial, s.username, s.status, s.machine, s.program, s.module,
               s.last_call_et AS segundos_inactiva, t.start_time AS inicio_tx, t.used_ublk AS bloques_undo,
               t.used_urec AS registros_undo
          FROM gv$session s
          JOIN gv$transaction t ON t.inst_id = s.inst_id AND t.addr = s.taddr
         WHERE s.status = 'INACTIVE' AND s.type = 'USER'
           AND s.last_call_et >= :min_segundos
         ORDER BY s.last_call_et DESC
         FETCH FIRST 200 ROWS ONLY""",
    "sesiones_por_maquina": """
        SELECT s.inst_id, s.username, s.machine, s.status, COUNT(*) AS sesiones,
               SUM(CASE WHEN s.blocking_session IS NOT NULL THEN 1 ELSE 0 END) AS bloqueadas,
               MAX(s.last_call_et) AS max_last_call_et
          FROM gv$session s
         WHERE s.type = 'USER' AND UPPER(s.machine) LIKE UPPER(:maquina) || '%'
         GROUP BY s.inst_id, s.username, s.machine, s.status
         ORDER BY sesiones DESC""",
    # ------------------------------------------------------------ salud
    "fra_uso": """
        SELECT name, ROUND(space_limit / 1048576) AS limite_mb, ROUND(space_used / 1048576) AS usado_mb,
               ROUND(space_reclaimable / 1048576) AS recuperable_mb, number_of_files AS archivos,
               ROUND(100 * (space_used - space_reclaimable) / NULLIF(space_limit, 0), 1) AS pct_usado
          FROM v$recovery_file_dest""",
    "archivelog_hora": """
        SELECT TRUNC(first_time, 'HH24') AS hora, COUNT(*) AS archivos,
               ROUND(SUM(blocks * block_size) / 1048576) AS mb
          FROM v$archived_log
         WHERE first_time > SYSDATE - 1 AND standby_dest = 'NO'
         GROUP BY TRUNC(first_time, 'HH24')
         ORDER BY hora""",
    "dg_rol": """
        SELECT database_role, protection_mode, protection_level, switchover_status, open_mode,
               force_logging, flashback_on
          FROM v$database""",
    "dg_stats": """
        SELECT name, value, unit, time_computed
          FROM v$dataguard_stats
         WHERE name IN ('transport lag', 'apply lag', 'apply finish time')""",
    "dg_gaps": """
        SELECT thread# AS hilo, low_sequence# AS secuencia_desde, high_sequence# AS secuencia_hasta
          FROM v$archive_gap""",
    "rman_7d": """
        SELECT session_key, input_type, status, start_time, end_time,
               ROUND(elapsed_seconds / 60) AS minutos, output_bytes_display AS tamano
          FROM v$rman_backup_job_details
         WHERE start_time > SYSDATE - 7
         ORDER BY start_time DESC""",
    "jobs_problema": """
        SELECT j.owner, j.job_name, j.enabled, j.state, j.failure_count, j.last_start_date, j.next_run_date,
               j.job_class
          FROM dba_scheduler_jobs j
         WHERE (j.enabled = 'FALSE' OR j.state IN ('BROKEN', 'FAILED') OR j.failure_count > 0)
           AND j.owner NOT IN """ + _SIN_ORACLE + """
         ORDER BY j.owner, j.job_name""",
    "jobs_fallidos_24h": """
        SELECT d.owner, d.job_name, d.status, d.actual_start_date, d.run_duration, d.error# AS error,
               SUBSTR(d.additional_info, 1, 400) AS detalle
          FROM dba_scheduler_job_run_details d
         WHERE d.log_date > SYSTIMESTAMP - INTERVAL '1' DAY AND d.status <> 'SUCCEEDED'
           AND d.owner NOT IN """ + _SIN_ORACLE + """
         ORDER BY d.log_date DESC
         FETCH FIRST 200 ROWS ONLY""",
    "undo_24h": """
        SELECT begin_time, end_time, undoblks, maxquerylen, maxconcurrency, ssolderrcnt AS ora_01555,
               nospaceerrcnt AS sin_espacio
          FROM v$undostat
         WHERE begin_time > SYSDATE - 1
         ORDER BY begin_time DESC""",
    "temp_sesiones": """
        SELECT s.inst_id, s.sid, s.serial# AS serial, s.username, s.sql_id, s.module, u.tablespace,
               ROUND(u.blocks * t.block_size / 1048576) AS mb
          FROM gv$tempseg_usage u
          JOIN gv$session s ON s.inst_id = u.inst_id AND s.saddr = u.session_addr
          JOIN dba_tablespaces t ON t.tablespace_name = u.tablespace
         ORDER BY mb DESC
         FETCH FIRST 50 ROWS ONLY""",
    "alertlog_24h": """
        SELECT originating_timestamp AS momento, message_level AS nivel, SUBSTR(message_text, 1, 500) AS mensaje
          FROM v$diag_alert_ext
         WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '1' DAY
           AND message_text LIKE '%ORA-%'
         ORDER BY originating_timestamp DESC
         FETCH FIRST 500 ROWS ONLY""",
    "esperas_sistema": """
        SELECT event, wait_class, total_waits, ROUND(time_waited_micro / 1000000) AS segundos,
               ROUND(time_waited_micro / NULLIF(total_waits, 0) / 1000, 2) AS ms_promedio
          FROM v$system_event
         WHERE wait_class <> 'Idle'
         ORDER BY time_waited_micro DESC
         FETCH FIRST 20 ROWS ONLY""",
    "esperas_ahora": """
        SELECT wait_class, event, COUNT(*) AS sesiones
          FROM gv$session
         WHERE status = 'ACTIVE' AND type = 'USER' AND wait_class <> 'Idle'
         GROUP BY wait_class, event
         ORDER BY sesiones DESC""",
    "parametros": """
        SELECT name, value, isdefault, ismodified, issys_modifiable
          FROM v$parameter
         WHERE isdefault = 'FALSE'
         ORDER BY name""",
    "parches": """
        SELECT patch_id, action, status, description, action_time, source_version, target_version
          FROM dba_registry_sqlpatch
         ORDER BY action_time DESC""",
    # ------------------------------------------------------------ usuarios
    "usr_roles": """
        SELECT grantee, granted_role, admin_option, default_role
          FROM dba_role_privs
         WHERE grantee = :usuario
         ORDER BY granted_role""",
    "usr_privs_sistema": """
        SELECT grantee, privilege, admin_option
          FROM dba_sys_privs
         WHERE grantee = :usuario
         ORDER BY privilege""",
    "usr_privs_objeto": """
        SELECT owner, table_name AS objeto, privilege, grantable
          FROM dba_tab_privs
         WHERE grantee = :usuario
         ORDER BY owner, table_name
         FETCH FIRST 500 ROWS ONLY""",
    # ------------------------------------------------------------ espacio
    "shrink_candidatos": """
        SELECT segment_owner, segment_name, segment_type, partition_name, tablespace_name,
               ROUND(allocated_space / 1048576) AS asignado_mb, ROUND(used_space / 1048576) AS usado_mb,
               ROUND(reclaimable_space / 1048576) AS recuperable_mb, recommendations
          FROM TABLE(DBMS_SPACE.ASA_RECOMMENDATIONS())
         ORDER BY reclaimable_space DESC
         FETCH FIRST 50 ROWS ONLY""",
    "papelera": """
        SELECT owner, object_name, original_name, type, ts_name, droptime, space AS bloques
          FROM dba_recyclebin
         WHERE (:esquema IS NULL OR owner = :esquema)
         ORDER BY owner, droptime
         FETCH FIRST 1000 ROWS ONLY""",
    "objetos_esquema": """
        SELECT object_name, object_type, status, last_ddl_time
          FROM dba_objects
         WHERE owner = :esquema AND object_name NOT LIKE 'BIN$%' AND generated = 'N'
         ORDER BY object_type, object_name""",
    # ------------------------------------------------------------ rendimiento
    "topsql_cpu": _TOPSQL.format(orden="cpu_time", sin_oracle=_SIN_ORACLE),
    "topsql_elapsed": _TOPSQL.format(orden="elapsed_time", sin_oracle=_SIN_ORACLE),
    "topsql_gets": _TOPSQL.format(orden="buffer_gets", sin_oracle=_SIN_ORACLE),
    "plan_historia": """
        SELECT s.plan_hash_value, MIN(sn.begin_interval_time) AS desde, MAX(sn.end_interval_time) AS hasta,
               SUM(s.executions_delta) AS ejecuciones,
               ROUND(SUM(s.elapsed_time_delta) / NULLIF(SUM(s.executions_delta), 0) / 1000, 1) AS ms_por_ejec,
               ROUND(SUM(s.buffer_gets_delta) / NULLIF(SUM(s.executions_delta), 0)) AS gets_por_ejec
          FROM dba_hist_sqlstat s
          JOIN dba_hist_snapshot sn ON sn.snap_id = s.snap_id AND sn.dbid = s.dbid
                                   AND sn.instance_number = s.instance_number
         WHERE s.sql_id = :sql_id
         GROUP BY s.plan_hash_value
         ORDER BY desde""",
    "plan_actual": """
        SELECT inst_id, plan_hash_value, child_number, executions,
               ROUND(elapsed_time / NULLIF(executions, 0) / 1000, 1) AS ms_por_ejec, last_active_time
          FROM gv$sql
         WHERE sql_id = :sql_id
         ORDER BY last_active_time DESC""",
    "longops": """
        SELECT inst_id, sid, serial# AS serial, username, opname, target, sofar, totalwork,
               ROUND(100 * sofar / NULLIF(totalwork, 0), 1) AS pct, elapsed_seconds, time_remaining, sql_id
          FROM gv$session_longops
         WHERE time_remaining > 0
         ORDER BY time_remaining DESC""",
    "stats_obsoletas": """
        SELECT owner, table_name, partition_name, object_type, last_analyzed, num_rows, stale_stats
          FROM dba_tab_statistics
         WHERE stale_stats = 'YES'
           AND owner NOT IN """ + _SIN_ORACLE + """
         ORDER BY owner, table_name
         FETCH FIRST 500 ROWS ONLY""",
}

# Variables de enlace que acepta cada plantilla (parámetro del catálogo -> bind)
BINDS: dict[str, tuple[str, ...]] = {
    "sql_texto": ("sql_id",), "usuario_estado": ("usuario",), "idle_tx": ("min_segundos",),
    "sesiones_por_maquina": ("maquina",), "usr_roles": ("usuario",), "usr_privs_sistema": ("usuario",),
    "usr_privs_objeto": ("usuario",), "papelera": ("esquema",), "objetos_esquema": ("esquema",),
    "plan_historia": ("sql_id",), "plan_actual": ("sql_id",),
}

for _n, _sql in PLANTILLAS.items():
    _ok, _msg = validar_lectura(_sql)
    if not _ok:
        raise RuntimeError(f"la plantilla {_n} no pasa la política de solo lectura: {_msg}")
