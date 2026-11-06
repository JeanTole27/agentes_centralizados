"""Qué credencial usa cada acción. Lo decide la ACCIÓN, no la persona.

Bases de datos (dos cuentas de servicio por base):
  OPS_CONSULTA  clase L: recolección, pantallas de lectura, asistente. Sin DML/DDL.
  OPS_DBA       clase O/C del motor BD, solo tras autorización/aprobación. Solo EXECUTE
                sobre ADMINBD.PKG_OPS_CENTRAL; nunca rol DBA ni privilegios ANY.

Capa Media:
  SSH con certificado de corta duración (ttl 5–10 min) firmado por el gestor de secretos.
  El key_id lleva usuario#ejecución y queda en el log de sshd del servidor.
  WebLogic: ops_operador (orquestación, frmweb) y ops_admin (solo usr.*).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .catalogo import Accion
from .secretos import Secretos


@dataclass(frozen=True)
class CredencialBD:
    usuario: str
    clave: str
    tipo: str            # consulta | dba

    def __repr__(self) -> str:  # nunca imprimir la clave
        return f"CredencialBD(usuario={self.usuario!r}, tipo={self.tipo!r})"


def tipo_cuenta_bd(accion: Accion, ambiente: str | None) -> str:
    if accion.motor != "bd":
        raise ValueError(f"{accion.id} no es una acción de base de datos")
    if accion.clase_en(ambiente) in ("O", "C"):
        if not accion.procedimiento:
            raise ValueError(f"{accion.id}: los cambios en BD solo se hacen por procedimientos del paquete")
        return "dba"
    return "consulta"


def credencial_bd(secretos: Secretos, cfg, ambiente: str, tipo: str, *, motivo: str) -> CredencialBD:
    cuenta = cfg["bd"]["cuenta_dba"] if tipo == "dba" else cfg["bd"]["cuenta_consulta"]
    datos = secretos.leer(f"oracle/{ambiente.upper()}/{cuenta}", motivo=motivo)
    return CredencialBD(usuario=datos.get("usuario", cuenta), clave=datos["clave"], tipo=tipo)


def cuenta_weblogic(accion: Accion) -> str:
    return "weblogic/ops_admin" if accion.id.startswith("usr.") else "weblogic/ops_operador"


@dataclass
class CertificadoSSH:
    directorio: Path
    llave: Path          # privada efímera; ssh toma <llave>-cert.pub automáticamente

    def borrar(self) -> None:
        shutil.rmtree(self.directorio, ignore_errors=True)


def certificado_ssh(secretos: Secretos, *, usuario: str, ejecucion_id: str, principal: str,
                    ttl_min: int) -> CertificadoSSH:
    """Genera una llave ed25519 efímera, la firma con la CA y devuelve la ruta para `ssh -i`."""
    d = Path(tempfile.mkdtemp(prefix="opsc-ssh-"))
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    llave = d / "id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{usuario}#{ejecucion_id}",
                    "-f", str(llave)], check=True, capture_output=True)
    publica = (d / "id_ed25519.pub").read_text()
    firmado = secretos.firmar_ssh(publica, principal, f"{usuario}#{ejecucion_id}", ttl_min)
    (d / "id_ed25519-cert.pub").write_text(firmado)
    return CertificadoSSH(d, llave)
