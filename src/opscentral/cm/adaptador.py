"""Adaptador del motor de Capa Media.

Por cada ejecución:
1. Firma un certificado SSH de corta duración (cm.certificado_ssh, con Vault): key_id = persona#ejecución.
   Sin certificado, cmops usa la llave que tenga configurada.
2. Lee de los secretos la cuenta WebLogic que corresponde (ops_operador u ops_admin) si la acción la necesita.
3. Lanza el puente con el Python de cmops y pasa todo por stdin (nunca por argumentos: `ps` los muestra).
4. Reenvía la salida línea por línea a la ejecución y borra la llave efímera al terminar.

El rol que recibe cmops lo calcula Ops Central (la autorización ya se hizo aquí):
lectura -> consulta · cambio fuera de PROD -> operador · cambio en PROD/DRP -> admin.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..catalogo import AMBIENTES_PROTEGIDOS, Accion
from ..config import RAIZ
from ..credenciales import certificado_ssh, cuenta_weblogic
from ..secretos import Secretos

PUENTE = Path(__file__).with_name("puente_cmops.py")
USAN_HOSTS = {"sys.conectividad", "rec.recolectar"}   # en cmops estas acciones reciben una lista 'hosts'
NECESITAN_WL ={"orq.credenciales": ("nm_usuario", "nm_password"), "usr.preparar": ("admin_usuario", "admin_password")}


class ErrorCM(RuntimeError):
    pass


def rol_cmops(accion: Accion, ambiente: str | None) -> str:
    if accion.clase_en(ambiente) == "L":
        return "consulta"
    return "admin" if (ambiente or "").upper() in AMBIENTES_PROTEGIDOS else "operador"


def referencia_cmops(e: dict) -> str:
    """cmops exige una referencia en PROD/DRP. El caso es opcional en Ops Central:
    si no hay, se usa el id de la ejecución, que la liga al log y a la evidencia."""
    return e.get("cambio") or f"OPS-{e['id']}"


def lote_maximo(cfg, ambiente: str | None) -> int:
    """Tope del lote de frm.mitigar por ambiente ([cm] frm_lote_max; '*' = el resto)."""
    topes = cfg["cm"].get("frm_lote_max") or {}
    return int(topes.get(str(ambiente or "").upper(), topes.get("*", 40)))


class MotorCM:
    def __init__(self, cfg, secretos: Secretos):
        self.cfg, self.sec = cfg, secretos
        from ..config import ruta_cmops
        self.ruta = ruta_cmops(cfg)

    def ejecutar(self, e: dict, acc: Accion, emitir: Callable[[str], None]) -> dict:
        if not (self.ruta / "cmops" / "trabajos.py").exists():
            raise ErrorCM(f"no se encuentra capa-media-ops en {self.ruta}")
        secretos: dict = {}
        if acc.id in NECESITAN_WL:
            u, p = NECESITAN_WL[acc.id]
            wl = self.sec.leer(cuenta_weblogic(acc), motivo=f"{acc.id} {e['id']}")
            secretos = {u: wl["usuario"], p: wl["clave"]}
        cert = None
        try:
            if self.cfg["cm"].get("certificado_ssh", self.cfg["secretos"]["proveedor"] == "vault"):
                cert = certificado_ssh(self.sec, usuario=e["solicitante"], ejecucion_id=e["id"],
                                       principal="svc_opscentral", ttl_min=self.cfg["cm"]["ttl_certificado_min"])
                emitir(f"Certificado SSH emitido ({self.cfg['cm']['ttl_certificado_min']} min) para {e['solicitante']}")
            params = dict(e["params"])
            if acc.id == "frm.mitigar":
                tope = lote_maximo(self.cfg, e.get("ambiente"))
                pedido = int(params.get("lote") or tope)
                if pedido > tope:
                    emitir(f"Lote pedido {pedido} > máximo de {e.get('ambiente')}: se usa {tope} ([cm] frm_lote_max)")
                params["lote"] = max(1, min(pedido, tope))
            if acc.id in USAN_HOSTS and not params.get("hosts") and params.get("host"):
                params["hosts"] = params.pop("host")
            req = {"ruta_cmops": str(self.ruta), "accion": acc.id, "params": params, "secretos": secretos,
                   "persona": e["solicitante"], "rol": rol_cmops(acc, e["ambiente"]), "cambio": referencia_cmops(e),
                   "llave": str(cert.llave) if cert else "",
                   "ejecucion": e["id"],
                   # Única fuente de servidores/IPs/dominios: el inventario de Ops Central (no el de cmops).
                   "inventario": str(self.cfg.ruta("inventario", "archivo"))}
            return self._correr(req, emitir)
        finally:
            if cert:
                cert.borrar()

    def _python(self) -> str:
        p = self.cfg["cm"].get("python_cmops") or "python"
        return sys.executable if p == "python" else p

    def _correr(self, req: dict, emitir) -> dict:
        proc = subprocess.Popen([self._python(), "-I", str(PUENTE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", cwd=str(self.ruta))
        proc.stdin.write(json.dumps(req))
        proc.stdin.close()
        fin = None
        for l in proc.stdout:
            try:
                ev = json.loads(l)
            except ValueError:
                emitir(l.rstrip())
                continue
            if ev["t"] == "linea":
                emitir(ev["texto"])
            elif ev["t"] == "error":
                proc.wait(timeout=30)
                raise ErrorCM(f"{ev['tipo']}: {ev['mensaje']}")
            elif ev["t"] == "fin":
                fin = ev
        try:
            proc.wait(timeout=self.cfg["cm"]["timeout_s"])
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ErrorCM("tiempo agotado en el motor de Capa Media") from None
        if not fin:
            raise ErrorCM(f"el motor de Capa Media terminó sin resultado (código {proc.returncode})")
        if fin["estado"] != "exitoso":
            raise ErrorCM(f"trabajo {fin['trabajo']} terminó en estado {fin['estado']}")
        return fin
