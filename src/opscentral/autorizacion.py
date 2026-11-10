"""Autorización: quién puede pedir qué, y con qué controles.

Reglas (02 §1.2 y 03 RF-APR, ajustadas con el equipo):
- X: nunca desde la interfaz.
- L: la ejecuta quien tiene 'ejecuta'; quien tiene 've' solo lee resultados del almacén.
- O/C: quien tiene 'ejecuta' sobre la acción la ejecuta DIRECTAMENTE, sin cola de aprobación,
  tanto en Base de Datos como en Capa Media (aprobaciones.ejecucion_directa = true). Cada ejecución
  queda registrada (log de ejecución, auditoría encadenada y evidencia con SHA-256).
  Con ejecucion_directa = false vuelve el esquema de cuatro ojos para clase C.
- El caso asociado (clave de Jira, incidente, cambio...) es OPCIONAL. Solo se exige si
  aprobaciones.exigir_referencia_prod = true y la acción es O/C en PROD/DRP.
- C exige motivo. Step-up MFA para cualquier cambio en PROD/DRP.
- El perfil Automatizador ('aut') tiene permiso de ejecución y edición sobre todo el catálogo.
La decisión se toma SIEMPRE en el backend; la interfaz solo oculta botones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalogo import AMBIENTES_PROTEGIDOS, Accion
from .identidad import PERFIL_TOTAL, Usuario


class PermisoDenegado(PermissionError):
    pass


@dataclass
class Decision:
    permitido: bool
    motivo: str = ""
    clase: str = "L"
    requiere_cambio: bool = False
    requiere_aprobacion: bool = False
    requiere_stepup: bool = False
    directa: bool = False            # DBA habilitado: se ejecuta sin cola de aprobación
    controles: list[str] = field(default_factory=list)


def permiso_de(usuario: Usuario, accion: Accion) -> str | None:
    """'ejecuta' > 've' > None, uniendo todos los perfiles del usuario."""
    if PERFIL_TOTAL in usuario.perfiles:
        return "ejecuta"
    mejor = None
    for p in usuario.perfiles:
        v = accion.perfiles.get(p)
        if v == "ejecuta":
            return "ejecuta"
        if v == "ve":
            mejor = "ve"
    return mejor


def puede_ver(usuario: Usuario, accion: Accion) -> bool:
    return permiso_de(usuario, accion) is not None


def decidir(usuario: Usuario, accion: Accion, ambiente: str | None, *,
            aprobacion_c_no_prod: bool = True, ejecucion_directa: bool = True,
            exigir_referencia_prod: bool = False) -> Decision:
    clase = accion.clase_en(ambiente)
    protegido = (ambiente or "").upper() in AMBIENTES_PROTEGIDOS
    if clase == "X":
        return Decision(False, "acción bloqueada: no se ejecuta desde Ops Central", clase)
    if accion.sistema:
        return Decision(False, "acción de sistema: la ejecuta la plataforma, no una persona", clase)
    if accion.estado != "implementada":
        return Decision(False, f"la acción {accion.id} aún no está implementada", clase)
    if accion.bloqueo_impl:
        return Decision(False, accion.bloqueo_impl, clase)
    permiso = permiso_de(usuario, accion)
    if permiso != "ejecuta":
        motivo = "solo lectura para tu perfil" if permiso == "ve" else "tu perfil no tiene acceso a esta acción"
        return Decision(False, motivo, clase)

    d = Decision(True, "", clase)
    if clase in ("O", "C") and protegido:
        if exigir_referencia_prod:
            d.requiere_cambio = True
            d.controles.append("caso asociado (PROD/DRP)")
        d.requiere_stepup = True
        d.controles.append("MFA reciente (cambio en PROD/DRP)")
    if clase == "C":
        d.controles.append("motivo obligatorio")
        if ejecucion_directa:
            d.directa = True
            d.controles.append("ejecución directa con permiso de ejecución (queda en el log)")
        elif protegido or aprobacion_c_no_prod:
            d.requiere_aprobacion = True
            d.controles.append("aprobación de una segunda persona")
    return d


def puede_aprobar(aprobador: Usuario, solicitante_login: str, accion: Accion) -> tuple[bool, str]:
    """Cuatro ojos: nadie aprueba lo suyo. El líder aprueba todo; DBA aprueba BD; CM aprueba CM."""
    if aprobador.login == solicitante_login.lower():
        return False, "nadie puede aprobar su propia solicitud"
    if {"lid", PERFIL_TOTAL} & set(aprobador.perfiles):
        return True, ""
    equipo_accion = "dba" if accion.motor == "bd" else "cm" if accion.motor == "cm" else None
    if equipo_accion and equipo_accion in aprobador.perfiles and permiso_de(aprobador, accion) == "ejecuta":
        return True, ""
    return False, "solo el líder o un miembro habilitado del mismo equipo puede aprobar"


def exigir(d: Decision) -> None:
    if not d.permitido:
        raise PermisoDenegado(d.motivo)


__all__ = ["Decision", "PermisoDenegado", "decidir", "exigir", "permiso_de", "puede_aprobar", "puede_ver",
           "AMBIENTES_PROTEGIDOS"]
