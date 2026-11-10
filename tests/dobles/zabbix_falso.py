"""Doble de prueba de la API de Zabbix (solo para tests): responde JSON-RPC en memoria.

Se conecta al ClienteZabbix real mediante httpx.MockTransport, así que el cliente,
la detección de versión, la ingesta de trends y la sincronización de problemas se
prueban con el mismo código que irá contra el Zabbix real.

Datos deterministas:
- Un host por cada servidor y base del inventario (zabbix_host).
- Servidores: CPU, memoria y filesystems. AP23SBCC04:/oracle crece ~0,9 %/día
  (se llena en ~20 días: aparece en Prevención); el resto se mantiene estable.
- Problemas abiertos: tablespace en CORPRD, stuck threads en AP23SBCC09 y uno
  en mantenimiento (suppressed) en WLPC02SBCC04.
"""
from __future__ import annotations

import json
import random
import time

import httpx

VERSION = "7.0.5"
H = 3600

# host: [(key, nombre, nivel_actual, pendiente_por_dia, ruido)]
_ITEMS_SERVIDOR = [
    ("system.cpu.util", "CPU utilization", 38.0, 0.0, 9.0),
    ("vm.memory.utilization", "Memory utilization", 71.0, 0.02, 2.0),
    ("vfs.fs.dependent.size[/,pused]", "/: Space utilization", 46.0, 0.01, 0.2),
]
_ITEMS_EXTRA = {
    "AP23SBCC04": [("vfs.fs.dependent.size[/oracle,pused]", "/oracle: Space utilization", 82.0, 0.9, 0.35)],
    "DBPRD01": [("vfs.fs.dependent.size[/u02,pused]", "/u02: Space utilization", 71.0, 0.4, 0.3)],
    "DBTST01": [("vfs.fs.dependent.size[/u02,pused]", "/u02: Space utilization", 55.0, 0.05, 0.3)],
}


def _problemas_iniciales(ahora: int) -> list[dict]:
    return [
        {"eventid": "9001001", "objectid": "7001", "host": "CORPRD", "severity": "3", "suppressed": "0",
         "name": "Oracle CORPRD: Tablespace APP_DATA usage is over 80%", "clock": str(ahora - 40 * 60),
         "tags": [{"tag": "component", "value": "tablespace"}]},
        {"eventid": "9001002", "objectid": "7002", "host": "AP23SBCC09", "severity": "4", "suppressed": "0",
         "name": "WebLogic creditoprd: Stuck threads on Clientes1", "clock": str(ahora - 25 * 60),
         "tags": [{"tag": "component", "value": "weblogic"}]},
        {"eventid": "9001003", "objectid": "7003", "host": "WLPC02SBCC04", "severity": "2", "suppressed": "1",
         "name": "/: Disk space is low (used > 80%)", "clock": str(ahora - 90 * 60),
         "tags": [{"tag": "component", "value": "storage"}]},
    ]


class SimuladorZabbix:
    def __init__(self, hosts: list[str], *, version: str = VERSION, ahora: int | None = None):
        self.version = version
        self.ahora = ahora or int(time.time())
        self.hosts = {h.upper(): str(10100 + i) for i, h in enumerate(sorted({h.upper() for h in hosts}))}
        self._items: dict[str, dict] = {}
        for host, hid in self.hosts.items():
            if host in ("CORPRD", "CRMPRD", "CORTST"):   # hosts de base: ítems de la plantilla Oracle, no de capacidad
                continue
            for j, (key, nombre, nivel, pend, ruido) in enumerate(_ITEMS_SERVIDOR + _ITEMS_EXTRA.get(host, [])):
                iid = f"{hid}{j:02d}"
                self._items[iid] = {"itemid": iid, "hostid": hid, "key_": key, "name": nombre, "value_type": "0",
                                    "units": "%", "lastvalue": str(nivel), "trends": "365d",
                                    "_nivel": nivel, "_pend": pend, "_ruido": ruido}
        self.problemas = [p for p in _problemas_iniciales(self.ahora) if p["host"] in self.hosts]
        self.llamadas: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------ control para pruebas/demo
    def resolver(self, eventid: str) -> None:
        self.problemas = [p for p in self.problemas if p["eventid"] != eventid]

    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._responder)

    # ------------------------------------------------------------------ API
    def _responder(self, req: httpx.Request) -> httpx.Response:
        d = json.loads(req.content)
        metodo, p = d["method"], d.get("params") or {}
        self.llamadas.append((metodo, p))
        if metodo != "apiinfo.version" and req.headers.get("Authorization") is None and "auth" not in d:
            return self._error(d, -32602, "Not authorized.")
        f = getattr(self, "_m_" + metodo.replace(".", "_"), None)
        if f is None:
            return self._error(d, -32601, f"Method not found: {metodo}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": f(p), "id": d["id"]})

    @staticmethod
    def _error(d, codigo, msg):
        return httpx.Response(200, json={"jsonrpc": "2.0", "error": {"code": codigo, "message": msg, "data": ""},
                                         "id": d.get("id")})

    def _m_apiinfo_version(self, p):
        return self.version

    def _m_host_get(self, p):
        nombres = {n.upper() for n in (p.get("filter") or {}).get("host", self.hosts)}
        return [{"hostid": hid, "host": h, "name": h, "status": "0"} for h, hid in self.hosts.items() if h in nombres]

    def _m_item_get(self, p):
        hids = set(p.get("hostids") or [])
        return [{k: v for k, v in it.items() if not k.startswith("_")}
                for it in self._items.values() if it["hostid"] in hids]

    def _m_trend_get(self, p):
        desde, hasta = int(p["time_from"]), int(p.get("time_till") or self.ahora)
        desde = max(desde, self.ahora - 30 * 86400)          # el simulador guarda 30 días
        filas = []
        for iid in p.get("itemids") or []:
            it = self._items.get(iid)
            if not it:
                continue
            t = (desde // H + 1) * H if desde % H else desde
            while t <= min(hasta, max(self.ahora, int(time.time()))):   # sigue generando horas nuevas
                rnd = random.Random(f"{iid}:{t}")
                v = it["_nivel"] - it["_pend"] * (self.ahora - t) / 86400 + rnd.gauss(0, it["_ruido"])
                v = max(0.0, min(100.0, v))
                filas.append({"itemid": iid, "clock": str(t), "num": "60", "value_min": f"{max(0, v - 1):.4f}",
                              "value_avg": f"{v:.4f}", "value_max": f"{min(100, v + 1):.4f}"})
                t += H
        return filas

    def _m_problem_get(self, p):
        sev = {str(s) for s in p.get("severities") or range(6)}
        return [{k: v for k, v in pr.items() if k != "host"} | {"acknowledged": "0"}
                for pr in self.problemas if pr["severity"] in sev]

    def _m_trigger_get(self, p):
        ids = set(p.get("triggerids") or [])
        out = []
        for pr in self.problemas:
            if pr["objectid"] in ids:
                out.append({"triggerid": pr["objectid"], "description": pr["name"], "priority": pr["severity"],
                            "hosts": [{"hostid": self.hosts[pr["host"]], "host": pr["host"], "name": pr["host"]}]})
        return out


_SIM: SimuladorZabbix | None = None


def instancia(hosts: list[str]) -> SimuladorZabbix:
    global _SIM
    if _SIM is None:
        _SIM = SimuladorZabbix(hosts)
    return _SIM


def reiniciar() -> None:
    global _SIM
    _SIM = None
