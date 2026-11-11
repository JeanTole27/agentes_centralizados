"""Doble de prueba de Oracle (solo para tests; no forma parte del producto).

Tiene la misma interfaz que ConexionOracle y un estado en memoria por base, para
que los flujos completos tengan sentido: al tomar el snapshot DESPUÉS aparecen
objetos inválidos nuevos (como si el despliegue los hubiera dejado), recompilar
los corrige y terminar una sesión elimina el bloqueo.
"""
from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta

from opscentral.bd.conexion import ErrorBD
from opscentral.bd.politica import validar_lectura, validar_procedimiento
from opscentral.credenciales import CredencialBD
from opscentral.inventario import Activo

_LOCK = threading.Lock()
_ESTADO: dict[str, dict] = {}

PROTEGIDOS = {"SYS", "SYSTEM", "OPS_CONSULTA", "OPS_DBA", "ADMINBD", "DBSNMP"}


def _estado(base: str) -> dict:
    with _LOCK:
        if base not in _ESTADO:
            rnd = random.Random(base)
            _ESTADO[base] = {
                "invalidos": [
                    {"owner": "LEGADO", "object_name": "PKG_REPORTES_OLD", "object_type": "PACKAGE BODY"},
                    {"owner": "APP_CORE", "object_name": "BIN$q1w2e3==$0", "object_type": "TRIGGER"},
                ],
                "linea_base": [{"owner": "LEGADO", "object_name": "PKG_REPORTES_OLD", "object_type": "PACKAGE BODY",
                                "justificacion": "Objeto obsoleto conocido", "registrado_por": "dba.demo",
                                "fecha_registro": datetime(2026, 1, 15)}],
                "bloqueo": rnd.random() < 0.7,
                "ts_pct": {"USERS": 62.0, "APP_DATA": 78.0 + rnd.random() * 10, "UNDOTBS1": 35.0, "SYSAUX": 71.0},
                "dg_pct": {"DATA": 74.0 + rnd.random() * 10, "FRA": 55.0},
                "usuarios": {"APP_CORE": "OPEN", "APP_BATCH": "LOCKED(TIMED)", "APP_WEB": "OPEN"},
                "cambio_aplicado": False,
            }
        return _ESTADO[base]


def reiniciar() -> None:
    with _LOCK:
        _ESTADO.clear()


def simular_despliegue(base: str) -> None:
    """Deja 3 objetos inválidos nuevos, como haría un despliegue con dependencias rotas."""
    e = _estado(base)
    if e["cambio_aplicado"]:
        return
    e["cambio_aplicado"] = True
    e["invalidos"] += [
        {"owner": "APP_CORE", "object_name": "PKG_CLIENTES", "object_type": "PACKAGE BODY"},
        {"owner": "APP_CORE", "object_name": "VW_SALDOS", "object_type": "VIEW"},
        {"owner": "APP_CORE", "object_name": "TRG_AUDITA_CLIENTE", "object_type": "TRIGGER"},
    ]


class ConexionFalsa:
    def __init__(self, base: Activo, cred: CredencialBD, *, persona: str, ejecucion_id: str,
                 paquete: str, call_timeout_s: int):
        self.base, self.cred, self.persona, self.eid = base, cred, persona, ejecucion_id

    def __enter__(self):
        if self.base.datos.get("simular_caida"):
            raise ErrorBD("ORA-12541: TNS:no listener (simulado)")
        return self

    def __exit__(self, *exc):
        return False

    def consultar(self, plantilla: str, params: dict | None = None) -> list[dict]:
        e = _estado(self.base.id)
        ahora = datetime.now()
        if plantilla == "salud_instancia":
            return [{"instance_name": self.base.id.lower(), "host_name": self.base.datos.get("servidor", "").lower(),
                     "version": "19.0.0.0.0", "status": "OPEN", "startup_time": ahora - timedelta(days=41),
                     "database_role": "PRIMARY", "open_mode": "READ WRITE", "sesiones": 180, "procesos": 1500}]
        if plantilla == "tablespaces":
            return [{"tablespace_name": k, "usado_mb": int(v * 100), "maximo_mb": 10000, "pct_usado": round(v, 1)}
                    for k, v in sorted(e["ts_pct"].items(), key=lambda x: -x[1])]
        if plantilla == "diskgroups":
            return [{"name": k, "state": "MOUNTED", "type": "EXTERN", "total_mb": 2_000_000,
                     "free_mb": int(2_000_000 * (1 - v / 100)), "usable_file_mb": int(2_000_000 * (1 - v / 100)),
                     "pct_usado": round(v, 1)} for k, v in e["dg_pct"].items()]
        if plantilla == "invalidos":
            return [dict(o, last_ddl_time=ahora) for o in e["invalidos"]]
        if plantilla == "linea_base":
            return [dict(o) for o in e["linea_base"]]
        if plantilla == "bloqueos":
            if not e["bloqueo"]:
                return []
            raiz = {"inst_id": 1, "sid": 1234, "serial": 5678, "username": "APP_BATCH", "status": "INACTIVE",
                    "machine": "AP23SBCC04", "program": "JDBC Thin Client", "module": "BatchCierre",
                    "sql_id": None, "event": "SQL*Net message from client", "seconds_in_wait": 0,
                    "last_call_et": 840, "blocking_instance": None, "blocking_session": None,
                    "final_blocking_instance": None, "final_blocking_session": None, "objeto": None}
            hijos = [{"inst_id": 1, "sid": 2000 + i, "serial": 100 + i, "username": "APP_WEB", "status": "ACTIVE",
                      "machine": "AP23SBCC09", "program": "JDBC Thin Client", "module": "ClientesWeb",
                      "sql_id": "7h35uxf5uhmm1", "event": "enq: TX - row lock contention",
                      "seconds_in_wait": 300 - i * 40, "last_call_et": 300 - i * 40, "blocking_instance": 1,
                      "blocking_session": 1234, "final_blocking_instance": 1, "final_blocking_session": 1234,
                      "objeto": "APP_CORE.CLIENTES"} for i in range(4)]
            return [raiz] + hijos
        if plantilla == "sesiones_activas":
            return [{"inst_id": 1, "sid": 2000 + i, "serial": 100 + i, "username": "APP_WEB", "machine": "AP23SBCC09",
                     "module": "ClientesWeb", "sql_id": "7h35uxf5uhmm1", "event": "CPU", "last_call_et": i}
                    for i in range(5)]
        if plantilla == "sql_texto":
            return [{"sql_id": (params or {}).get("sql_id"), "plan_hash_value": 123456789, "executions": 10,
                     "elapsed_time": 9_000_000, "sql_fulltext": "UPDATE clientes SET saldo = :1 WHERE id = :2"}]
        if plantilla == "usuario_estado":
            u = (params or {}).get("usuario", "")
            if u not in e["usuarios"]:
                return []
            return [{"username": u, "account_status": e["usuarios"][u], "lock_date": None,
                     "expiry_date": ahora + timedelta(days=9), "profile": "PRF_APLICACION", "last_login": ahora}]
        if plantilla == "usuarios_por_expirar":
            return [{"username": "APP_BATCH", "account_status": "OPEN", "expiry_date": ahora + timedelta(days=9),
                     "profile": "PRF_APLICACION", "dias_restantes": 9.0}]
        if plantilla in _EXTRA:
            r = _EXTRA[plantilla](self.base.id, params or {}, ahora)
            if isinstance(r, Exception):
                raise r
            return r
        raise ErrorBD(f"plantilla no simulada: {plantilla}")

    def consultar_sql(self, sql: str, params: dict | None = None, max_filas: int = 500) -> list[dict]:
        """SQL de los scripts de reporte: misma validación que la conexión real; las filas
        salen del cliente de muestra de los reportes."""
        from opscentral.bd.reportes import es_plantilla_fija
        if self.cred.tipo != "consulta":
            raise ErrorBD("los reportes solo consultan con la cuenta de lectura")
        if not es_plantilla_fija(sql):
            raise ErrorBD("consulta de reporte rechazada: no es una de las consultas fijas de bd/reportes")
        ok, msg = validar_lectura(sql)
        if not ok:
            raise ErrorBD(f"consulta de reporte rechazada por la política: {msg}")
        from opscentral.bd.reportes.muestra import ClienteMuestra
        return ClienteMuestra(self.base.id).query(sql, params, max_filas)

    def llamar(self, procedimiento: str, args: dict) -> str:
        if self.cred.tipo != "dba":
            raise ErrorBD("los procedimientos de cambio solo se llaman con OPS_DBA")
        validar_procedimiento(procedimiento)
        e = _estado(self.base.id)
        p = procedimiento.upper()
        if p == "RECOMPILAR_OBJETO":
            clave = (args["propietario"], args["objeto"], args["tipo_objeto"])
            if args["objeto"].startswith("BIN$"):
                raise ErrorBD("ORA-20010: no se recompilan objetos de la papelera")
            if any((o["owner"], o["object_name"], o["object_type"]) == clave for o in e["linea_base"]):
                raise ErrorBD("ORA-20011: el objeto está en la línea base")
            # el trigger depende de la vista: compila en la segunda pasada
            if args["objeto"] == "TRG_AUDITA_CLIENTE" and any(o["object_name"] == "VW_SALDOS" for o in e["invalidos"]):
                return "INVALID"
            e["invalidos"] = [o for o in e["invalidos"] if (o["owner"], o["object_name"], o["object_type"]) != clave]
            return "VALID"
        if p == "TERMINAR_SESION":
            if not e["bloqueo"] or int(args["sid"]) != 1234:
                raise ErrorBD("ORA-20020: la sesión no existe o ya terminó")
            e["bloqueo"] = False
            return "TERMINADA"
        if p == "DESBLOQUEAR_USUARIO":
            u = args["usuario"]
            if u in PROTEGIDOS:
                raise ErrorBD("ORA-20030: usuario protegido")
            if u not in e["usuarios"]:
                raise ErrorBD("ORA-20031: el usuario no existe")
            e["usuarios"][u] = "OPEN"
            return "OPEN"
        return "OK (simulado)"


# ---------------------------------------------------------------- plantillas de diagnóstico y reportes
def _objetos(base, p, ahora):
    comunes = [{"object_name": "PKG_CLIENTES", "object_type": "PACKAGE BODY", "status": "VALID", "last_ddl_time": ahora},
               {"object_name": "CLIENTES", "object_type": "TABLE", "status": "VALID", "last_ddl_time": ahora}]
    if base == "CORPRD":
        return comunes + [{"object_name": "VW_SALDOS", "object_type": "VIEW", "status": "INVALID", "last_ddl_time": ahora}]
    return comunes + [{"object_name": "VW_SALDOS", "object_type": "VIEW", "status": "VALID", "last_ddl_time": ahora},
                      {"object_name": "PKG_NUEVO", "object_type": "PACKAGE", "status": "VALID", "last_ddl_time": ahora}]


def _parametros(base, p, ahora):
    comun = [{"name": "processes", "value": "1500", "isdefault": "FALSE"},
             {"name": "audit_trail", "value": "DB", "isdefault": "FALSE"},
             {"name": "db_unique_name", "value": base.lower(), "isdefault": "FALSE"}]
    sga = "32G" if base == "CORPRD" else "16G"
    return comun + [{"name": "sga_target", "value": sga, "isdefault": "FALSE"}]


_EXTRA = {
    "idle_tx": lambda b, p, a: [
        {"inst_id": 1, "sid": 310, "serial": 77, "username": "APP_BATCH", "machine": "AP23SBCC04",
         "segundos_inactiva": 4000, "bloques_undo": 1200},
        {"inst_id": 1, "sid": 311, "serial": 78, "username": "APP_WEB", "machine": "AP23SBCC09",
         "segundos_inactiva": 700, "bloques_undo": 3}][: 2 if int(p.get("min_segundos") or 0) <= 700 else 1],
    "sesiones_por_maquina": lambda b, p, a: [{"inst_id": 1, "username": "APP_WEB", "machine": p.get("maquina"),
                                              "status": "ACTIVE", "sesiones": 12, "bloqueadas": 0}],
    "fra_uso": lambda b, p, a: [{"name": "+FRA", "limite_mb": 500000, "usado_mb": 470000, "recuperable_mb": 10000,
                                 "archivos": 900, "pct_usado": 92.0}],
    "archivelog_hora": lambda b, p, a: [{"hora": a - timedelta(hours=h), "archivos": 4, "mb": 2000 if h == 3 else 400}
                                        for h in range(12)],
    "dg_rol": lambda b, p, a: [{"database_role": "PRIMARY", "protection_mode": "MAXIMUM PERFORMANCE",
                                "force_logging": "YES"}],
    "dg_stats": lambda b, p, a: ErrorBD("ORA-00942: table or view does not exist"),
    "dg_gaps": lambda b, p, a: [],
    "rman_7d": lambda b, p, a: [
        {"input_type": "DB INCR", "status": "COMPLETED", "start_time": a - timedelta(days=2, hours=1),
         "end_time": a - timedelta(days=2)},
        {"input_type": "ARCHIVELOG", "status": "COMPLETED", "start_time": a - timedelta(hours=2),
         "end_time": a - timedelta(hours=1)},
        {"input_type": "DB INCR", "status": "FAILED", "start_time": a - timedelta(days=1), "end_time": None}],
    "jobs_problema": lambda b, p, a: [{"owner": "APP_CORE", "job_name": "JOB_CIERRE", "enabled": "TRUE",
                                       "state": "BROKEN", "failure_count": 16}],
    "jobs_fallidos_24h": lambda b, p, a: [{"owner": "APP_CORE", "job_name": "JOB_CIERRE", "status": "FAILED"}] * 3,
    "undo_24h": lambda b, p, a: [{"undoblks": 100, "maxquerylen": 5400, "ora_01555": 1, "sin_espacio": 0}],
    "temp_sesiones": lambda b, p, a: [{"sid": 2001, "username": "APP_REP", "sql_id": "7h35uxf5uhmm1", "mb": 20480}],
    "alertlog_24h": lambda b, p, a: [
        {"momento": a, "mensaje": "ORA-00600: internal error code, arguments: [kcbz_check]"},
        {"momento": a, "mensaje": "ORA-00600: internal error code, arguments: [kcbz_check]"},
        {"momento": a, "mensaje": "ORA-01555 caused by SQL statement below"}],
    "esperas_sistema": lambda b, p, a: [{"event": "db file sequential read", "wait_class": "User I/O"}],
    "esperas_ahora": lambda b, p, a: [{"wait_class": "Concurrency", "event": "enq: TX - row lock contention",
                                       "sesiones": 8}, {"wait_class": "CPU", "event": "CPU", "sesiones": 2}],
    "parametros": _parametros,
    "parches": lambda b, p, a: [{"patch_id": 36233263, "action": "APPLY", "status": "SUCCESS",
                                 "description": "Database Release Update : 19.23.0.0.240416", "action_time": a}],
    "usr_roles": lambda b, p, a: [{"grantee": p.get("usuario"), "granted_role": "CONNECT"},
                                  {"grantee": p.get("usuario"), "granted_role": "DBA"}],
    "usr_privs_sistema": lambda b, p, a: [{"grantee": p.get("usuario"), "privilege": "SELECT ANY TABLE",
                                           "admin_option": "NO"}],
    "usr_privs_objeto": lambda b, p, a: [{"owner": "APP_CORE", "objeto": "CLIENTES", "privilege": "SELECT",
                                          "grantable": "YES"}],
    "shrink_candidatos": lambda b, p, a: [{"segment_owner": "APP_CORE", "segment_name": "LOG_EVENTOS",
                                           "recuperable_mb": 5120}],
    "papelera": lambda b, p, a: [{"owner": "APP_CORE", "object_name": "BIN$q1w2e3==$0", "bloques": 1000}],
    "objetos_esquema": _objetos,
    "topsql_cpu": lambda b, p, a: [{"sql_id": "7h35uxf5uhmm1", "cpu_s": 900}],
    "topsql_elapsed": lambda b, p, a: [{"sql_id": "7h35uxf5uhmm1", "elapsed_s": 1800},
                                       {"sql_id": "0w2qpuc6u2zsp", "elapsed_s": 300}],
    "topsql_gets": lambda b, p, a: [{"sql_id": "0w2qpuc6u2zsp", "buffer_gets": 10 ** 9}],
    "plan_historia": lambda b, p, a: [
        {"plan_hash_value": 111, "ejecuciones": 5000, "ms_por_ejec": 12.0, "desde": a - timedelta(days=20)},
        {"plan_hash_value": 222, "ejecuciones": 800, "ms_por_ejec": 95.0, "desde": a - timedelta(days=1)}],
    "plan_actual": lambda b, p, a: [{"inst_id": 1, "plan_hash_value": 222, "ms_por_ejec": 97.0}],
    "longops": lambda b, p, a: [{"sid": 900, "opname": "RMAN: incremental datafile backup", "pct": 40.0,
                                 "time_remaining": 1200}],
    "stats_obsoletas": lambda b, p, a: [{"owner": "APP_CORE", "table_name": "CLIENTES", "stale_stats": "YES"},
                                        {"owner": "APP_CORE", "table_name": "MOVIMIENTOS", "stale_stats": "YES"}],
}
