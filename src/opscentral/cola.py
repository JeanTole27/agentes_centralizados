"""Cola de trabajos sobre el mismo almacén (sin Redis ni RabbitMQ).

En PostgreSQL usa SELECT ... FOR UPDATE SKIP LOCKED, así varios workers toman
trabajos sin pisarse. En SQLite (desarrollo) la transacción es serial.
`clave_unica` une pedidos repetidos: si ya hay uno pendiente igual, no se duplica.
Esta interfaz es la misma que ofrece procrastinate; se puede cambiar sin tocar los workers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, select, update

from .almacen import Almacen, trabajo, utc

COLAS = ("bd", "cm", "ingesta", "ml", "portal")


@dataclass
class Trabajo:
    id: int
    cola: str
    tipo: str
    payload: dict
    intentos: int
    max_intentos: int


class Cola:
    def __init__(self, almacen: Almacen):
        self.a = almacen

    def encolar(self, cola: str, tipo: str, payload: dict, *, clave_unica: str | None = None,
                max_intentos: int = 1, en_segundos: int = 0, con=None) -> int:
        if cola not in COLAS:
            raise ValueError(f"cola desconocida: {cola}")

        def _hacer(c):
            if clave_unica:
                ya = c.execute(select(trabajo.c.id).where(and_(trabajo.c.clave_unica == clave_unica,
                                                               trabajo.c.estado.in_(("pendiente", "tomado"))))).first()
                if ya:
                    return ya[0]
            r = c.execute(trabajo.insert().values(
                cola=cola, tipo=tipo, payload=payload, estado="pendiente", intentos=0, max_intentos=max_intentos,
                ejecutar_desde=utc() + timedelta(seconds=en_segundos), clave_unica=clave_unica, creado=utc()))
            return r.inserted_primary_key[0]

        if con is not None:
            return _hacer(con)
        with self.a.tx() as c:
            return _hacer(c)

    def tomar(self, cola: str, worker: str) -> Trabajo | None:
        with self.a.tx() as c:
            q = (select(trabajo).where(and_(trabajo.c.cola == cola, trabajo.c.estado == "pendiente",
                                            trabajo.c.ejecutar_desde <= utc()))
                 .order_by(trabajo.c.id).limit(1))
            if self.a.es_postgres:
                q = q.with_for_update(skip_locked=True)
            f = c.execute(q).first()
            if not f:
                return None
            r = c.execute(update(trabajo).where(and_(trabajo.c.id == f.id, trabajo.c.estado == "pendiente"))
                          .values(estado="tomado", tomado_por=worker, tomado_en=utc(), intentos=f.intentos + 1))
            if r.rowcount != 1:
                return None
            return Trabajo(f.id, f.cola, f.tipo, f.payload, f.intentos + 1, f.max_intentos)

    def completar(self, tid: int) -> None:
        with self.a.tx() as c:
            c.execute(update(trabajo).where(trabajo.c.id == tid).values(estado="hecho", error=None))

    def fallar(self, t: Trabajo, error: str) -> bool:
        """Devuelve True si se reintentará (backoff 30 s, 2 min, 8 min...)."""
        reintenta = t.intentos < t.max_intentos
        with self.a.tx() as c:
            if reintenta:
                c.execute(update(trabajo).where(trabajo.c.id == t.id).values(
                    estado="pendiente", error=error[:4000],
                    ejecutar_desde=utc() + timedelta(seconds=30 * (4 ** (t.intentos - 1)))))
            else:
                c.execute(update(trabajo).where(trabajo.c.id == t.id).values(estado="fallido", error=error[:4000]))
        return reintenta

    def liberar_huerfanos(self, minutos: int = 90) -> int:
        """Trabajos 'tomados' por un worker que murió vuelven a la cola."""
        with self.a.tx() as c:
            r = c.execute(update(trabajo).where(and_(trabajo.c.estado == "tomado",
                                                     trabajo.c.tomado_en < utc() - timedelta(minutes=minutos)))
                          .values(estado="pendiente"))
            return r.rowcount
