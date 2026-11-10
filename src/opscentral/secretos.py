"""Gestor de secretos (cierra H6) y reglas de quién puede leer qué.

Las credenciales de destino no viven en variables de entorno ni en el repositorio.
Cada componente se identifica con un CONTEXTO y solo lee las rutas que le tocan:

  api          firma de aprobaciones, secreto del webhook de Zabbix, cliente OIDC, Jira (solo lectura de casos)
  worker-bd    oracle/<AMB>/OPS_CONSULTA y oracle/<AMB>/OPS_DBA
  worker-ingesta / worker-ml   oracle/<AMB>/OPS_CONSULTA, zabbix/*
  worker-cm    weblogic/* y la firma de certificados SSH

La API NUNCA puede leer OPS_DBA: aunque la API se comprometa, no hay cómo cambiar una base.
"""
from __future__ import annotations

import fnmatch
import json
import threading
from pathlib import Path
from typing import Any, Protocol

import httpx

POLITICA: dict[str, list[str]] = {
    "api": ["ops/firma_aprobaciones", "zabbix/secreto_webhook", "auth/*", "almacen/*", "jira/*"],
    "worker-bd": ["oracle/*/OPS_CONSULTA", "oracle/*/OPS_DBA", "ops/firma_aprobaciones", "almacen/*"],
    "worker-ingesta": ["oracle/*/OPS_CONSULTA", "zabbix/*", "almacen/*"],
    "worker-ml": ["zabbix/*", "almacen/*"],
    "worker-cm": ["weblogic/*", "ssh/*", "ops/firma_aprobaciones", "almacen/*"],
    "cli": ["almacen/*", "ops/firma_aprobaciones"],
}


class AccesoSecretoDenegado(PermissionError):
    pass


class ProveedorSecretos(Protocol):
    def leer(self, ruta: str) -> dict[str, Any]: ...
    def firmar_ssh(self, llave_publica: str, principal: str, id_llave: str, ttl_min: int) -> str: ...


class SecretosArchivo:
    """SOLO desarrollo: lee un JSON local. config._validar impide usarlo en producción."""

    def __init__(self, ruta: Path):
        self._ruta = ruta
        self._datos: dict | None = None
        self._lock = threading.Lock()

    def leer(self, ruta: str) -> dict[str, Any]:
        with self._lock:
            if self._datos is None:
                self._datos = json.loads(self._ruta.read_text(encoding="utf-8")) if self._ruta.exists() else {}
        if ruta not in self._datos:
            raise KeyError(f"secreto no encontrado: {ruta}")
        return dict(self._datos[ruta])

    def firmar_ssh(self, llave_publica: str, principal: str, id_llave: str, ttl_min: int) -> str:
        raise RuntimeError("la firma de certificados SSH requiere el gestor de secretos (Vault)")


class SecretosVault:
    """HashiCorp Vault: KV v2 para claves y motor SSH para certificados de corta duración.

    Autenticación del componente: AppRole (role_id + secret_id entregado por el despliegue,
    envuelto y de un solo uso). El token resultante vive solo en memoria.
    """

    def __init__(self, url: str, kv: str, ssh_montaje: str, ssh_rol: str, token: str,
                 verificar_tls: bool | str = True):
        self._c = httpx.Client(base_url=url.rstrip("/"), headers={"X-Vault-Token": token},
                               timeout=10, verify=verificar_tls)
        self._kv, self._ssh, self._rol = kv, ssh_montaje, ssh_rol

    @classmethod
    def con_approle(cls, url: str, kv: str, ssh_montaje: str, ssh_rol: str, role_id: str, secret_id: str,
                    verificar_tls: bool | str = True) -> "SecretosVault":
        r = httpx.post(f"{url.rstrip('/')}/v1/auth/approle/login", json={"role_id": role_id, "secret_id": secret_id},
                       timeout=10, verify=verificar_tls)
        r.raise_for_status()
        return cls(url, kv, ssh_montaje, ssh_rol, r.json()["auth"]["client_token"], verificar_tls)

    def leer(self, ruta: str) -> dict[str, Any]:
        r = self._c.get(f"/v1/{self._kv}/data/{ruta}")
        if r.status_code == 404:
            raise KeyError(f"secreto no encontrado: {ruta}")
        r.raise_for_status()
        return r.json()["data"]["data"]

    def firmar_ssh(self, llave_publica: str, principal: str, id_llave: str, ttl_min: int) -> str:
        r = self._c.post(f"/v1/{self._ssh}/sign/{self._rol}", json={
            "public_key": llave_publica, "valid_principals": principal, "key_id": id_llave,
            "ttl": f"{int(ttl_min)}m", "cert_type": "user",
            # sin pty ni reenvíos: lo restringe también el rol en Vault
            "extensions": {}})
        r.raise_for_status()
        return r.json()["data"]["signed_key"]


class Secretos:
    """Fachada con control de acceso por contexto y auditoría de lecturas."""

    def __init__(self, proveedor: ProveedorSecretos, contexto: str, auditar=None):
        if contexto not in POLITICA:
            raise ValueError(f"contexto desconocido: {contexto}")
        self._p, self.contexto, self._auditar = proveedor, contexto, auditar

    def permitido(self, ruta: str) -> bool:
        return any(fnmatch.fnmatchcase(ruta, patron) for patron in POLITICA[self.contexto])

    def leer(self, ruta: str, *, motivo: str = "") -> dict[str, Any]:
        if not self.permitido(ruta):
            raise AccesoSecretoDenegado(f"el componente '{self.contexto}' no puede leer '{ruta}'")
        datos = self._p.leer(ruta)
        if self._auditar:
            self._auditar("secreto_leido", self.contexto, ruta=ruta, motivo=motivo)
        return datos

    def firmar_ssh(self, llave_publica: str, principal: str, id_llave: str, ttl_min: int) -> str:
        if not self.permitido("ssh/firma"):
            raise AccesoSecretoDenegado(f"el componente '{self.contexto}' no puede firmar certificados SSH")
        return self._p.firmar_ssh(llave_publica, principal, id_llave, ttl_min)


def crear_proveedor(cfg) -> ProveedorSecretos:
    s = cfg["secretos"]
    if s["proveedor"] == "archivo":
        return SecretosArchivo(cfg.ruta("secretos", "archivo"))
    if s["proveedor"] == "vault":
        import os
        return SecretosVault.con_approle(s["vault_url"], s.get("vault_kv", "secret"), s.get("vault_ssh", "ssh"),
                                         s.get("vault_ssh_rol", "ops-central-cm"),
                                         os.environ["OPS_VAULT_ROLE_ID"], os.environ["OPS_VAULT_SECRET_ID"])
    raise ValueError(f"proveedor de secretos desconocido: {s['proveedor']}")
