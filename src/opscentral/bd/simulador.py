"""Bases Oracle simuladas para desarrollo (general.simulacion = true).

Tiene la misma interfaz que ConexionOracle y un estado en memoria por base, para
que los flujos completos tengan sentido: al tomar el snapshot DESPUÉS aparecen
objetos inválidos nuevos (como si el despliegue los hubiera dejado), recompilar
los corrige y terminar una sesión elimina el bloqueo.
"""
from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta

from ..credenciales import CredencialBD
from ..inventario import Activo
from .conexion import ErrorBD
from .politica import validar_procedimiento

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


class ConexionSimulada:
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
        raise ErrorBD(f"plantilla no simulada: {plantilla}")

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
        if p == "ACTUALIZAR_LINEA_BASE":
            o = {"owner": args["propietario"], "object_name": args["objeto"], "object_type": args["tipo_objeto"],
                 "justificacion": args["justificacion"], "registrado_por": self.persona, "fecha_registro": datetime.now()}
            if args["operacion"] == "AGREGAR":
                e["linea_base"].append(o)
            else:
                e["linea_base"] = [x for x in e["linea_base"] if (x["owner"], x["object_name"]) !=
                                   (o["owner"], o["object_name"])]
            return args["operacion"]
        return "OK (simulado)"
