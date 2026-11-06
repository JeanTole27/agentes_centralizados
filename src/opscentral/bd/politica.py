"""Política SQL: reutiliza SqlPolicyEngine del kit Oracle y agrega reglas propias.

Si el kit está en la ruta configurada (bd.ruta_kit_oracle), se usa su motor tal
cual. Si no (por ejemplo, en pruebas), se aplica una versión mínima equivalente.
Además, los cambios solo pueden ser llamadas a procedimientos del paquete
ADMINBD.PKG_OPS_CENTRAL que estén en la lista permitida.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from ..config import RAIZ

PROCEDIMIENTOS = {
    "TERMINAR_SESION", "RECOMPILAR_OBJETO", "COMPILAR_ESQUEMA", "ACTUALIZAR_LINEA_BASE", "DESBLOQUEAR_USUARIO",
    "AGREGAR_DATAFILE", "RECOLECTAR_ESTADISTICAS", "EJECUTAR_JOB", "CAMBIAR_ESTADO_JOB",
}

_BLOQUEADOS = [r"\bDROP\s+(DATABASE|TABLESPACE|USER)\b", r"\bTRUNCATE\b", r"\bALTER\s+SYSTEM\b",
               r"\bALTER\s+DATABASE\b", r"\bSHUTDOWN\b", r"\bSTARTUP\b", r"\bGRANT\s+(DBA|SYSDBA|SYSOPER)\b",
               r"\bDBMS_SYS_SQL\b", r"\bDBMS_IJOB\b"]

_motor = None


def _cargar_kit():
    global _motor
    if _motor is not None:
        return _motor
    _motor = False
    try:
        from ..config import cargar
        ruta = Path(cargar()["bd"]["ruta_kit_oracle"])
        ruta = ruta if ruta.is_absolute() else (RAIZ / ruta).resolve()
        if (ruta / "mcp_servers" / "shared" / "policy.py").exists():
            if str(ruta) not in sys.path:
                sys.path.insert(0, str(ruta))
            from mcp_servers.shared.policy import SqlPolicyEngine  # type: ignore
            _motor = SqlPolicyEngine
    except Exception:  # el kit trae dependencias (fastmcp); si faltan, se usa la versión mínima
        _motor = False
    return _motor


def origen_politica() -> str:
    return "kit Oracle (SqlPolicyEngine)" if _cargar_kit() else "mínima interna"


def _normalizar(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"\s+", " ", sql).strip().upper()


def validar_lectura(sql: str) -> tuple[bool, str]:
    kit = _cargar_kit()
    if kit:
        return kit.validate_readonly(sql)
    n = _normalizar(sql)
    if ";" in n.rstrip(";"):
        return False, "múltiples sentencias no permitidas"
    if not n.startswith(("SELECT", "WITH")):
        return False, "solo se permiten consultas SELECT/WITH"
    for p in _BLOQUEADOS:
        if re.search(p, n):
            return False, f"operación bloqueada: {p}"
    return True, ""


def validar_procedimiento(nombre: str) -> None:
    if nombre.upper() not in PROCEDIMIENTOS:
        raise PermissionError(f"el procedimiento {nombre} no está permitido para OPS_DBA")
