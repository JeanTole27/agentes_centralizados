"""Sesiones de servidor (RF-AUT-04).

- Cookie opaca HttpOnly + Secure + SameSite=Strict; en el almacén solo va su SHA-256.
- Vence a los 30 min sin actividad o a las 8 h.
- Con LDAPS: bloqueo de 15 min tras 5 intentos fallidos.
- El perfil se recalcula en cada ingreso (no se guarda un perfil elegido).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from .almacen import Almacen, intento_ingreso, sesion, utc
from .auditoria import Auditoria
from .identidad import Usuario, huella_sesion, nuevo_id_sesion


def _aware(dt):
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=utc().tzinfo))


class Sesiones:
    def __init__(self, almacen: Almacen, auditoria: Auditoria, cfg):
        self.a, self.aud = almacen, auditoria
        self.inactividad = timedelta(minutes=cfg["auth"]["inactividad_min"])
        self.maximo = timedelta(hours=cfg["auth"]["duracion_max_h"])
        self.intentos_max = cfg["auth"]["intentos_max"]
        self.bloqueo = timedelta(minutes=cfg["auth"]["bloqueo_min"])

    def crear(self, u: Usuario, metodo: str) -> str:
        sid, h = nuevo_id_sesion()
        t = utc()
        with self.a.tx() as con:
            con.execute(sesion.insert().values(huella=h, login=u.login, nombre=u.nombre, perfiles=sorted(u.perfiles),
                                               grupos=u.grupos, ip=u.ip, creada=t, ultima_actividad=t,
                                               vence=t + self.maximo, mfa_en=u.mfa_en, cerrada=False))
            con.execute(intento_ingreso.delete().where(intento_ingreso.c.login == u.login))
        self.aud.registrar("ingreso", u.login, metodo=metodo, perfiles=sorted(u.perfiles), ip=u.ip,
                           mfa=bool(u.mfa_en))
        return sid

    def obtener(self, sid: str | None) -> Usuario | None:
        if not sid:
            return None
        h = huella_sesion(sid)
        t = utc()
        with self.a.tx() as con:
            f = con.execute(select(sesion).where(sesion.c.huella == h)).first()
            if not f or f.cerrada:
                return None
            if t > _aware(f.vence) or t - _aware(f.ultima_actividad) > self.inactividad:
                con.execute(update(sesion).where(sesion.c.huella == h).values(cerrada=True))
                return None
            con.execute(update(sesion).where(sesion.c.huella == h).values(ultima_actividad=t))
        return Usuario(login=f.login, nombre=f.nombre, grupos=list(f.grupos), perfiles=frozenset(f.perfiles),
                       mfa_en=_aware(f.mfa_en), ip=f.ip or "")

    def registrar_mfa(self, sid: str, login: str) -> None:
        with self.a.tx() as con:
            con.execute(update(sesion).where(sesion.c.huella == huella_sesion(sid)).values(mfa_en=utc()))
        self.aud.registrar("segundo_factor", login)

    def cerrar(self, sid: str | None, login: str = "") -> None:
        if not sid:
            return
        with self.a.tx() as con:
            con.execute(update(sesion).where(sesion.c.huella == huella_sesion(sid)).values(cerrada=True))
        self.aud.registrar("salida", login or "desconocido")

    # ---------------------------------------------------------------- intentos (solo LDAPS)
    def bloqueado(self, login: str) -> bool:
        with self.a.tx() as con:
            f = con.execute(select(intento_ingreso).where(intento_ingreso.c.login == login.lower())).first()
        return bool(f and f.bloqueado_hasta and _aware(f.bloqueado_hasta) > utc())

    def fallo(self, login: str, ip: str) -> None:
        login = login.lower()
        with self.a.tx() as con:
            f = con.execute(select(intento_ingreso).where(intento_ingreso.c.login == login)).first()
            n = (f.fallidos if f else 0) + 1
            hasta = utc() + self.bloqueo if n >= self.intentos_max else None
            if f:
                con.execute(update(intento_ingreso).where(intento_ingreso.c.login == login)
                            .values(fallidos=0 if hasta else n, bloqueado_hasta=hasta))
            else:
                con.execute(intento_ingreso.insert().values(login=login, fallidos=n, bloqueado_hasta=hasta))
        self.aud.registrar("ingreso_fallido", login, ip=ip, bloqueado=bool(hasta))
