"""Consultas de solo lectura por plantilla (cuenta OPS_CONSULTA).

Nadie escribe SQL en la interfaz ni en el chat: solo se usan estas plantillas,
con variables de enlace. Al cargar el módulo, cada plantilla pasa por la
política de solo lectura (SqlPolicyEngine del kit Oracle si está disponible).
"""
from __future__ import annotations

from .politica import validar_lectura

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
    "linea_base": """
        SELECT owner, object_name, object_type, justificacion, registrado_por, fecha_registro
          FROM adminbd.tbl_base_objetos_invalidos
         WHERE activo = 'S'""",
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
}

# Variables de enlace que acepta cada plantilla (parámetro del catálogo -> bind)
BINDS: dict[str, tuple[str, ...]] = {"sql_texto": ("sql_id",), "usuario_estado": ("usuario",)}

for _n, _sql in PLANTILLAS.items():
    _ok, _msg = validar_lectura(_sql)
    if not _ok:
        raise RuntimeError(f"la plantilla {_n} no pasa la política de solo lectura: {_msg}")
