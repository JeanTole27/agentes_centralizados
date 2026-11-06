"""Protecciones para no tumbar las instancias (04 §3).

- Como máximo N sesiones simultáneas por base (1 por defecto) y M bases en paralelo (10).
- Circuit breaker por destino: tras K fallos seguidos no se vuelve a intentar durante T segundos.
- Refresco a demanda: como máximo 1 cada 2 min por objeto; los pedidos simultáneos se unen.

En PostgreSQL el límite por base se refuerza entre procesos con pg_try_advisory_lock.
"""
from __future__ import annotations

import threading
import time
import zlib
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select

from .almacen import Almacen, refresco, utc
from .cola import Cola


class CircuitoAbierto(RuntimeError):
    pass


class Ocupado(RuntimeError):
    pass


class Circuito:
    def __init__(self, fallos_max: int, espera_s: int):
        self.fallos_max, self.espera_s = fallos_max, espera_s
        self._fallos: dict[str, int] = {}
        self._abierto_hasta: dict[str, float] = {}
        self._lock = threading.Lock()

    def verificar(self, destino: str) -> None:
        with self._lock:
            hasta = self._abierto_hasta.get(destino, 0)
            if hasta > time.monotonic():
                raise CircuitoAbierto(f"{destino}: demasiados fallos seguidos, se reintenta en "
                                      f"{int(hasta - time.monotonic())} s")

    def exito(self, destino: str) -> None:
        with self._lock:
            self._fallos.pop(destino, None)
            self._abierto_hasta.pop(destino, None)

    def fallo(self, destino: str) -> None:
        with self._lock:
            n = self._fallos.get(destino, 0) + 1
            self._fallos[destino] = n
            if n >= self.fallos_max:
                self._abierto_hasta[destino] = time.monotonic() + self.espera_s

    def estado(self) -> dict:
        with self._lock:
            ahora = time.monotonic()
            return {d: {"fallos": self._fallos.get(d, 0), "abierto_s": max(0, int(h - ahora))}
                    for d, h in self._abierto_hasta.items()}


class Limites:
    def __init__(self, almacen: Almacen, *, por_destino: int, paralelo: int, fallos_max: int, espera_s: int):
        self.a = almacen
        self._global = threading.BoundedSemaphore(paralelo)
        self._por_destino: dict[str, threading.BoundedSemaphore] = {}
        self._n = por_destino
        self._lock = threading.Lock()
        self.circuito = Circuito(fallos_max, espera_s)

    def _sem(self, destino: str) -> threading.BoundedSemaphore:
        with self._lock:
            if destino not in self._por_destino:
                self._por_destino[destino] = threading.BoundedSemaphore(self._n)
            return self._por_destino[destino]

    @contextmanager
    def turno(self, destino: str, espera_s: float = 30):
        """Pide un turno para conectarse a un destino; falla rápido si el circuito está abierto."""
        self.circuito.verificar(destino)
        sem = self._sem(destino)
        if not self._global.acquire(timeout=espera_s):
            raise Ocupado("demasiadas consultas en paralelo")
        try:
            if not sem.acquire(timeout=espera_s):
                raise Ocupado(f"{destino}: ya hay una sesión de Ops Central en curso")
            try:
                with self._candado_pg(destino):
                    yield
                self.circuito.exito(destino)
            except (Ocupado, CircuitoAbierto):
                raise
            except Exception:
                self.circuito.fallo(destino)
                raise
            finally:
                sem.release()
        finally:
            self._global.release()

    @contextmanager
    def _candado_pg(self, destino: str):
        if not self.a.es_postgres:
            yield
            return
        clave = zlib.crc32(f"opsc:{destino}".encode())
        with self.a.engine.connect() as con:
            if not con.exec_driver_sql("SELECT pg_try_advisory_lock(%s)", (clave,)).scalar():
                raise Ocupado(f"{destino}: otro worker ya tiene una sesión abierta")
            try:
                yield
            finally:
                con.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (clave,))


def pedir_refresco(almacen: Almacen, cola: Cola, objeto: str, *, minimo_s: int, cola_destino: str,
                   payload: dict) -> dict:
    """com.refresco: devuelve si se encoló, se unió a uno en curso o es demasiado pronto."""
    ahora = utc()
    with almacen.tx() as con:
        f = con.execute(select(refresco).where(refresco.c.objeto == objeto)).first()
        if f:
            ultimo = f.ultimo if f.ultimo.tzinfo else f.ultimo.replace(tzinfo=ahora.tzinfo)
            faltan = minimo_s - (ahora - ultimo).total_seconds()
            if faltan > 0:
                return {"estado": "reciente", "reintentar_en_s": int(faltan) + 1, "trabajo_id": f.trabajo_id}
        tid = cola.encolar(cola_destino, "refrescar", {**payload, "objeto": objeto},
                           clave_unica=f"refresco:{objeto}", con=con)
        if f:
            con.execute(refresco.update().where(refresco.c.objeto == objeto).values(ultimo=ahora, trabajo_id=tid))
        else:
            con.execute(refresco.insert().values(objeto=objeto, ultimo=ahora, trabajo_id=tid))
    return {"estado": "encolado", "trabajo_id": tid, "proximo_permitido": (ahora + timedelta(seconds=minimo_s)).isoformat()}
