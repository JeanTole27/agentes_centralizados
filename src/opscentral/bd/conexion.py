"""Conexión a Oracle con las protecciones de 04 §3.

- oracledb en modo thin (sin cliente Oracle instalado). Si las bases exigen
  Native Network Encryption, thin no la soporta: usar listener TCPS o modo thick.
- call_timeout por llamada (20 s por defecto).
- Trazabilidad: CLIENT_IDENTIFIER = persona, MODULE = 'OpsCentral', ACTION = ejecución.
  Así el DBA de turno ve en V$SESSION quién está detrás de cada sesión de servicio.
- OPS_CONSULTA solo consulta plantillas; OPS_DBA solo llama procedimientos del paquete.
"""
from __future__ import annotations

from typing import Any

from ..credenciales import CredencialBD
from ..inventario import Activo
from .plantillas import BINDS, PLANTILLAS
from .politica import validar_lectura, validar_procedimiento


class ErrorBD(RuntimeError):
    pass


class ConexionOracle:
    def __init__(self, base: Activo, cred: CredencialBD, *, persona: str, ejecucion_id: str,
                 paquete: str, call_timeout_s: int):
        self.base, self.cred, self.persona, self.eid = base, cred, persona, ejecucion_id
        self.paquete, self.timeout = paquete, call_timeout_s
        self._con = None

    def __enter__(self):
        import oracledb  # import diferido: no se necesita en simulación
        dsn = self.base.datos.get("dsn")
        if not dsn:
            raise ErrorBD(f"la base {self.base.id} no tiene DSN en el inventario")
        self._con = oracledb.connect(user=self.cred.usuario, password=self.cred.clave, dsn=dsn,
                                     tcp_connect_timeout=10)
        self._con.call_timeout = int(self.timeout * 1000)
        self._con.client_identifier = self.persona[:64]
        self._con.module = "OpsCentral"
        self._con.action = self.eid[:32]
        self._con.clientinfo = f"opscentral:{self.cred.tipo}"
        return self

    def __exit__(self, *exc):
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None

    def consultar(self, plantilla: str, params: dict | None = None) -> list[dict[str, Any]]:
        sql = PLANTILLAS[plantilla]
        ok, msg = validar_lectura(sql)
        if not ok:
            raise ErrorBD(msg)
        binds = {k: (params or {})[k] for k in BINDS.get(plantilla, ())}
        with self._con.cursor() as cur:
            cur.execute(sql, binds)
            cols = [c[0].lower() for c in cur.description]
            filas = []
            for r in cur.fetchmany(5000):
                fila = {}
                for c, v in zip(cols, r):
                    fila[c] = v.read() if hasattr(v, "read") else v
                filas.append(fila)
            return filas

    def llamar(self, procedimiento: str, args: dict[str, Any]) -> str:
        """Llama ADMINBD.PKG_OPS_CENTRAL.<proc>. No se construye SQL: callproc con parámetros con nombre."""
        if self.cred.tipo != "dba":
            raise ErrorBD("los procedimientos de cambio solo se llaman con OPS_DBA")
        validar_procedimiento(procedimiento)
        with self._con.cursor() as cur:
            salida = cur.var(str)
            kw = {f"p_{k}": v for k, v in args.items()}
            kw["p_ejecucion"] = self.eid
            kw["p_resultado"] = salida
            cur.callproc(f"{self.paquete}.{procedimiento.upper()}", keyword_parameters=kw)
            return salida.getvalue() or ""
