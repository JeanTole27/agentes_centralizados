"""Reportes HTML de BD generados por script (sin LLM), con el formato de las skills.

- hoja_vida.py   → copia de generar_hoja_vida.py del paquete portable (spec v2.0).
- analisis.py    → reporte-analisis-bd (plan de acción, rankings, alert_registry).
- diskgroups.py  → reporte-diskgroups (por ambiente, agrupado por servidor, RAC deduplicado).
- sentencias.py  → reporte-sentencias-costosas (origen por sql_id, panel expandible).

Cada módulo también corre solo (python -m opscentral.bd.reportes.analisis --alias X)
y tiene su copia en el paquete portable. Dentro de Ops Central se les pasa un
ClienteOps: la conexión OPS_CONSULTA del motor BD, que valida cada SQL con la
política de solo lectura antes de ejecutarlo.
"""
from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_MODULOS_SQL = ("hoja_vida", "analisis", "diskgroups", "sentencias")


def _normalizar(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").upper()


@lru_cache(maxsize=1)
def plantillas_fijas() -> frozenset[str]:
    """Todo el SQL que los scripts de reporte pueden mandar: los literales SELECT/WITH escritos en
    su código fuente y los diccionarios SQL de cada módulo. Ninguna consulta lleva texto del
    usuario (los valores van como binds), así que esta lista es cerrada y se revisa leyendo el código."""
    aceptadas = set()
    for m in _MODULOS_SQL:
        arbol = ast.parse((Path(__file__).parent / f"{m}.py").read_text(encoding="utf-8"))
        for n in ast.walk(arbol):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and re.match(r"\s*(SELECT|WITH)\b", n.value, re.I):
                aceptadas.add(_normalizar(n.value))
    # sentencias arma top_CPU/ELAPSED/GETS al importar, con una plantilla constante y columnas fijas
    from . import analisis, diskgroups, sentencias
    for mod in (analisis, sentencias):
        aceptadas |= {_normalizar(v) for v in mod.SQL.values()}
    aceptadas.add(_normalizar(diskgroups.SQL_DG))
    return frozenset(aceptadas)


def es_plantilla_fija(sql: str) -> bool:
    return _normalizar(sql) in plantillas_fijas()


class ClienteOps:
    """Adapta ConexionOracle a la interfaz que usan los scripts (query / query_one)."""

    def __init__(self, conexion):
        self.c = conexion

    def query(self, sql: str, params: dict | None = None, max_rows: int = 500) -> list[dict[str, Any]]:
        return self.c.consultar_sql(sql, params or {}, max_filas=max_rows)

    def query_one(self, sql: str, params: dict | None = None) -> dict[str, Any] | None:
        filas = self.query(sql, params, max_rows=1)
        return filas[0] if filas else None

    def close(self) -> None:  # la conexión la cierra el motor
        pass


# acción del catálogo → (módulo, etiqueta)
REPORTES = {
    "bd.rep.hoja_vida": ("hoja_vida", "Hoja de vida"),
    "bd.rep.analisis": ("analisis", "Análisis y plan de acción"),
    "bd.rep.diskgroups": ("diskgroups", "Diskgroups ASM"),
    "bd.rep.sql_costosas": ("sentencias", "Sentencias costosas"),
}

# Lo que muestra la pantalla de Reportes (textos del diseño, demo-ops-central.html). Las etapas
# salen de cada script (ETAPAS), las mismas que emite en las líneas "Etapa i/n: …".
FICHAS = {
    "bd.rep.hoja_vida": ("Hoja de vida de BD", "hoja-de-vida-bd",
                         "Inventario y estado completo de una base, por categorías.", "base"),
    "bd.rep.analisis": ("Análisis y plan de acción", "reporte-analisis-bd",
                        "Rankings Top-N y veredictos: qué atender primero.", "base"),
    "bd.rep.diskgroups": ("Diskgroups ASM", "reporte-diskgroups",
                          "Llenado de diskgroups por servidor y ambiente.", "ambiente"),
    "bd.rep.sql_costosas": ("Sentencias costosas", "reporte-sentencias-costosas",
                            "CPU, elapsed y buffer gets con detalle por sql_id.", "base"),
}


def tipos() -> list[dict]:
    from importlib import import_module
    out = []
    for accion, (modulo, _etq) in REPORTES.items():
        nombre, skill, descripcion, alcance = FICHAS[accion]
        out.append({"accion": accion, "nombre": nombre, "skill": skill, "descripcion": descripcion,
                    "alcance": alcance, "script": f"{modulo}.py",
                    "etapas": list(import_module(f"{__name__}.{modulo}").ETAPAS)})
    return out
