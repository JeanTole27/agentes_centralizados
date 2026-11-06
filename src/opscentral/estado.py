"""Estado actual de cada objeto: lo único que lee la interfaz (04 §2).

Cada fila dice de dónde salió el dato y cuándo, para mostrar su antigüedad.
"""
from __future__ import annotations

from sqlalchemy import select

from .almacen import Almacen, estado_actual, utc


def guardar(almacen: Almacen, objeto: str, tipo: str, datos, *, origen: str, error: str | None = None) -> None:
    with almacen.tx() as con:
        valores = dict(tipo=tipo, datos=datos, origen=origen, actualizado=utc(), error=error)
        r = con.execute(estado_actual.update().where(estado_actual.c.objeto == objeto).values(**valores))
        if r.rowcount == 0:
            con.execute(estado_actual.insert().values(objeto=objeto, **valores))


def marcar_error(almacen: Almacen, objeto: str, tipo: str, error: str, *, origen: str) -> None:
    """Conserva el último dato bueno y anota el error (la interfaz muestra 'sin dato reciente')."""
    with almacen.tx() as con:
        r = con.execute(estado_actual.update().where(estado_actual.c.objeto == objeto).values(error=error[:2000]))
        if r.rowcount == 0:
            con.execute(estado_actual.insert().values(objeto=objeto, tipo=tipo, datos={}, origen=origen,
                                                      actualizado=utc(), error=error[:2000]))


def leer(almacen: Almacen, objeto: str) -> dict | None:
    with almacen.tx() as con:
        f = con.execute(select(estado_actual).where(estado_actual.c.objeto == objeto)).first()
    return _con_antiguedad(dict(f._mapping)) if f else None


def leer_tipo(almacen: Almacen, tipo: str, prefijo: str | None = None) -> list[dict]:
    q = select(estado_actual).where(estado_actual.c.tipo == tipo)
    if prefijo:
        q = q.where(estado_actual.c.objeto.like(prefijo + "%"))
    with almacen.tx() as con:
        return [_con_antiguedad(dict(f._mapping)) for f in con.execute(q.order_by(estado_actual.c.objeto))]


def _con_antiguedad(f: dict) -> dict:
    act = f["actualizado"]
    if act.tzinfo is None:
        act = act.replace(tzinfo=utc().tzinfo)
    f["antiguedad_s"] = int((utc() - act).total_seconds())
    return f
