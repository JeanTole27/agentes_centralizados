"""Cliente de Zabbix: API JSON-RPC (lectura y mantenimiento) y envío de ítems trapper.

Adaptado del cliente de zabbix-capacity-mcp (versión síncrona, sin LLM):
- Detecta la versión con apiinfo.version. Desde 6.4 el token va en "Authorization: Bearer";
  en 6.0–6.2 va en el campo "auth" del cuerpo. Versiones < 6.0 se rechazan.
- Token de API (recomendado, de un usuario de solo lectura) o user.login como alternativa,
  con un nuevo login si la sesión vence.
- Reintenta errores 5xx y de red con espera exponencial (3 intentos).

Uso en Ops Central:
- Lectura: problem.get + trigger.get (respaldo del webhook), host.get + item.get + trend.get
  (series horarias de capacidad para gráficos y ML).
- Mantenimiento: maintenance.create/delete durante las ventanas de cambio (RF-ZBX-05).
- Publicación: predicciones como ítems trapper con el protocolo de zabbix_sender (RF-ZBX-04).
El token es de solo lectura salvo para mantenimiento y trapper (permisos en el rol de Zabbix).
"""
from __future__ import annotations

import itertools
import json
import socket
import struct
import time

import httpx

VERSION_MINIMA = (6, 0)
BEARER_DESDE = (6, 4)


class ErrorZabbix(RuntimeError):
    def __init__(self, mensaje: str, *, metodo: str = "", codigo: int = 0, datos: str = ""):
        super().__init__(mensaje)
        self.metodo, self.codigo, self.datos = metodo, codigo, datos

    @property
    def es_sesion(self) -> bool:
        t = str(self).lower()
        return any(s in t for s in ("session terminated", "re-login", "not authoris", "not authoriz"))

    @property
    def es_permiso(self) -> bool:
        t = str(self).lower()
        return any(s in t for s in ("no permissions", "permission denied", "access denied"))


class VersionNoSoportada(ErrorZabbix):
    pass


def version_tupla(v: str) -> tuple[int, int]:
    partes = (v.split(".") + ["0"])[:2]
    return int(partes[0]), int(partes[1])


def normalizar_url(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("api_jsonrpc.php") else f"{url}/api_jsonrpc.php"


class ClienteZabbix:
    INTENTOS = 3

    def __init__(self, url: str, token: str | None = None, *, usuario: str | None = None,
                 clave: str | None = None, verificar_tls: bool | str = True, timeout: float = 30,
                 user_agent: str = "OpsCentral/0.1", espera_base: float = 1.0,
                 transporte: httpx.BaseTransport | None = None):
        if not token and not (usuario and clave):
            raise ValueError("se necesita un token de API o usuario y clave de Zabbix")
        self.url = normalizar_url(url)
        self._token, self._usuario, self._clave = token, usuario, clave
        self._c = httpx.Client(timeout=timeout, verify=verificar_tls, transport=transporte,
                               headers={"User-Agent": user_agent})
        self._ids = itertools.count(1)
        self._auth: str | None = None
        self._por_login = False
        self._bearer = False
        self._espera = espera_base
        self.version: str | None = None

    # ------------------------------------------------------------------ conexión
    def conectar(self) -> None:
        if self._auth:
            return
        self.version = str(self._post("apiinfo.version", {}, con_auth=False))
        vt = version_tupla(self.version)
        if vt < VERSION_MINIMA:
            raise VersionNoSoportada(f"Zabbix {self.version} está fuera de soporte; se requiere 6.0 LTS o superior")
        self._bearer = vt >= BEARER_DESDE
        self._autenticar()

    def _autenticar(self) -> None:
        if self._token:
            self._auth, self._por_login = self._token, False
        else:
            self._auth = str(self._post("user.login", {"username": self._usuario, "password": self._clave},
                                        con_auth=False))
            self._por_login = True

    @property
    def modo_auth(self) -> str:
        tipo = "token de API" if self._token else "sesión user.login"
        return f"{tipo} vía {'header Bearer' if self._bearer else 'campo auth'}"

    def cerrar(self) -> None:
        try:
            if self._por_login and self._auth:
                self._post("user.logout", [], con_auth=True)
        except Exception:  # noqa: BLE001 - cerrar nunca debe fallar
            pass
        finally:
            self._c.close()

    # ------------------------------------------------------------------ llamadas
    def llamar(self, metodo: str, params: dict | list | None = None) -> object:
        self.conectar()
        params = {} if params is None else params
        try:
            return self._post(metodo, params, con_auth=True)
        except ErrorZabbix as e:
            if e.es_sesion and self._por_login:
                self._autenticar()
                return self._post(metodo, params, con_auth=True)
            raise

    def _post(self, metodo: str, params, *, con_auth: bool) -> object:
        cuerpo: dict = {"jsonrpc": "2.0", "method": metodo, "params": params, "id": next(self._ids)}
        cab = {"Content-Type": "application/json-rpc"}
        if con_auth and self._auth:
            if self._bearer:
                cab["Authorization"] = f"Bearer {self._auth}"
            else:
                cuerpo["auth"] = self._auth
        ultimo: Exception | None = None
        for intento in range(self.INTENTOS):
            try:
                r = self._c.post(self.url, content=json.dumps(cuerpo), headers=cab)
                if r.status_code >= 500:
                    ultimo = ErrorZabbix(f"HTTP {r.status_code}", metodo=metodo)
                else:
                    r.raise_for_status()
                    d = r.json()
                    break
            except httpx.TransportError as e:
                ultimo = e
            except httpx.HTTPStatusError as e:
                raise ErrorZabbix(f"{metodo}: HTTP {e.response.status_code}", metodo=metodo) from None
            if intento < self.INTENTOS - 1:
                time.sleep(self._espera * 2 ** intento)
        else:
            raise ErrorZabbix(f"Zabbix no respondió tras {self.INTENTOS} intentos ({metodo}): {ultimo}",
                              metodo=metodo)
        if "error" in d:
            err = d["error"]
            raise ErrorZabbix(f"{metodo}: {err.get('message', '')} {err.get('data', '')}".strip(),
                              metodo=metodo, codigo=err.get("code", 0), datos=err.get("data", ""))
        return d["result"]

    # ------------------------------------------------------------------ lectura
    def trends(self, itemids: list[str], desde: int, hasta: int | None = None) -> list[dict]:
        p = {"output": ["itemid", "clock", "num", "value_min", "value_avg", "value_max"], "itemids": itemids,
             "time_from": desde, "time_till": hasta or int(time.time())}
        return self.llamar("trend.get", p)  # type: ignore[return-value]

    def problemas(self, severidad_min: int = 2) -> list[dict]:
        return self.llamar("problem.get", {"output": "extend", "selectTags": "extend", "recent": False,
                                           "severities": list(range(severidad_min, 6))})  # type: ignore

    def triggers(self, triggerids: list[str]) -> list[dict]:
        if not triggerids:
            return []
        return self.llamar("trigger.get", {"output": ["triggerid", "description", "priority"],
                                           "triggerids": triggerids,
                                           "selectHosts": ["hostid", "host", "name"]})  # type: ignore

    def hosts(self, nombres: list[str]) -> list[dict]:
        return self.llamar("host.get", {"output": ["hostid", "host", "name", "status"],
                                        "filter": {"host": nombres}})  # type: ignore[return-value]

    def hosts_con_ip(self) -> list[dict]:
        """Todos los hosts con sus interfaces (para vincular el inventario por IP)."""
        return self.llamar("host.get", {"output": ["hostid", "host", "name", "status"],
                                        "selectInterfaces": ["ip", "main"]})  # type: ignore[return-value]

    def hostids(self, nombres: list[str]) -> list[str]:
        return [h["hostid"] for h in self.hosts(nombres)]

    def items_numericos(self, hostids: list[str]) -> list[dict]:
        """Ítems numéricos habilitados: los únicos que tienen trends."""
        return self.llamar("item.get", {
            "output": ["itemid", "hostid", "name", "key_", "value_type", "units", "lastvalue", "trends"],
            "hostids": hostids, "filter": {"value_type": [0, 3], "status": 0}})  # type: ignore

    # ------------------------------------------------------------------ mantenimiento
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
