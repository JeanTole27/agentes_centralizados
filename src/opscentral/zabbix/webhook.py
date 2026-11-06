"""Receptor del webhook de Zabbix (RF-ZBX-01).

Zabbix envía cada problema y recuperación con un tipo de medio propio
(deploy/zabbix/media_ops_central.js) que firma el cuerpo con HMAC-SHA256:

  X-OpsCentral-Tiempo: <unix>        se rechaza si difiere más de 5 min (repetición)
  X-OpsCentral-Firma:  sha256=<hex>  HMAC(secreto, "<tiempo>.<cuerpo>")
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone

TOLERANCIA_S = 300


class FirmaInvalida(PermissionError):
    pass


def firmar(secreto: str, tiempo: str, cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(secreto.encode(), tiempo.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()


def verificar(secreto: str, tiempo: str | None, firma: str | None, cuerpo: bytes, ahora: float | None = None) -> None:
    if not tiempo or not firma:
        raise FirmaInvalida("faltan las cabeceras de firma")
    try:
        t = int(tiempo)
    except ValueError:
        raise FirmaInvalida("tiempo inválido") from None
    if abs((ahora or time.time()) - t) > TOLERANCIA_S:
        raise FirmaInvalida("evento fuera de la ventana de tiempo permitida")
    if not hmac.compare_digest(firmar(secreto, tiempo, cuerpo), firma):
        raise FirmaInvalida("firma inválida")


@dataclass
class Evento:
    eventid: str
    host: str
    nombre: str
    severidad: int
    problema: bool                 # True = PROBLEM, False = RESOLVED
    inicio: datetime
    en_mantenimiento: bool
    etiquetas: dict
    crudo: dict


def normalizar(d: dict) -> Evento:
    """Acepta el JSON del tipo de medio. Los campos vienen de macros de Zabbix."""
    try:
        eventid = str(d.get("problem_eventid") or d["eventid"])
        valor = str(d.get("event_value", "1"))
        clock = int(d.get("clock") or time.time())
        sev = int(d.get("severity", 0))
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"evento de Zabbix incompleto: {e}") from None
    etiquetas = {}
    for t in d.get("tags") or []:
        if isinstance(t, dict) and "tag" in t:
            etiquetas[str(t["tag"])] = str(t.get("value", ""))
    return Evento(eventid=eventid, host=str(d.get("host", "")).strip(), nombre=str(d.get("name", ""))[:500],
                  severidad=max(0, min(sev, 5)), problema=valor == "1",
                  inicio=datetime.fromtimestamp(clock, timezone.utc),
                  en_mantenimiento=str(d.get("maintenance", "0")) in ("1", "true", "True"),
                  etiquetas=etiquetas, crudo=d)
