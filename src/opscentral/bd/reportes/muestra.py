# -*- coding: utf-8 -*-
"""Cliente de datos de MUESTRA para los generadores de reportes.

Responde a las mismas consultas que una base Oracle real con filas inventadas y
deterministas (misma semilla por alias), para revisar el formato de los reportes
sin conexión: opción --muestra de los scripts, pruebas automáticas y la demo de
Ops Central sin Oracle (bd.reportes_muestra = true; prohibido en producción).
Nunca se mezcla con datos reales: los reportes generados así llevan un aviso.
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta

try:
    from . import analisis, diskgroups, sentencias
except ImportError:  # paquete portable
    import generar_reporte_analisis as analisis  # type: ignore
    import generar_reporte_diskgroups as diskgroups  # type: ignore
    import generar_sentencias_costosas as sentencias  # type: ignore

AVISO_HTML = ('<div class="aviso nc" style="margin:14px 0"><strong>DATOS DE MUESTRA</strong> — este reporte se generó '
              'sin conexión a Oracle para revisar el formato. Las cifras no corresponden a ninguna base real.</div>')

_SQLS = [
    ("7h35uxf5uhmm1", "APP_WEB", "ClientesWeb", "JDBC Thin Client",
     "SELECT * FROM app_core.clientes c WHERE UPPER(c.nombre) LIKE '%' || :b1 || '%' ORDER BY c.fecha_alta"),
    ("0w2qpuc6u2zsp", "APP_BATCH", "CIERRE_DIARIO", "oracle@dbprd01 (J001)",
     "UPDATE app_core.movimientos SET estado = 'CERRADO' WHERE fecha < TRUNC(SYSDATE) AND estado = 'ABIERTO'"),
    ("g4y6nw3tts7cc", "CREDITO", "PKG_SCORING", "JDBC Thin Client",
     "SELECT s.id, s.valor FROM credito.solicitudes s WHERE s.id NOT IN (SELECT r.solicitud_id FROM credito.rechazos r)"),
    ("5k5v1ah25fb2c", "LEGADO", "RUNCOBOL", "runcobol@ap23sbcc09",
     "SELECT cta, saldo FROM legado.cuentas WHERE sucursal = 101 AND tipo = 'A'"),
    ("9babjv8yq8ru3", "INTEGRA", "", "oracle@dbcrm01 (TNS V1-V3)",
     "SELECT /*+ FULL(h) */ h.* FROM integra.historico h WHERE h.periodo >= :b1"),
    ("1p5grz1gy7xuc", "APP_WEB", "ClientesWeb", "JDBC Thin Client",
     "SELECT c.id, c.nombre FROM app_core.clientes c WHERE c.id = :b1"),
    ("3xw8zr6cj4g2n", "REPORTES", "SQL*Plus", "sqlplus@ap23sbcc10",
     "SELECT TO_CHAR(fecha,'YYYY-MM') mes, SUM(valor) FROM reportes.ventas GROUP BY TO_CHAR(fecha,'YYYY-MM')"),
    ("b6usrg82hwsa3", "DBSNMP", "emagent_SQL_oracle_database", "emagent@oem01",
     "SELECT metric_name, value FROM v$sysmetric WHERE group_id = 2"),
    ("f0wzs9nc663bn", "APP_BATCH", "carga_python", "python.exe",
     "INSERT INTO app_core.staging_pagos (id, valor) VALUES (:1, :2)"),
    ("4ztz048yfq32s", "CREDITO", "PKG_SCORING", "JDBC Thin Client",
     "SELECT COUNT(*) FROM credito.solicitudes WHERE estado = :b1"),
    ("c2fn9ndw6m2gr", "APP_WEB", "PagosWeb", "JarLauncher",
     "SELECT p.* FROM pagos.transacciones p WHERE p.fecha > SYSDATE - 1"),
    ("8szmwam7fysa3", "SYSTEM", "DBMS_SCHEDULER", "oracle@dbprd01 (J002)",
     "DELETE FROM app_core.log_auditoria WHERE fecha < SYSDATE - 90"),
]


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower()


class ClienteMuestra:
    """Misma interfaz que OracleClient: query(), query_one(), close()."""

    def __init__(self, alias: str):
        self.alias = alias
        # los nodos de un mismo RAC (prod_core1, prod_core2) comparten semilla y almacenamiento
        self.semilla = re.sub(r"\d+$", "", alias.lower())
        self.r = random.Random(self.semilla)
        self.hoy = date.today()
        self._exactas = {_norm(v): k for k, v in analisis.SQL.items()}
        self._exactas.update({_norm(v): "s_" + k for k, v in sentencias.SQL.items()})
        self._exactas[_norm(diskgroups.SQL_DG)] = "dg"

    def close(self) -> None:
        pass

    def query_one(self, sql: str, params: dict | None = None):
        r = self.query(sql, params, max_rows=1)
        return r[0] if r else None

    def query(self, sql: str, params: dict | None = None, max_rows: int = 500) -> list[dict]:
        n = _norm(sql)
        nombre = self._exactas.get(n)
        if nombre:
            fn = getattr(self, "_" + nombre, None)
            filas = fn(params or {}) if fn else []
        else:
            filas = self._hoja_vida(n)
        return filas[:max_rows]

    # ── utilidades ──────────────────────────────────────────────────────────
    def _rnd(self, clave: str) -> random.Random:
        return random.Random(f"{self.semilla}:{clave}")

    def _d(self, dias: float) -> str:
        return (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M")

    def _db_name(self) -> str:
        return {"prod_core": "CORPRD", "prod_crm": "CRMPRD", "prod_pagos": "PAGPRD", "prod_hist": "HISPRD",
                "test_core": "CORTST"}.get(self.semilla, re.sub(r"[^A-Z0-9]", "", self.alias.upper())[:8] or "DEMO")

    # ── diskgroups ──────────────────────────────────────────────────────────
    def _dg(self, p):
        if self.semilla == "prod_hist":
            return []  # servidor sin ASM (filesystem)
        r = self._rnd("dg")
        base = {"DATA": (r.uniform(70, 98), 26_620_000), "FRA": (r.uniform(40, 80), 4_096_000),
                "REDO": (r.uniform(20, 55), 512_000), "ARCH": (r.uniform(50, 92), 2_048_000)}
        filas = []
        for nombre, (pct, total) in base.items():
            free = round(total * (1 - pct / 100))
            filas.append({"name": nombre, "total_mb": total, "free_mb": free,
                          "pct_used": round((1 - free / total) * 100, 2)})
        return sorted(filas, key=lambda f: -f["pct_used"])

    # ── análisis ────────────────────────────────────────────────────────────
    def _jobs_total(self, p):
        return [{"total_fallidos": 4}]

    def _jobs_fallidos(self, p):
        return [{"owner": o, "job_name": j, "state": s, "enabled": "TRUE", "run_count": rc, "failure_count": fc,
                 "failure_pct": round(100 * fc / rc, 1), "last_start": self._d(d)}
                for o, j, s, rc, fc, d in [("APP_BATCH", "JOB_CIERRE_DIARIO", "SCHEDULED", 30, 6, 0.4),
                                           ("LEGADO", "JOB_REPORTES_OLD", "BROKEN", 12, 12, 3),
                                           ("APP_CORE", "JOB_PURGA_LOG", "SCHEDULED", 90, 2, 1),
                                           ("LEGADO", "JOB_CONCILIA", "SCHEDULED", 30, 1, 2)]]

    def _segs(self, orden: str):
        r = self._rnd("seg" + orden)
        owners = ["APP_CORE", "CREDITO", "LEGADO", "INTEGRA", "PAGOS", "REPORTES"]
        filas = []
        for i in range(40):
            gb = round(r.uniform(2, 380) / (1 + i * 0.15), 2)
            filas.append({"owner": owners[i % 6], "segment_name": f"{['MOVIMIENTOS', 'CLIENTES', 'HISTORICO', 'LOG_AUDITORIA', 'TRANSACCIONES', 'VENTAS'][i % 6]}{'_IX' + str(i) if i > 5 else ''}",
                          "partition_name": f"P{2026}{(i % 12) + 1:02d}" if i % 3 == 0 else None,
                          "segment_type": "TABLE PARTITION" if i % 3 == 0 else "INDEX" if i > 5 else "TABLE",
                          "tablespace_name": "APP_DATA" if i % 2 else "APP_IDX", "gb": gb,
                          "extents": int(gb * r.uniform(8, 60))})
        return sorted(filas, key=lambda f: -(f["gb"] if orden == "t" else f["extents"]))

    def _seg_tamano(self, p):
        return self._segs("t")

    def _seg_extents(self, p):
        return self._segs("e")

    def _esquemas(self, p):
        datos = [("APP_CORE", 1840.2), ("CREDITO", 960.7), ("LEGADO", 610.3), ("INTEGRA", 402.9),
                 ("PAGOS", 288.1), ("REPORTES", 140.5), ("SYS", 38.4), ("SYSTEM", 4.2)]
        tot = sum(g for _, g in datos)
        return [{"owner": o, "gb": g, "pct_of_database_total": round(100 * g / tot, 2)} for o, g in datos]

    def _indices(self, p):
        return [{"owner": o, "index_name": i, "table_name": t, "tablespace_name": "APP_IDX", "gb": g, "blevel": b,
                 "leaf_blocks": lb} for o, i, t, g, b, lb in [
            ("APP_CORE", "IX_MOV_FECHA", "MOVIMIENTOS", 182.4, 4, 2_310_400),
            ("APP_CORE", "PK_MOVIMIENTOS", "MOVIMIENTOS", 120.9, 3, 1_502_100),
            ("CREDITO", "IX_SOL_ESTADO", "SOLICITUDES", 64.0, 3, 820_300),
            ("INTEGRA", "IX_HIST_PERIODO", "HISTORICO", 51.7, 4, 690_020)]]

    def _redo_dia(self, p):
        r = self._rnd("redo")
        return [{"dia": (self.hoy - timedelta(days=6 - i)).isoformat(), "switches": r.randint(60, 180)} for i in range(7)]

    def _rman_7d(self, p):
        filas = []
        for d in range(7):
            filas.append({"input_type": "ARCHIVELOG", "status": "COMPLETED", "start_time": self._d(d + 0.1),
                          "duration_min": 6.5, "output_gb": 38.2})
            if d in (1, 4):
                filas.append({"input_type": "DB INCR", "status": "COMPLETED" if d == 4 else "FAILED",
                              "start_time": self._d(d + 0.5), "duration_min": 182.0, "output_gb": 610.4})
        return filas

    def _sets_30d(self, p):
        return [{"backup_type": "D", "incremental_level": 0, "sets": 4, "ultimo": self._d(4)},
                {"backup_type": "I", "incremental_level": 1, "sets": 18, "ultimo": self._d(1)},
                {"backup_type": "L", "incremental_level": None, "sets": 412, "ultimo": self._d(0.1)}]

    def _datafile_7d(self, p):
        # el ambiente de test simula el caso §7.2: el job "COMPLETED" solo respaldó archivelogs
        return [{"sets_d": 0 if self.semilla.startswith("test") else 1,
                 "ultimo_d": None if self.semilla.startswith("test") else self._d(4)}]

    def _log_mode(self, p):
        return [{"log_mode": "ARCHIVELOG", "force_logging": "YES", "flashback_on": "NO"}]

    def _heatmap(self, p):
        r = self._rnd("heat")
        return [{"dia": (self.hoy - timedelta(days=13 - i)).isoformat(), "archivos": r.randint(80, 260),
                 "gb": round(r.uniform(40, 160) * (2.2 if i == 10 else 1), 2)} for i in range(14)]

    def _sga_advice(self, p):
        return [{"sga_size": int(32768 * f), "sga_size_factor": f, "estd_db_time": int(1_000_000 * t),
                 "estd_physical_reads": int(9e8 * t)}
                for f, t in [(0.5, 1.41), (0.75, 1.12), (1.0, 1.0), (1.25, 0.91), (1.5, 0.88), (2.0, 0.87)]]

    def _pga_over(self, p):
        return [{"over_allocation_count": 0}]

    def _pga_advice(self, p):
        return [{"pga_mb": int(8192 * f), "pga_target_factor": f, "hit_pct": h, "estd_overalloc_count": 0}
                for f, h in [(0.5, 81), (1.0, 94), (1.5, 97), (2.0, 98)]]

    def _top_sql(self, p):
        return [{"sql_id": f["sql_id"], "parsing_schema_name": f["parsing_schema_name"], "executions": f["executions"],
                 "elapsed_sec": f["elapsed_sec"], "cpu_sec": f["cpu_sec"], "elapsed_por_exec": f["elapsed_por_exec"],
                 "buffer_gets": f["buffer_gets"], "disk_reads": f["disk_reads"], "sql_text": f["sql_text"][:100]}
                for f in self._s_top_ELAPSED({"top_n": 50})]

    def _waits(self, p):
        return [{"event": e, "wait_class": w, "total_waits": tw, "time_waited_sec": t, "avg_wait_ms": a}
                for e, w, tw, t, a in [("db file sequential read", "User I/O", 98_221_004, 182_301.2, 1.86),
                                       ("log file sync", "Commit", 12_400_331, 40_220.7, 3.24),
                                       ("enq: TX - row lock contention", "Application", 18_220, 22_014.0, 1208.2),
                                       ("db file scattered read", "User I/O", 4_100_221, 19_880.4, 4.85),
                                       ("SQL*Net more data from dblink", "Network", 902_114, 8_401.9, 9.31),
                                       ("direct path read", "User I/O", 2_210_009, 6_120.3, 2.77)]]

    def _sta(self, p):
        return [{"task_name": "TAREA_STA_7h35uxf5uhmm1", "tipo_hallazgo": "INDEX", "benefit": 9420,
                 "mensaje": "El rendimiento de la sentencia mejora creando uno o más índices.",
                 "tipo_recomendacion": "INDEX"},
                {"task_name": "TAREA_STA_0w2qpuc6u2zsp", "tipo_hallazgo": "STATISTICS", "benefit": 6100,
                 "mensaje": "Las estadísticas de la tabla MOVIMIENTOS están obsoletas.",
                 "tipo_recomendacion": "STATISTICS"}]

    def _dba_login(self, p):
        return [{"username": u, "account_status": s, "last_login": None if d is None else (self.hoy - timedelta(days=d)).isoformat(),
                 "dias_sin_login": d} for u, s, d in [("DBA_ANTIGUO", "OPEN", 412), ("DBA_SOPORTE", "OPEN", 121),
                                                     ("SYSTEM", "OPEN", 2), ("SYS", "OPEN", None)]]

    def _roles_huerfanos(self, p):
        return [{"role": "ROL_REPORTES_2019"}, {"role": "ROL_MIGRACION_TMP"}]

    def _registro(self, p):
        return [{"comp_id": c, "comp_name": n, "version": "19.0.0.0.0", "status": s} for c, n, s in [
            ("CATALOG", "Oracle Database Catalog Views", "VALID"), ("CATPROC", "Oracle Database Packages and Types", "VALID"),
            ("JAVAVM", "JServer JAVA Virtual Machine", "VALID"), ("XDB", "Oracle XML Database", "VALID"),
            ("OLS", "Oracle Label Security", "OPTION OFF")]]

    def _invalidos_owner(self, p):
        return [{"owner": "LEGADO", "object_type": "PACKAGE BODY", "cnt": 14}, {"owner": "LEGADO", "object_type": "VIEW", "cnt": 9},
                {"owner": "APP_CORE", "object_type": "TRIGGER", "cnt": 2}, {"owner": "REPORTES", "object_type": "SYNONYM", "cnt": 6}]

    def _rebuild(self, p):
        return [{"owner": "APP_CORE", "index_name": "IX_MOV_FECHA", "table_name": "MOVIMIENTOS", "blevel": 4,
                 "leaf_blocks": 2_310_400, "last_analyzed": (self.hoy - timedelta(days=40)).isoformat()},
                {"owner": "INTEGRA", "index_name": "IX_HIST_PERIODO", "table_name": "HISTORICO", "blevel": 4,
                 "leaf_blocks": 690_020, "last_analyzed": (self.hoy - timedelta(days=95)).isoformat()}]

    def _stale_total(self, p):
        return [{"total": 37}]

    def _stale(self, p):
        return [{"owner": o, "table_name": t, "num_rows": n, "last_analyzed": (self.hoy - timedelta(days=d)).isoformat()}
                for o, t, n, d in [("APP_CORE", "MOVIMIENTOS", 1_840_220_114, 31), ("LEGADO", "CUENTAS", 12_400_331, 220),
                                   ("LEGADO", "CONCILIACION", 3_100_009, 180), ("CREDITO", "SOLICITUDES", 9_200_441, 15)]]

    def _alertlog(self, p):
        return [{"codigo": "ORA-01555", "cnt": 6, "ultimo": self._d(0.6)},
                {"codigo": "ORA-12012", "cnt": 4, "ultimo": self._d(0.3)},
                {"codigo": "ORA-00060", "cnt": 2, "ultimo": self._d(2)}]

    def _limites(self, p):
        return [{"resource_name": "processes", "current_utilization": 1_228, "max_utilization": 1_402, "limit_value": "1500"},
                {"resource_name": "sessions", "current_utilization": 1_310, "max_utilization": 1_590, "limit_value": "2272"}]

    def _sesiones_usuario(self, p):
        return [{"username": u, "total": t, "activas": a} for u, t, a in [
            ("APP_WEB", 640, 41), ("APP_BATCH", 220, 12), ("CREDITO", 180, 9), ("INTEGRA", 96, 3), ("DBSNMP", 4, 1)]]

    # ── sentencias costosas ─────────────────────────────────────────────────
    def _filas_sql(self):
        r = self._rnd("sql")
        filas = []
        for i, (sid, esq, mod, _prog, txt) in enumerate(_SQLS):
            ejec = int(r.choice([12, 340, 8_200, 150_000, 2_400_000]) * (1 + r.random()))
            ela = round(r.uniform(9_000, 60_000) / (1 + i * 0.55), 2)
            cpu = round(ela * r.uniform(0.15, 0.9), 2)
            gets = int(ela * r.uniform(2_000, 40_000))
            filas.append({"sql_id": sid, "parsing_schema_name": esq, "module": mod, "executions": ejec,
                          "elapsed_sec": ela, "cpu_sec": cpu, "elapsed_por_exec": round(ela / ejec, 4),
                          "cpu_por_exec": round(cpu / ejec, 4), "buffer_gets": gets,
                          "gets_por_exec": round(gets / ejec), "disk_reads": int(gets * r.uniform(0.01, 0.2)),
                          "rows_processed": ejec * r.randint(1, 40), "plan_hash_value": r.randint(10**8, 4 * 10**9),
                          "program_id": 88_120 + i if "PKG" in mod or "CIERRE" in mod else 0, "sql_text": txt[:200]})
        return filas

    def _s_top(self, clave: str, p):
        return sorted(self._filas_sql(), key=lambda f: -f[clave])[: int(p.get("top_n") or 20)]

    def _s_top_CPU(self, p):
        return self._s_top("cpu_sec", p)

    def _s_top_ELAPSED(self, p):
        return self._s_top("elapsed_sec", p)

    def _s_top_GETS(self, p):
        return self._s_top("buffer_gets", p)

    def _s_sesiones(self, p):
        return [{"total": 1_310, "activas": 66}]

    def _s_cpu_activas(self, p):
        return [{"sql_id": s, "sql_child_number": 0, "username": esq, "program": prog, "machine": "AP23SBCC09",
                 "sid": 2_000 + i, "serial": 101 + i, "seconds_active": 30 * (i + 1),
                 "cpu_sec_total": 4_000 - 900 * i, "elapsed_sec_total": 6_000 - 1_000 * i, "executions": 10 + i,
                 "cpu_per_exec_sec": round((4_000 - 900 * i) / (10 + i), 4), "buffer_gets": 9_000_000,
                 "disk_reads": 41_000, "sql_text": txt[:200]} for i, (s, esq, _m, prog, txt) in enumerate(_SQLS[:3])]

    def _s_waits(self, p):
        return self._waits(p)

    def _sql(self, sql_id):
        return next((x for x in _SQLS if x[0] == sql_id), None)

    def _s_origen_sql(self, p):
        x = self._sql(p.get("sql_id"))
        if not x:
            return []
        sid, esq, mod, _prog, _txt = x
        pls = "PKG" in mod or "CIERRE" in mod
        return [{"sql_id": sid, "module": mod, "action": "PROCESAR" if pls else None, "parsing_schema_name": esq,
                 "program_id": 88_120 if pls else 0, "program_line": 412 if pls else 0,
                 "first_load_time": self._d(9), "last_active_time": self._d(0.01),
                 "owner": esq if pls else None, "object_name": mod if pls else None,
                 "object_type": "PACKAGE BODY" if pls else None}]

    def _s_origen_sesion(self, p):
        x = self._sql(p.get("sql_id"))
        if not x or _SQLS.index(x) > 4:
            return []
        sid, esq, mod, prog, _txt = x
        return [{"sid": 2_000 + _SQLS.index(x), "serial": 4_411, "username": esq, "osuser": "svc_app",
                 "program": prog, "module": mod, "action": None, "machine": "AP23SBCC09", "client_pid": "18220",
                 "plsql_entry_object_id": 88_120 if "PKG" in mod else None, "plsql_entry_subprogram_id": 3,
                 "entry_owner": esq if "PKG" in mod else None, "entry_object": mod if "PKG" in mod else None,
                 "entry_type": "PACKAGE" if "PKG" in mod else None}]

    def _s_largo(self, p):
        x = self._sql(p.get("sql_id"))
        return [{"total_length": len(x[4]) if x else 0}]

    def _s_texto(self, p):
        x = self._sql(p.get("sql_id"))
        if not x:
            return []
        desde = int(p.get("desde") or 1)
        return [{"parte": x[4][desde - 1: desde - 1 + 4000] or None}]

    # ── hoja de vida (consultas en línea del script original) ───────────────
    def _hoja_vida(self, n: str) -> list[dict]:
        db = self._db_name()
        if "from v$instance" in n:
            return [{"instance_name": db.lower() + "1", "host_name": "dbprd01", "version_full": "19.24.0.0.0",
                     "version": "19.0.0.0.0", "status": "OPEN", "database_status": "ACTIVE", "logins": "ALLOWED",
                     "startup_time": self._d(41), "uptime_days": 41.3}]
        if "from v$database" in n and "db_unique_name" in n:
            return [{"name": db, "db_unique_name": db, "dbid": 3_419_220_118, "open_mode": "READ WRITE",
                     "log_mode": "ARCHIVELOG", "flashback_on": "NO", "force_logging": "YES",
                     "protection_mode": "MAXIMUM PERFORMANCE", "database_role": "PRIMARY",
                     "platform_name": "Linux x86 64-bit", "created": "2019-03-11 10:20"}]
        if "from v$database" in n:
            return [{"log_mode": "ARCHIVELOG", "force_logging": "YES"}]
        if "v$osstat" in n:
            return [{"stat_name": k, "value": v} for k, v in [("NUM_CPUS", 32), ("NUM_CPU_CORES", 16),
                                                               ("NUM_CPU_SOCKETS", 2), ("PHYSICAL_MEMORY_BYTES", 274877906944)]]
        if "v$tempseg_usage" in n:
            return [{"sid": 2001, "username": "APP_BATCH", "program": "JDBC Thin Client", "temp_mb": 4096.0}]
        if "control_management_pack_access'" in n and "from v$parameter where name=" in n:
            return [{"value": "DIAGNOSTIC+TUNING"}]
        if "from v$parameter" in n and "'processes'" in n:
            vals = {"processes": "1500", "sessions": "2272", "sga_target": "34359738368", "sga_max_size": "34359738368",
                    "pga_aggregate_target": "8589934592", "memory_target": "0", "memory_max_target": "0",
                    "cluster_database": "TRUE", "compatible": "19.0.0", "db_recovery_file_dest_size": "2199023255552",
                    "control_management_pack_access": "DIAGNOSTIC+TUNING", "statistics_level": "TYPICAL",
                    "undo_management": "AUTO", "undo_tablespace": "UNDOTBS1", "undo_retention": "10800"}
            return [{"name": k, "value": v, "display_value": v} for k, v in sorted(vals.items())]
        if "from v$parameter" in n and "undo_management" in n:
            return [{"name": "undo_management", "value": "AUTO"}, {"name": "undo_retention", "value": "10800"},
                    {"name": "undo_tablespace", "value": "UNDOTBS1"}]
        if "dba_registry_sqlpatch" in n:
            return [{"patch_id": 36233263, "patch_type": "RU", "action": "APPLY", "status": "SUCCESS",
                     "action_time": (self.hoy - timedelta(days=410)).isoformat(),
                     "description": "Database Release Update : 19.24.0.0.240716"}]
        if "from dba_registry" in n:
            return self._registro({})
        if "dba_scheduler_jobs" in n and "count(*) as total" in n:
            return [{"total": 42, "enabled_cnt": 38, "broken_cnt": 1, "total_failures": 21}]
        if "dba_scheduler_jobs" in n:
            return [{k: j[k] for k in ("owner", "job_name", "state", "failure_count", "enabled")} | {"last_start": j["last_start"]}
                    for j in self._jobs_fallidos({})]
        if "dba_tablespace_usage_metrics" in n:
            filas = []
            for t, mx, pct in [("APP_DATA", 4_194_304, 96.1), ("APP_IDX", 2_097_152, 83.4), ("SYSAUX", 65_536, 71.0),
                               ("USERS", 32_768, 40.2), ("SYSTEM", 32_768, 12.5)]:
                filas.append({"tablespace_name": t, "contents": "PERMANENT", "alloc_mb": mx * 0.9, "max_mb": mx,
                              "used_mb": round(mx * pct / 100, 2), "pct_used": pct, "pct_max_used": pct})
            return filas
        if "dba_temp_files" in n:
            return [{"total_gb": 4243.6}]
        if "from dba_segments group by owner" in n:
            return [{"owner": e["owner"], "gb": e["gb"]} for e in self._esquemas({})]
        if "v$asm_diskgroup" in n:
            return [{"name": d["name"], "state": "MOUNTED", "type": "EXTERN", "total_gb": round(d["total_mb"] / 1024, 2),
                     "free_gb": round(d["free_mb"] / 1024, 2), "pct_used": d["pct_used"]} for d in self._dg({})]
        if "from v$log " in n or n.endswith("from v$log order by thread#, group#"):
            return [{"group#": g, "thread#": 1 + (g > 3), "sequence#": 88_000 + g, "size_mb": 4096.0, "members": 2,
                     "archived": "YES" if g % 3 else "NO", "status": "CURRENT" if g in (3, 6) else "INACTIVE"}
                    for g in range(1, 7)]
        if "v$log_history" in n:
            r = self._rnd("sw")
            return [{"hour": (datetime.now() - timedelta(hours=h)).strftime("%Y-%m-%d %H"), "switches": r.randint(2, 14)}
                    for h in range(24)]
        if "dba_temp_free_space" in n:
            return [{"tablespace_name": "TEMP", "total_mb": 262_144.0, "allocated_mb": 98_304.0, "free_mb": 180_224.0}]
        if "dba_undo_extents" in n:
            return [{"tablespace_name": "UNDOTBS1", "status": s, "size_mb": m, "extents": e}
                    for s, m, e in [("ACTIVE", 1_024.0, 12), ("EXPIRED", 40_960.0, 880), ("UNEXPIRED", 18_432.0, 402)]]
        if "v$rman_backup_job_details" in n:
            return [{"input_type": f["input_type"], "status": f["status"], "start_time": f["start_time"],
                     "end_time": f["start_time"], "duration_min": f["duration_min"], "input_gb": f["output_gb"] * 1.6,
                     "output_gb": f["output_gb"]} for f in self._rman_7d({})]
        if "v$recovery_file_dest" in n:
            return [{"name": "+FRA", "limit_gb": 2048.0, "used_gb": 1310.0, "reclaimable_gb": 420.0, "pct_used": 63.96}]
        if "from v$sql where executions > 0" in n:
            return [{"sql_id": f["sql_id"], "executions": f["executions"], "elapsed_sec": f["elapsed_sec"],
                     "cpu_sec": f["cpu_sec"], "buffer_gets": f["buffer_gets"], "disk_reads": f["disk_reads"],
                     "sql_text": f["sql_text"][:120]} for f in self._s_top_ELAPSED({"top_n": 12})]
        if "v$system_event" in n:
            return [{"event": w["event"], "wait_class": w["wait_class"], "total_waits": w["total_waits"],
                     "time_waited_sec": w["time_waited_sec"], "avg_wait_sec": w["avg_wait_ms"] / 1000}
                    for w in self._waits({})]
        if "from v$sga" in n:
            return [{"sga_mb": 32_768.0}]
        if "aggregate pga auto target" in n:
            return [{"pga_target_mb": 6_144.0}]
        if "dba_hist_snapshot" in n:
            return [{"snaps": 192, "min_snap": self._d(8), "max_snap": self._d(0.04)}]
        if "from v$session" in n and "background_cnt" in n:
            return [{"total": 1_310, "active_cnt": 66, "background_cnt": 74}]
        if "from v$session s1 join v$session s2" in n:
            return [{"blocking_sid": 1234, "blocking_user": "APP_BATCH", "waiting_sid": 2001 + i, "waiting_user": "APP_WEB",
                     "wait_seconds": 300 - 40 * i, "wait_event": "enq: TX - row lock contention"} for i in range(3)]
        if "from v$session" in n:
            return [{"sid": 2000 + i, "username": "APP_WEB", "program": "JDBC Thin Client", "machine": "AP23SBCC09",
                     "event": "db file sequential read", "seconds_in_wait": i} for i in range(5)]
        if "dba_users group by account_status" in n:
            return [{"account_status": s, "cnt": c} for s, c in [("OPEN", 88), ("EXPIRED & LOCKED", 41), ("LOCKED", 12)]]
        if "granted_role='dba'" in n:
            return [{"grantee": g} for g in ("DBA_ANTIGUO", "DBA_SOPORTE", "SYS", "SYSTEM")]
        if "dba_db_links" in n:
            return [{"owner": "INTEGRA", "db_link": "CRMPRD.LINK", "host": "CRMPRD", "created": "2021-06-02"}]
        if "from dba_objects where status='invalid' group by object_type" in n:
            return [{"object_type": "PACKAGE BODY", "cnt": 14}, {"object_type": "VIEW", "cnt": 9},
                    {"object_type": "SYNONYM", "cnt": 6}, {"object_type": "TRIGGER", "cnt": 2}]
        if "from dba_objects where status='invalid'" in n:
            return [{"owner": "LEGADO", "object_name": f"PKG_REPORTES_{i:02d}", "object_type": "PACKAGE BODY",
                     "last_ddl": (self.hoy - timedelta(days=200 + i)).isoformat()} for i in range(8)]
        if "from dba_objects group by object_type" in n:
            return [{"object_type": t, "cnt": c} for t, c in [("SYNONYM", 41_220), ("JAVA CLASS", 30_118), ("VIEW", 7_220),
                                                               ("INDEX", 5_880), ("TABLE", 4_102), ("PACKAGE", 1_410)]]
        return []
