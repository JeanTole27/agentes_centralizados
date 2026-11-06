"""Agrupación de eventos en episodios por reglas (M4 versión 1, RF-ML-04).

Regla: dos eventos van al mismo episodio si sus activos están a ≤ 2 saltos en el
grafo servidor ↔ dominio ↔ base y llegan con menos de 15 min de diferencia.
Causa probable: la base si alertan base y dominios que dependen de ella; si no,
el servidor; si no, el primer activo que alertó.
"""
from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import and_, select, update

from ..almacen import episodio, evento_zbx, utc
from ..inventario import Inventario
from .webhook import Evento

PRIORIDAD = {"base": 0, "servidor": 1, "dominio": 2}
SEVERIDAD = ["sin clasificar", "información", "advertencia", "promedio", "alta", "desastre"]


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=utc().tzinfo)


def registrar(con, ev: Evento, inv: Inventario, ventana_min: int = 15) -> dict:
    """Guarda el evento y lo asocia a un episodio. Devuelve {evento, episodio, nuevo, activo}."""
    activo = inv.por_zabbix(ev.host)
    clave = activo.clave if activo else None
    existente = con.execute(select(evento_zbx).where(evento_zbx.c.eventid == ev.eventid)).first()

    if not ev.problema:   # recuperación
        if existente:
            con.execute(update(evento_zbx).where(evento_zbx.c.eventid == ev.eventid)
                        .values(estado="RESOLVED", fin=ev.inicio))
            _cerrar_si_resuelto(con, existente.episodio_id)
            return {"evento": ev.eventid, "episodio": existente.episodio_id, "nuevo": False, "activo": clave,
                    "recuperacion": True}
        return {"evento": ev.eventid, "episodio": None, "nuevo": False, "activo": clave, "recuperacion": True}

    if existente:          # reenvío del mismo evento: idempotente
        return {"evento": ev.eventid, "episodio": existente.episodio_id, "nuevo": False, "activo": clave}

    ep_id, nuevo = None, False
    if clave and not ev.en_mantenimiento:
        cercanos = inv.relacionados(clave, saltos=2)
        desde = ev.inicio - timedelta(minutes=ventana_min)
        for ep in con.execute(select(episodio).where(and_(episodio.c.estado == "abierto",
                                                          episodio.c.ultimo_evento >= desde))
                              .order_by(episodio.c.ultimo_evento.desc())):
            if set(ep.activos) & cercanos:
                ep_id = ep.id
                activos = sorted(set(ep.activos) | {clave})
                raiz = causa_probable(activos)
                con.execute(update(episodio).where(episodio.c.id == ep.id).values(
                    activos=activos, ultimo_evento=max(_aware(ep.ultimo_evento), ev.inicio),
                    severidad=max(ep.severidad, ev.severidad), activo_raiz=raiz,
                    causa_probable=f"Varias alertas relacionadas con {raiz}"))
                break
    if ep_id is None and not ev.en_mantenimiento:
        ep_id, nuevo = os.urandom(8).hex(), True
        con.execute(episodio.insert().values(
            id=ep_id, abierto=ev.inicio, ultimo_evento=ev.inicio, activo_raiz=clave or ev.host,
            activos=[clave] if clave else [], titulo=f"{ev.nombre} ({ev.host})", severidad=ev.severidad,
            estado="abierto", causa_probable=None))
    con.execute(evento_zbx.insert().values(
        eventid=ev.eventid, host=ev.host, activo=activo.id if activo else None,
        tipo_activo=activo.tipo if activo else None, severidad=ev.severidad, nombre=ev.nombre, estado="PROBLEM",
        inicio=ev.inicio, episodio_id=ep_id, en_mantenimiento=ev.en_mantenimiento, datos=ev.crudo))
    return {"evento": ev.eventid, "episodio": ep_id, "nuevo": nuevo, "activo": clave,
            "en_mantenimiento": ev.en_mantenimiento}


def causa_probable(activos: list[str]) -> str:
    return sorted(activos, key=lambda c: (PRIORIDAD.get(c.split(":")[0], 9), c))[0]


def _cerrar_si_resuelto(con, ep_id: str | None) -> None:
    if not ep_id:
        return
    abiertos = con.execute(select(evento_zbx.c.eventid).where(and_(
        evento_zbx.c.episodio_id == ep_id, evento_zbx.c.estado == "PROBLEM"))).first()
    if not abiertos:
        con.execute(update(episodio).where(episodio.c.id == ep_id).values(estado="cerrado", cerrado=utc()))
