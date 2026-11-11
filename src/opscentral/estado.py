"""Estado actual de cada objeto: lo único que lee la interfaz (04 §2).

Cada fila dice de dónde salió el dato y cuándo, para mostrar su antigüedad:
- `actualizado`: cuándo se guardó el último dato bueno (si nunca lo hubo, el primer intento);
- `intentado`: el último intento de recolección, exitoso o no (para ver que el sistema sigue reintentando);
- `error`: el error del último intento (vacío si fue bueno).
"""
from __future__ import annotations

from sqlalchemy import select

from .almacen import Almacen, estado_actual, utc


def guardar(almacen: Almacen, objeto: str, tipo: str, datos, *, origen: str, error: str | None = None) -> None:
    ahora = utc()
    with almacen.tx() as con:
        valores = dict(tipo=tipo, datos=datos, origen=origen, actualizado=ahora, intentado=ahora, error=error)
        r = con.execute(estado_actual.update().where(estado_actual.c.objeto == objeto).values(**valores))
        if r.rowcount == 0:
            con.execute(estado_actual.insert().values(objeto=objeto, **valores))


def marcar_error(almacen: Almacen, objeto: str, tipo: str, error: str, *, origen: str) -> None:
    """Conserva el último dato bueno (y su fecha en `actualizado`), anota el error y la hora del intento."""
    ahora = utc()
    with almacen.tx() as con:
        r = con.execute(estado_actual.update().where(estado_actual.c.objeto == objeto)
                        .values(error=error[:2000], intentado=ahora, origen=origen))
        if r.rowcount == 0:
            con.execute(estado_actual.insert().values(objeto=objeto, tipo=tipo, datos={}, origen=origen,
                                                      actualizado=ahora, intentado=ahora, error=error[:2000]))


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


def _seg(ts) -> int | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=utc().tzinfo)
    return int((utc() - ts).total_seconds())


def _con_antiguedad(f: dict) -> dict:
    f["antiguedad_s"] = _seg(f["actualizado"])
    f["intento_s"] = _seg(f.get("intentado")) if f.get("intentado") is not None else f["antiguedad_s"]
    # ¿Hay un dato bueno guardado? (las filas que solo registraron errores tienen datos = {})
    f["con_dato"] = bool(f.get("datos"))
    return f
