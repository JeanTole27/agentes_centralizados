"""Conexión a Oracle con las protecciones de 04 §3.

- oracledb en modo thin (sin cliente Oracle instalado) por defecto. Si las bases exigen
  Native Network Encryption o son versiones antiguas (DPY-3010 / DPY-3001), thin no sirve:
  definir [bd] oracle_client_dir (o la variable ORACLE_CLIENT_DIR) con la carpeta del
  Oracle Instant Client y se usa modo thick.
- call_timeout por llamada (20 s por defecto).
- Trazabilidad: CLIENT_IDENTIFIER = persona, MODULE = 'OpsCentral', ACTION = ejecución.
  Así el DBA de turno ve en V$SESSION quién está detrás de cada sesión de servicio.
- OPS_CONSULTA solo consulta plantillas; OPS_DBA solo llama procedimientos del paquete.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..credenciales import CredencialBD
from ..inventario import Activo
from .plantillas import BINDS, PLANTILLAS
from .politica import validar_lectura, validar_procedimiento


class ErrorBD(RuntimeError):
    pass


_modo_iniciado = False


def iniciar_cliente_oracle() -> str:
    """Activa el modo thick una sola vez, antes de la primera conexión, si hay ruta de cliente.

    Orden: [bd] oracle_client_dir del TOML y, si está vacío, la variable ORACLE_CLIENT_DIR.
    Sin ruta se queda en thin. Con ruta inválida falla con un mensaje claro (no cae a thin en
    silencio, porque las bases que exigen thick fallarían después con un error confuso).
    Devuelve "thick" o "thin".
    """
    global _modo_iniciado
    import oracledb
    if _modo_iniciado:
        return "thin" if oracledb.is_thin_mode() else "thick"
    from ..config import RAIZ, cargar
    ruta = (str(cargar()["bd"].get("oracle_client_dir") or "").strip()
            or os.environ.get("ORACLE_CLIENT_DIR", "").strip())
    if ruta:
        p = Path(ruta)
        p = p if p.is_absolute() else (RAIZ / p).resolve()
        if not p.is_dir():
            raise ErrorBD(f"oracle_client_dir no existe o no es carpeta: {p}")
        try:
            oracledb.init_oracle_client(lib_dir=str(p))
        except oracledb.Error as e:
            raise ErrorBD(f"no se pudo cargar el Oracle Client desde {p}: {e}") from e
    _modo_iniciado = True
    return "thin" if oracledb.is_thin_mode() else "thick"


class ConexionOracle:
    def __init__(self, base: Activo, cred: CredencialBD, *, persona: str, ejecucion_id: str,
                 paquete: str, call_timeout_s: int):
        self.base, self.cred, self.persona, self.eid = base, cred, persona, ejecucion_id
        self.paquete, self.timeout = paquete, call_timeout_s
        self._con = None

    def __enter__(self):
        import oracledb  # import diferido: la API no lo necesita
        iniciar_cliente_oracle()
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
        binds = {k: (params or {}).get(k) for k in BINDS.get(plantilla, ())}   # opcionales llegan como NULL
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

    def consultar_sql(self, sql: str, params: dict | None = None, max_filas: int = 500) -> list[dict[str, Any]]:
        """SQL de los generadores de reportes (bd/reportes). Solo con OPS_CONSULTA, solo si es una de
        las consultas fijas escritas en los scripts y si la política de lectura la acepta."""
        from .reportes import es_plantilla_fija
        if self.cred.tipo != "consulta":
            raise ErrorBD("los reportes solo consultan con la cuenta de lectura")
        if not es_plantilla_fija(sql):
            raise ErrorBD("consulta de reporte rechazada: no es una de las consultas fijas de bd/reportes")
        ok, msg = validar_lectura(sql)
        if not ok:
            raise ErrorBD(f"consulta de reporte rechazada por la política: {msg}")
        nombres = set(re.findall(r"(?<!:):([a-zA-Z_]\w*)", re.sub(r"'[^']*'", "''", sql)))
        binds = {k: v for k, v in (params or {}).items() if k in nombres}
        with self._con.cursor() as cur:
            cur.execute(sql, binds)
            cols = [c[0].lower() for c in cur.description]
            filas = []
            for r in cur.fetchmany(max(1, min(int(max_filas), 5000))):
                filas.append({c: (v.read() if hasattr(v, "read") else v) for c, v in zip(cols, r)})
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
