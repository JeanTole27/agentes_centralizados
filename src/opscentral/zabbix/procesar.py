"""F10 · Evento de Zabbix → episodio → refresco dirigido → acción sugerida.

Zabbix ya notificó a las personas; Ops Central NO vuelve a notificar. Lo que hace:
1. Guarda el evento y lo agrupa en un episodio.
2. Pide un refresco dirigido del activo (con el límite de 1 cada 2 min).
3. Sugiere la acción del catálogo que corresponde, para ejecutarla con un clic.
"""
from __future__ import annotations

from ..almacen import Almacen
from ..cola import Cola
from ..inventario import Inventario
from ..limites import pedir_refresco
from . import episodios
from .webhook import Evento

SUGERENCIAS = [  # (palabra en el nombre del trigger, tipo de activo, acción del catálogo)
    ("lock", "base", "bd.ses.bloqueos"), ("bloqueo", "base", "bd.ses.bloqueos"),
    ("tablespace", "base", "bd.salud.tablespaces"), ("diskgroup", "base", "bd.asm.diskgroups"),
    ("asm", "base", "bd.asm.diskgroups"), ("invalid", "base", "bd.inv.listar"),
    ("stuck", "servidor", "cm.stuck_threads"), ("heap", "servidor", "orq.status"),
    ("frmweb", "servidor", "frm.validar"), ("down", "servidor", "orq.status"),
]


def sugerir(ev: Evento, tipo_activo: str | None) -> str | None:
    n = ev.nombre.lower()
    for palabra, tipo, accion in SUGERENCIAS:
        if palabra in n and (tipo_activo is None or tipo == tipo_activo):
            return accion
    return "bd.salud.instancia" if tipo_activo == "base" else "orq.status" if tipo_activo == "servidor" else None


def procesar(almacen: Almacen, cola: Cola, inv: Inventario, ev: Evento, *, ventana_min: int, refresco_min_s: int) -> dict:
    with almacen.tx() as con:
        r = episodios.registrar(con, ev, inv, ventana_min)
    activo = inv.por_zabbix(ev.host)
    r["sugerencia"] = sugerir(ev, activo.tipo if activo else None)
    if ev.problema and activo and activo.tipo == "base" and not ev.en_mantenimiento:
        r["refresco"] = pedir_refresco(almacen, cola, f"bd:{activo.id}", minimo_s=refresco_min_s, cola_destino="bd",
                                       payload={"base": activo.id, "motivo": f"zabbix:{ev.eventid}"})
    return r
