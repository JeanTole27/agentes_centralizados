"""Identidad de las personas: perfiles por grupo de AD y sesiones.

- Con SSO, el perfil NO se elige: se calcula desde los grupos GG_OPS_* en cada ingreso.
- En la demo (auth.proveedor = "demo") no hay autenticación construida: la persona se registra
  con su nombre y elige el perfil (Capa Media, DBA, Administrador o Automatizador).
- Quien está en varios grupos recibe la unión de permisos.
- La sesión vive en el servidor; el navegador solo lleva un identificador opaco.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

PERFILES = ("dba", "cm", "lid", "con", "adm", "aut")
NOMBRES_PERFIL = {"dba": "Administrador de bases de datos (DBA)", "cm": "Especialista Capa Media",
                  "lid": "Líder de turno", "con": "Consulta", "adm": "Administrador",
                  "aut": "Automatizador"}
# Perfiles que se pueden elegir al registrarse en la demo
PERFILES_DEMO = ("cm", "dba", "adm", "aut")
# El automatizador tiene permiso de ejecución y edición sobre todo el catálogo
PERFIL_TOTAL = "aut"


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class SinPerfil(PermissionError):
    """El usuario no pertenece a ningún grupo GG_OPS_*."""


@dataclass
class Usuario:
    login: str
    nombre: str
    grupos: list[str]
    perfiles: frozenset[str]
    correo: str = ""
    mfa_en: datetime | None = None          # última vez que se validó un segundo factor
    ip: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def equipo(self) -> str | None:
        if "dba" in self.perfiles:
            return "dba"
        if "cm" in self.perfiles:
            return "cm"
        return None

    @property
    def total(self) -> bool:
        """Automatizador: ejecuta y edita todo."""
        return PERFIL_TOTAL in self.perfiles

    def mfa_reciente(self, vigencia_min: int) -> bool:
        return bool(self.mfa_en) and ahora() - self.mfa_en <= timedelta(minutes=vigencia_min)


def perfiles_desde_grupos(grupos: list[str], mapa: dict[str, str]) -> frozenset[str]:
    """Unión de perfiles. Los grupos se comparan sin distinguir mayúsculas y aceptan DN completos."""
    norm = {k.upper(): v for k, v in mapa.items()}
    perfiles = set()
    for g in grupos or []:
        g = g.strip()
        cn = g.split(",")[0].removeprefix("CN=").removeprefix("cn=") if g.upper().startswith("CN=") else g
        p = norm.get(cn.upper())
        if p in PERFILES:
            perfiles.add(p)
    return frozenset(perfiles)


def construir_usuario(login: str, nombre: str, grupos: list[str], mapa: dict[str, str], *,
                      correo: str = "", mfa: bool = False, ip: str = "") -> Usuario:
    perfiles = perfiles_desde_grupos(grupos, mapa)
    if not perfiles:
        raise SinPerfil(f"el usuario {login} no pertenece a ningún grupo de Ops Central")
    return Usuario(login=login.lower(), nombre=nombre, grupos=list(grupos), perfiles=perfiles,
                   correo=correo, mfa_en=ahora() if mfa else None, ip=ip)


def nuevo_id_sesion() -> tuple[str, str]:
    """Devuelve (id para la cookie, huella que se guarda). El almacén nunca guarda el id en claro."""
    sid = secrets.token_urlsafe(32)
    return sid, huella_sesion(sid)


def huella_sesion(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


# --------------------------------------------------------------------------- proveedor de desarrollo
# Usuarios de la demo (01 §3). Solo existe con auth.proveedor = "dev" y modo desarrollo.
USUARIOS_DEV = {
    "dba.demo": ("María DBA (demo)", ["GG_OPS_DBA"]),
    "cm.demo": ("Carlos Capa Media (demo)", ["GG_OPS_CM"]),
    "lider.demo": ("Laura Líder (demo)", ["GG_OPS_LIDER"]),
    "consulta.demo": ("Camilo Consulta (demo)", ["GG_OPS_CONSULTA"]),
    "admin.demo": ("Ana Admin (demo)", ["GG_OPS_ADMIN"]),
    "dba2.demo": ("Diego DBA 2 (demo)", ["GG_OPS_DBA"]),
    "cm2.demo": ("Clara Capa Media 2 (demo)", ["GG_OPS_CM"]),
}


def ingreso_dev(login: str, mapa: dict[str, str], ip: str = "") -> Usuario:
    if login not in USUARIOS_DEV:
        raise SinPerfil("usuario de desarrollo desconocido")
    nombre, grupos = USUARIOS_DEV[login]
    return construir_usuario(login, nombre, grupos, mapa, mfa=False, ip=ip)


# --------------------------------------------------------------------------- registro de la demo
RE_LOGIN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,59}$")


def login_desde_nombre(nombre: str) -> str:
    """'María Pérez' -> 'maria.perez' (sin tildes ni espacios)."""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    partes = ["".join(c for c in p if c.isalnum()) for p in s.split()]
    return ".".join(p for p in partes if p)[:60]


def ingreso_demo(nombre: str, perfil: str, login: str = "", ip: str = "") -> Usuario:
    nombre = " ".join((nombre or "").split())
    if len(nombre) < 3:
        raise ValueError("escribe tu nombre (al menos 3 letras)")
    if perfil not in PERFILES_DEMO:
        raise ValueError(f"perfil inválido: elige uno de {', '.join(PERFILES_DEMO)}")
    login = (login or "").strip().lower() or login_desde_nombre(nombre)
    if not RE_LOGIN.match(login):
        raise ValueError("usuario inválido: usa letras, números, punto, guion o guion bajo")
    return Usuario(login=login, nombre=nombre, grupos=[f"DEMO:{perfil}"], perfiles=frozenset({perfil}),
                   mfa_en=None, ip=ip)
