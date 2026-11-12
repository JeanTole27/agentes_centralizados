"""F10 · Evento de Zabbix → episodio → refresco dirigido → acción sugerida.

Zabbix ya notificó a las personas; Ops Central NO vuelve a notificar. Lo que hace:
1. Guarda el evento y lo agrupa en un episodio.
2. Pide un refresco dirigido: SOLO de la sección que indica la alerta (bloqueos, tablespaces,
   inválidos, ASM o instancia) y SOLO de las bases afectadas. Así la salud, los bloqueos y los
   inválidos se actualizan a partir de las alertas, sin barrer toda la flota cada 15 min.
3. Sugiere la acción del catálogo que corresponde, para ejecutarla con un clic.

¿Qué bases afecta una alerta?
- El host de Zabbix es la base (zabbix_host de la base): esa base.
- El host es el servidor de BD (lo normal): las bases de ese servidor cuyo id, alias o servicio
  aparezcan en el nombre o las etiquetas del trigger; si ninguna aparece, todas las del servidor
  (con tope [bd] max_bases_por_evento).
También se refresca en la recuperación, para que el dato deje de mostrar el problema resuelto.
"""
from __future__ import annotations

from ..almacen import Almacen
from ..cola import Cola
from ..inventario import Activo, Inventario
from ..limites import pedir_refresco
from . import episodios
from .webhook import Evento

SUGERENCIAS = [  # (palabra en el nombre del trigger, tipo de activo, acción del catálogo)
    ("lock", "base", "bd.ses.bloqueos"), ("bloqueo", "base", "bd.ses.bloqueos"),
    ("blocking", "base", "bd.ses.bloqueos"),
    ("tablespace", "base", "bd.salud.tablespaces"), ("diskgroup", "base", "bd.asm.diskgroups"),
    ("asm", "base", "bd.asm.diskgroups"), ("invalid", "base", "bd.inv.listar"),
    ("stuck", "servidor", "cm.stuck_threads"), ("heap", "servidor", "orq.status"),
    ("frmweb", "servidor", "frm.validar"), ("down", "servidor", "orq.status"),
]

# palabra del trigger → secciones de la recolección que hay que releer (además de la instancia)
SECCIONES = [
    (("lock", "bloque", "blocking", "enq:", "deadlock"), ["bloqueos"]),
    (("tablespace", "ts_", " ts "), ["tablespaces"]),
    (("diskgroup", "asm"), ["diskgroups"]),
    (("invalid", "inválid", "invalid"), ["invalidos"]),
]


def sugerir(ev: Evento, tipo_activo: str | None) -> str | None:
    n = ev.nombre.lower()
    for palabra, tipo, accion in SUGERENCIAS:
        if palabra in n and (tipo_activo in (None, "servidor_bd") and tipo == "base" or tipo == tipo_activo):
            return accion
    if tipo_activo in ("base", "servidor_bd"):
        return "bd.salud.instancia"
    return "orq.status" if tipo_activo == "servidor" else None


def secciones_de(ev: Evento) -> list[str]:
    """Secciones a releer según el trigger. Sin coincidencia: solo la instancia (consulta barata)."""
    texto = (ev.nombre + " " + " ".join(f"{k} {v}" for k, v in ev.etiquetas.items())).lower()
    out: list[str] = []
    for palabras, secs in SECCIONES:
        if any(p in texto for p in palabras):
            out += [s for s in secs if s not in out]
    return out


def bases_afectadas(ev: Evento, activo: Activo | None, inv: Inventario, maximo: int) -> list[Activo]:
    if not activo:
        return []
    if activo.tipo == "base":
        return [activo]
    if activo.tipo != "servidor":
        return []
    bases = inv.bases_en(activo.id)
    if len(bases) <= 1:
        return bases
    texto = (ev.nombre + " " + " ".join(f"{k} {v}" for k, v in ev.etiquetas.items())).upper()
    nombradas = [b for b in bases if any(str(x).upper() in texto for x in
                                         (b.id, b.datos.get("alias"), b.datos.get("servicio")) if x and len(str(x)) >= 3)]
    return (nombradas or bases)[:maximo]


def procesar(almacen: Almacen, cola: Cola, inv: Inventario, ev: Evento, *, ventana_min: int, refresco_min_s: int,
             max_bases: int = 10) -> dict:
    with almacen.tx() as con:
        r = episodios.registrar(con, ev, inv, ventana_min)
    activo = inv.por_zabbix(ev.host)
    bases = bases_afectadas(ev, activo, inv, max_bases)
    tipo = activo.tipo if activo else None
    if tipo == "servidor" and bases:
        tipo = "servidor_bd"
    r["sugerencia"] = sugerir(ev, tipo)
    if bases and not ev.en_mantenimiento:
        secs = secciones_de(ev)
        etiqueta = "+".join(secs) or "instancia"
        por_base = {b.id: pedir_refresco(
            almacen, cola, f"bd:{b.id}:{etiqueta}", minimo_s=refresco_min_s, cola_destino="bd",
            payload={"base": b.id, "secciones": secs, "motivo": f"zabbix:{ev.eventid}"}) for b in bases}
        encolado = any(x["estado"] == "encolado" for x in por_base.values())
        r["refresco"] = {"estado": "encolado" if encolado else "reciente", "secciones": secs, "bases": por_base}
    return r
