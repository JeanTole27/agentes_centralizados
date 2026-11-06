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
from .inventario import Inventario
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


def crear(contexto: str, cfg: config_mod.Config | None = None, *, almacen: Almacen | None = None) -> Nucleo:
    cfg = cfg or config_mod.cargar()
    alm = almacen or Almacen(cfg.url_almacen())
    alm.crear_esquema()
    aud = Auditoria(alm)
    cat = Catalogo.cargar()
    inv = Inventario.desde_archivo(cfg.ruta("inventario", "archivo"))
    cola = Cola(alm)
    evid = Evidencia(cfg.ruta("evidencia", "ruta"))
    sec = Secretos(crear_proveedor(cfg), contexto, auditar=None)
    ejec = ServicioEjecuciones(cfg, alm, cat, inv, aud, cola, sec, evid)
    return Nucleo(cfg, contexto, alm, cat, inv, aud, cola, evid, sec, ejec)
