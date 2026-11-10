"""Catálogo de acciones: una sola fuente para interfaz, API y workers (05 §4.5).

Cada acción declara su clase de riesgo, los perfiles que la ejecutan o la ven,
su motor y el esquema de parámetros. Nada se ejecuta si no está aquí.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import RAIZ

CLASES = {"L", "O", "C", "X", "N", "V"}
PERMISOS = {"ejecuta", "ve"}
AMBIENTES_PROTEGIDOS = {"PROD", "DRP"}

RE_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
RE_SQL_ID = re.compile(r"^[0-9a-z]{13}$")
# Referencia del caso: opcional y de formato libre (clave de Jira, incidente, cambio, requerimiento,
# tarea, despliegue...). Mismo conjunto de caracteres que acepta cmops (3 a 40).
RE_REFERENCIA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/.]{2,39}$")
RE_CAMBIO = RE_REFERENCIA          # nombre anterior, se conserva por compatibilidad
RE_BASE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}$")
TIPOS_OBJETO = {"PACKAGE", "PACKAGE BODY", "TYPE", "TYPE BODY", "PROCEDURE", "FUNCTION", "TRIGGER",
                "VIEW", "SYNONYM", "MATERIALIZED VIEW"}


class ErrorValidacion(ValueError):
    """Parámetros que no cumplen el esquema del catálogo."""


@dataclass(frozen=True)
class Accion:
    id: str
    nombre: str
    motor: str
    clase: str
    perfiles: dict[str, str]
    estado: str = "catalogada"
    clase_prod: str | None = None
    parametros: dict[str, dict] = field(default_factory=dict)
    parametros_libres: bool = False
    plantilla: str | None = None
    procedimiento: str | None = None
    cuenta_bd: str | None = None
    sistema: bool = False
    bloqueo_impl: str | None = None
    nota: str | None = None

    def clase_en(self, ambiente: str | None) -> str:
        """Clase efectiva: algunas acciones son O fuera de PROD y C en PROD/DRP."""
        if self.clase_prod and (ambiente or "").upper() in AMBIENTES_PROTEGIDOS:
            return self.clase_prod
        return self.clase

    @property
    def es_cambio(self) -> bool:
        return self.clase in ("O", "C") or self.clase_prod in ("O", "C")

    def validar_parametros(self, params: dict[str, Any]) -> dict[str, Any]:
        """Devuelve los parámetros normalizados o lanza ErrorValidacion."""
        params = dict(params or {})
        desconocidos = set(params) - set(self.parametros)
        if desconocidos and not self.parametros_libres:
            raise ErrorValidacion(f"parámetros no permitidos para {self.id}: {', '.join(sorted(desconocidos))}")
        salida: dict[str, Any] = {}
        for nombre, esp in self.parametros.items():
            if nombre not in params or params[nombre] in (None, ""):
                if esp.get("requerido"):
                    raise ErrorValidacion(f"falta el parámetro '{nombre}'")
                if "defecto" in esp:
                    salida[nombre] = esp["defecto"]
                continue
            salida[nombre] = _validar_valor(nombre, esp, params[nombre])
        if self.parametros_libres:
            for k, v in params.items():
                if k not in salida:
                    if not re.match(r"^[a-z_][a-z0-9_]{0,40}$", k):
                        raise ErrorValidacion(f"nombre de parámetro inválido: {k}")
                    salida[k] = v
        _sin_secretos(salida)
        return salida


_NOMBRES_SECRETOS = ("password", "clave", "contrasena", "contraseña", "secret", "token")


def _sin_secretos(v, ruta: str = "") -> None:
    """Los parámetros se guardan y van a la evidencia: nunca pueden traer claves.
    Las credenciales las pone el worker desde el gestor de secretos."""
    if isinstance(v, dict):
        for k, x in v.items():
            if any(s in str(k).lower() for s in _NOMBRES_SECRETOS):
                raise ErrorValidacion(f"el parámetro '{ruta}{k}' parece una credencial: no se aceptan claves en "
                                      "los parámetros (las pone el gestor de secretos)")
            _sin_secretos(x, f"{ruta}{k}.")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _sin_secretos(x, f"{ruta}{i}.")


def _validar_valor(nombre: str, esp: dict, valor: Any) -> Any:
    tipo = esp.get("tipo", "texto")
    if tipo == "entero":
        try:
            v = int(valor)
        except (TypeError, ValueError):
            raise ErrorValidacion(f"'{nombre}' debe ser entero") from None
        if "min" in esp and v < esp["min"] or "max" in esp and v > esp["max"]:
            raise ErrorValidacion(f"'{nombre}' fuera de rango [{esp.get('min')}, {esp.get('max')}]")
        return v
    if tipo == "booleano":
        if isinstance(valor, bool):
            return valor
        s = str(valor).strip().lower()
        if s in ("1", "true", "si", "sí", "s", "yes"):
            return True
        if s in ("0", "false", "no", "n"):
            return False
        raise ErrorValidacion(f"'{nombre}' debe ser verdadero o falso")
    s = str(valor).strip()
    if tipo == "enum":
        s = s.upper()
        if s not in esp["valores"]:
            raise ErrorValidacion(f"'{nombre}' debe ser uno de {esp['valores']}")
        return s
    if tipo == "identificador":
        if not RE_IDENT.match(s):
            raise ErrorValidacion(f"'{nombre}' no es un identificador Oracle válido")
        return s.upper()
    if tipo == "base":
        if not RE_BASE.match(s):
            raise ErrorValidacion(f"'{nombre}' no es un nombre de base válido")
        return s.upper()
    if tipo == "sql_id":
        if not RE_SQL_ID.match(s):
            raise ErrorValidacion(f"'{nombre}' no es un SQL_ID válido")
        return s
    if tipo == "cambio":
        if not RE_CAMBIO.match(s):
            raise ErrorValidacion(f"'{nombre}' no es una referencia válida (3 a 40 caracteres: letras, números, "
                                  "guion, guion bajo, punto o barra)")
        return s.upper()
    if tipo == "tipo_objeto":
        s = s.upper()
        if s not in TIPOS_OBJETO:
            raise ErrorValidacion(f"'{nombre}' no es un tipo de objeto soportado")
        return s
    # texto
    if len(s) < esp.get("min", 0):
        raise ErrorValidacion(f"'{nombre}' debe tener al menos {esp['min']} caracteres")
    if len(s) > esp.get("max_largo", 2000):
        raise ErrorValidacion(f"'{nombre}' es demasiado largo")
    if any(ord(c) < 32 and c not in "\t" for c in s):
        raise ErrorValidacion(f"'{nombre}' contiene caracteres de control")
    return s


class Catalogo:
    def __init__(self, acciones: dict[str, Accion]):
        self._acciones = acciones

    @classmethod
    def cargar(cls, ruta: str | Path | None = None) -> "Catalogo":
        ruta = Path(ruta) if ruta else RAIZ / "catalogo" / "acciones.yaml"
        with open(ruta, encoding="utf-8") as f:
            datos = yaml.safe_load(f)
        acciones: dict[str, Accion] = {}
        campos = set(Accion.__dataclass_fields__)
        for a in datos["acciones"]:
            extra = set(a) - campos
            if extra:
                raise ValueError(f"campos desconocidos en {a.get('id')}: {extra}")
            acc = Accion(**a)
            if acc.clase not in CLASES or (acc.clase_prod and acc.clase_prod not in CLASES):
                raise ValueError(f"clase inválida en {acc.id}")
            if set(acc.perfiles.values()) - PERMISOS:
                raise ValueError(f"permiso inválido en {acc.id}")
            if acc.id in acciones:
                raise ValueError(f"acción duplicada: {acc.id}")
            if acc.motor == "bd" and acc.es_cambio and not acc.procedimiento and acc.estado == "implementada":
                raise ValueError(f"{acc.id}: un cambio en BD debe usar un procedimiento del paquete")
            acciones[acc.id] = acc
        return cls(acciones)

    def __getitem__(self, aid: str) -> Accion:
        try:
            return self._acciones[aid]
        except KeyError:
            raise ErrorValidacion(f"acción desconocida: {aid}") from None

    def __contains__(self, aid: str) -> bool:
        return aid in self._acciones

    def __iter__(self):
        return iter(self._acciones.values())

    def __len__(self) -> int:
        return len(self._acciones)
