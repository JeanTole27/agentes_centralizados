"""Arma los componentes de Ops Central para un contexto (api, worker-bd, worker-cm...)."""
from __future__ import annotations

from dataclasses import dataclass

from . import config as config_mod
from .almacen import Almacen
from .auditoria import Auditoria
from .catalogo import Catalogo
from .cola import Cola
from .ejecuciones import ServicioEjecuciones
from .evidencia import Evidencia
import json

from .inventario import Inventario
from .inventario_ajustes import ServicioInventario
from .secretos import Secretos, crear_proveedor


@dataclass
class Nucleo:
    cfg: config_mod.Config
    contexto: str
    almacen: Almacen
    catalogo: Catalogo
    inventario: Inventario
    auditoria: Auditoria
    cola: Cola
    evidencia: Evidencia
    secretos: Secretos
    ejecuciones: ServicioEjecuciones
    inv_ajustes: ServicioInventario | None = None


def crear(contexto: str, cfg: config_mod.Config | None = None, *, almacen: Almacen | None = None) -> Nucleo:
    cfg = cfg or config_mod.cargar()
    alm = almacen or Almacen(cfg.url_almacen())
    alm.crear_esquema()
    aud = Auditoria(alm)
    cat = Catalogo.cargar()
    base_inv = json.loads(cfg.ruta("inventario", "archivo").read_text(encoding="utf-8"))
    inv = Inventario(base_inv)
    ajustes = ServicioInventario(cfg, alm, inv, base_inv)   # aplica los ajustes guardados (CRUD) sobre el archivo
    cola = Cola(alm)
    evid = Evidencia(cfg.ruta("evidencia", "ruta"))
    sec = Secretos(crear_proveedor(cfg), contexto, auditar=None)
    ejec = ServicioEjecuciones(cfg, alm, cat, inv, aud, cola, sec, evid)
    return Nucleo(cfg, contexto, alm, cat, inv, aud, cola, evid, sec, ejec, ajustes)
