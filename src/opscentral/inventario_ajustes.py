"""Ajustes del inventario desde la interfaz (CRUD) superpuestos al archivo generado.

`config/inventario.json` se sigue generando desde los insumos reales (`generar-inventario`).
Encima se aplican los ajustes guardados en el almacén (tabla ops_inventario_ajuste):

  alta    activo nuevo que no está en los insumos (p. ej. una base DESA o DRP)
  cambio  campos corregidos de un activo existente (ambiente, zabbix_host, criticidad…)
  baja    activo que se oculta del inventario (sus relaciones también)

Así regenerar el archivo no pierde lo editado, y cada ajuste queda auditado con quién y cuándo.
El id de un activo no se cambia: para renombrar, baja + alta.

Quién puede editar: `[inventario] editores` (lista de logins). Vacía = todos (modo demo).
"""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

from sqlalchemy import delete, func, select

from .almacen import Almacen, inventario_ajuste, utc
from .inventario import Inventario

AMBIENTES = ["PROD", "DRP", "QA", "TEST", "DESA"]
LISTA = {"servidor": "servidores", "dominio": "dominios", "base": "bases"}

# Campos editables por tipo: nombre -> (etiqueta, tipo, obligatorio)
CAMPOS: dict[str, dict[str, tuple[str, str, bool]]] = {
    "servidor": {"ip": ("IP", "texto", False), "ambiente": ("Ambiente", "ambiente", True),
                 "equipo": ("Equipo", "equipo", False), "zabbix_host": ("Host en Zabbix", "texto", False),
                 "ssh_port": ("Puerto SSH", "entero", False)},
    "dominio": {"nombre": ("Nombre", "texto", True), "servidores": ("Servidores", "lista_servidores", True),
                "ambiente": ("Ambiente", "ambiente", True)},
    "base": {"alias": ("Alias", "texto", False), "servicio": ("Servicio", "texto", False),
             "servidor": ("Servidor", "servidor", False), "dsn": ("DSN (host:puerto/servicio)", "texto", False),
             "ambiente": ("Ambiente", "ambiente", True), "criticidad": ("Criticidad", "criticidad", False),
             "equipo_dueno": ("Equipo dueño", "texto", False), "zabbix_host": ("Host en Zabbix", "texto", False)},
}
OPCIONES = {"ambiente": AMBIENTES, "equipo": ["cm", "dba", "otro"], "criticidad": ["alta", "media", "baja", "sin definir"]}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ()]{0,79}$")
_DSN = re.compile(r"^[^:/\s]+:\d{1,5}/\S+$")


class ErrorInventario(ValueError):
    pass


def _norm_id(tipo: str, id_: str) -> str:
    id_ = (id_ or "").strip()
    if not _ID.match(id_):
        raise ErrorInventario("id inválido: letras, números, espacio y . _ - ( ), hasta 80 caracteres")
    return id_ if tipo == "dominio" else id_.upper()


def validar(tipo: str, datos: dict, *, alta: bool, inv: Inventario) -> dict:
    if tipo not in CAMPOS:
        raise ErrorInventario(f"tipo desconocido: {tipo}")
    out: dict[str, Any] = {}
    for campo, valor in (datos or {}).items():
        if campo not in CAMPOS[tipo]:
            continue                      # se ignoran campos que no se pueden editar (id, relacionados…)
        _, clase, _ = CAMPOS[tipo][campo]
        if valor in (None, "") or valor == []:
            out[campo] = None
            continue
        if clase in OPCIONES:
            v = str(valor).strip()
            v = v.upper() if clase == "ambiente" else v.lower() if clase != "criticidad" else v.lower()
            if v not in OPCIONES[clase]:
                raise ErrorInventario(f"{campo}: valor no permitido ({', '.join(OPCIONES[clase])})")
            out[campo] = v
        elif clase == "entero":
            try:
                out[campo] = int(valor)
            except (TypeError, ValueError):
                raise ErrorInventario(f"{campo}: debe ser un número") from None
        elif clase == "servidor":
            v = str(valor).strip().upper()
            if not inv.servidor(v):
                raise ErrorInventario(f"el servidor {v} no está en el inventario")
            out[campo] = v
        elif clase == "lista_servidores":
            lista = valor if isinstance(valor, list) else [x for x in re.split(r"[,;\s]+", str(valor)) if x]
            lista = sorted({x.strip().upper() for x in lista})
            faltan = [x for x in lista if not inv.servidor(x)]
            if faltan:
                raise ErrorInventario(f"servidores fuera del inventario: {', '.join(faltan)}")
            out[campo] = lista
        else:
            v = str(valor).strip()[:200]
            if campo == "dsn" and not _DSN.match(v):
                raise ErrorInventario("dsn: formato host:puerto/servicio")
            out[campo] = v.upper() if campo == "zabbix_host" else v
    if alta:
        faltan = [CAMPOS[tipo][c][0] for c, (_, _, ob) in CAMPOS[tipo].items() if ob and not out.get(c)]
        if faltan:
            raise ErrorInventario(f"faltan campos obligatorios: {', '.join(faltan)}")
    if tipo == "dominio" and out.get("servidores"):
        out["servidor"] = out["servidores"][0]
    return out


def combinar(datos: dict, ajustes: list[dict]) -> dict:
    """Aplica los ajustes (en orden) sobre una copia del inventario generado."""
    d = copy.deepcopy(datos)
    for lista in LISTA.values():
        d.setdefault(lista, [])
    d.setdefault("relaciones", [])
    for a in ajustes:
        lista = d[LISTA[a["tipo"]]]
        idx = next((i for i, x in enumerate(lista) if str(x["id"]).upper() == a["id"].upper()), None)
        if a["operacion"] == "baja":
            if idx is not None:
                lista.pop(idx)
            campo = "base" if a["tipo"] == "base" else "dominio" if a["tipo"] == "dominio" else None
            if campo:
                d["relaciones"] = [r for r in d["relaciones"] if str(r.get(campo, "")).upper() != a["id"].upper()]
            continue
        cambios = {k: v for k, v in a["datos"].items()}
        if idx is None:
            item = {"id": a["id"], **{k: v for k, v in cambios.items() if v is not None}, "_ajuste": a["operacion"]}
            if a["tipo"] == "dominio" and not item.get("servidor") and item.get("servidores"):
                item["servidor"] = item["servidores"][0]
            if a["tipo"] == "dominio" and not item.get("servidor"):
                continue                       # un dominio sin servidor no se puede ubicar en el grafo
            lista.append(item)
        else:
            item = lista[idx]
            for k, v in cambios.items():
                if v is None:
                    item.pop(k, None)
                else:
                    item[k] = v
            item["_ajuste"] = "cambio" if item.get("_ajuste") != "alta" else "alta"
    return d


class ServicioInventario:
    """Lee y escribe los ajustes y mantiene el inventario compartido al día."""

    def __init__(self, cfg, almacen: Almacen, inv: Inventario, base: dict):
        self.cfg, self.a, self.inv = cfg, almacen, inv
        self.base = base                       # contenido de config/inventario.json
        self._version: tuple | None = None
        self._lock = threading.Lock()
        self.sincronizar(forzar=True)

    # -------------------------------------------------------------- lectura
    def _leer(self) -> list[dict]:
        with self.a.tx() as con:
            filas = con.execute(select(inventario_ajuste).order_by(inventario_ajuste.c.actualizado)).all()
        return [dict(f._mapping) for f in filas]

    def _version_actual(self) -> tuple:
        with self.a.tx() as con:
            return tuple(con.execute(select(func.count(), func.max(inventario_ajuste.c.actualizado))
                                     .select_from(inventario_ajuste)).one())

    def sincronizar(self, *, forzar: bool = False) -> bool:
        """Rearma el inventario si cambiaron los ajustes (lo llama cada proceso antes de usarlo)."""
        v = self._version_actual()
        if not forzar and v == self._version:
            return False
        with self._lock:
            self.inv.recargar(combinar(self.base, self._leer()))
            self._version = v
        return True

    def ajustes(self) -> list[dict]:
        return [{**f, "actualizado": f["actualizado"].isoformat() if f.get("actualizado") else None}
                for f in self._leer()]

    def puede_editar(self, login: str) -> bool:
        editores = [e.lower() for e in self.cfg["inventario"].get("editores") or []]
        return not editores or login.lower() in editores

    # -------------------------------------------------------------- escritura
    def _guardar(self, tipo: str, id_: str, operacion: str, datos: dict, por: str) -> None:
        clave = f"{tipo}:{id_.upper() if tipo != 'dominio' else id_}"
        with self.a.tx() as con:
            previo = con.execute(select(inventario_ajuste).where(inventario_ajuste.c.clave == clave)).first()
            if previo and previo.operacion == "alta" and operacion == "cambio":
                operacion, datos = "alta", {**previo.datos, **datos}
            elif previo and previo.operacion == "cambio" and operacion == "cambio":
                datos = {**previo.datos, **datos}
            con.execute(delete(inventario_ajuste).where(inventario_ajuste.c.clave == clave))
            if not (previo and previo.operacion == "alta" and operacion == "baja"):
                con.execute(inventario_ajuste.insert().values(clave=clave, tipo=tipo, id=id_, operacion=operacion,
                                                              datos=datos, por=por, actualizado=utc()))
        self.sincronizar(forzar=True)

    def crear(self, tipo: str, id_: str, datos: dict, por: str) -> dict:
        id_ = _norm_id(tipo, id_)
        if self.inv.activos.get(f"{tipo}:{id_}"):
            raise ErrorInventario(f"ya existe {tipo} {id_}")
        v = validar(tipo, datos, alta=True, inv=self.inv)
        self._guardar(tipo, id_, "alta", v, por)
        return self.inv.activos[f"{tipo}:{id_}"].datos

    def editar(self, tipo: str, id_: str, datos: dict, por: str) -> dict:
        id_ = _norm_id(tipo, id_)
        if not self.inv.activos.get(f"{tipo}:{id_}"):
            raise KeyError(f"no existe {tipo} {id_}")
        v = validar(tipo, datos, alta=False, inv=self.inv)
        if not v:
            raise ErrorInventario("no hay campos para cambiar")
        self._guardar(tipo, id_, "cambio", v, por)
        return self.inv.activos[f"{tipo}:{id_}"].datos

    def eliminar(self, tipo: str, id_: str, por: str) -> None:
        id_ = _norm_id(tipo, id_)
        if not self.inv.activos.get(f"{tipo}:{id_}"):
            raise KeyError(f"no existe {tipo} {id_}")
        if tipo == "servidor":
            dependen = [c for c in self.inv.relacionados(f"servidor:{id_}", 1) if not c.startswith("servidor:")]
            if dependen:
                raise ErrorInventario(f"el servidor tiene {len(dependen)} dominio(s)/base(s) asociados: "
                                      "darlos de baja o moverlos primero")
        self._guardar(tipo, id_, "baja", {}, por)

    def deshacer(self, tipo: str, id_: str) -> bool:
        """Quita el ajuste: el activo vuelve a como viene en el archivo generado."""
        clave = f"{tipo}:{_norm_id(tipo, id_)}"
        with self.a.tx() as con:
            n = con.execute(delete(inventario_ajuste).where(inventario_ajuste.c.clave == clave)).rowcount
        self.sincronizar(forzar=True)
        return bool(n)
