"""Cliente de Zabbix: API JSON-RPC (lectura y mantenimiento) y envío de ítems trapper.

- Lectura: trend.get (series horarias para gráficos y ML), problem.get (respaldo del webhook).
- Mantenimiento: maintenance.create/delete durante las ventanas de cambio (RF-ZBX-05).
- Publicación: predicciones como ítems trapper, con el protocolo de zabbix_sender (RF-ZBX-04).
El token es de solo lectura salvo para mantenimiento y trapper (permisos en el rol de Zabbix).
"""
from __future__ import annotations

import json
import socket
import struct
import time

import httpx


class ErrorZabbix(RuntimeError):
    pass


class ClienteZabbix:
    def __init__(self, url: str, token: str, *, verificar_tls: bool | str = True, timeout: float = 20):
        self._c = httpx.Client(timeout=timeout, verify=verificar_tls,
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json-rpc"})
        self._url = url
        self._id = 0

    def llamar(self, metodo: str, params: dict | list) -> object:
        self._id += 1
        r = self._c.post(self._url, content=json.dumps({"jsonrpc": "2.0", "method": metodo, "params": params,
                                                         "id": self._id}))
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            raise ErrorZabbix(f"{metodo}: {d['error'].get('data') or d['error'].get('message')}")
        return d["result"]

    def trends(self, itemids: list[str], desde: int, hasta: int | None = None) -> list[dict]:
        p = {"output": ["itemid", "clock", "value_min", "value_avg", "value_max"], "itemids": itemids,
             "time_from": desde, "time_till": hasta or int(time.time())}
        return self.llamar("trend.get", p)  # type: ignore[return-value]

    def problemas(self, severidad_min: int = 2) -> list[dict]:
        return self.llamar("problem.get", {"output": "extend", "selectTags": "extend", "recent": False,
                                           "severities": list(range(severidad_min, 6))})  # type: ignore

    def hostids(self, nombres: list[str]) -> list[str]:
        r = self.llamar("host.get", {"output": ["hostid"], "filter": {"host": nombres}})
        return [h["hostid"] for h in r]  # type: ignore[union-attr]

    def crear_mantenimiento(self, nombre: str, hostids: list[str], desde: int, hasta: int) -> str:
        r = self.llamar("maintenance.create", {
            "name": nombre[:128], "active_since": desde, "active_till": hasta, "hostids": hostids,
            "maintenance_type": 0,  # con recolección de datos
            "timeperiods": [{"timeperiod_type": 0, "start_date": desde, "period": max(600, hasta - desde)}]})
        return r["maintenanceids"][0]  # type: ignore[index]

    def borrar_mantenimiento(self, mid: str) -> None:
        self.llamar("maintenance.delete", [mid])


def enviar_trapper(servidor: str, puerto: int, datos: list[dict], timeout: float = 10) -> dict:
    """Protocolo de zabbix_sender. datos = [{"host", "key", "value", "clock"?}]."""
    carga = json.dumps({"request": "sender data", "data": datos}).encode()
    paquete = b"ZBXD\x01" + struct.pack("<Q", len(carga)) + carga
    with socket.create_connection((servidor, puerto), timeout=timeout) as s:
        s.sendall(paquete)
        cab = _recibir(s, 13)
        if cab[:4] != b"ZBXD":
            raise ErrorZabbix("respuesta inválida del servidor Zabbix")
        largo = struct.unpack("<Q", cab[5:13])[0]
        resp = json.loads(_recibir(s, largo))
    if resp.get("response") != "success":
        raise ErrorZabbix(f"trapper rechazado: {resp}")
    return resp


def _recibir(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        parte = s.recv(n - len(b))
        if not parte:
            break
        b += parte
    return b
