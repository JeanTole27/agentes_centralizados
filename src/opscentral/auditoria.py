"""Auditoría de solo agregar con cadena de hash (RF-AUD).

Cada registro guarda el hash del anterior. Si alguien borra o edita una fila,
`verificar()` encuentra el punto exacto donde se rompe la cadena. En producción
la tabla además se replica al SIEM y el usuario de la aplicación no tiene
UPDATE/DELETE sobre ella (db/postgres/002_permisos.sql).
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import timezone

from sqlalchemy import select

from .almacen import Almacen, auditoria, utc

GENESIS = "0" * 64
_SENSIBLES = ("clave", "password", "token", "secreto", "secret")


def _limpiar(d):
    if isinstance(d, dict):
        return {k: ("***" if any(s in k.lower() for s in _SENSIBLES) else _limpiar(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [_limpiar(x) for x in d]
    return d


def _hash(prev: str, ts: str, evento: str, actor: str, datos: dict) -> str:
    canon = json.dumps({"p": prev, "t": ts, "e": evento, "a": actor, "d": datos},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


class Auditoria:
    def __init__(self, almacen: Almacen):
        self.a = almacen
        self._lock = threading.Lock()

    def registrar(self, evento: str, actor: str, **datos) -> str:
        datos = _limpiar(datos)
        with self._lock, self.a.tx() as con:
            if self.a.es_postgres:     # serializa escritores entre procesos
                con.exec_driver_sql("SELECT pg_advisory_xact_lock(4242)")
            fila = con.execute(select(auditoria.c.hash).order_by(auditoria.c.id.desc()).limit(1)).first()
            prev = fila[0] if fila else GENESIS
            ts = utc()
            # se normaliza a ISO con microsegundos para que la verificación sea estable
            ts_iso = ts.isoformat(timespec="microseconds")
            h = _hash(prev, ts_iso, evento, actor, datos)
            con.execute(auditoria.insert().values(ts=ts, evento=evento, actor=actor, datos=datos, hash_prev=prev, hash=h))
            return h

    def verificar(self) -> tuple[bool, int, str]:
        """(íntegra, filas revisadas, mensaje)."""
        prev = GENESIS
        n = 0
        with self.a.tx() as con:
            for f in con.execute(select(auditoria).order_by(auditoria.c.id)):
                n += 1
                ts = utc(f.ts if f.ts.tzinfo else f.ts.replace(tzinfo=timezone.utc))
                esperado = _hash(prev, ts.isoformat(timespec="microseconds"), f.evento, f.actor, f.datos)
                if f.hash_prev != prev or f.hash != esperado:
                    return False, n, f"cadena rota en el registro {f.id} ({f.evento})"
                prev = f.hash
        return True, n, "cadena íntegra"
