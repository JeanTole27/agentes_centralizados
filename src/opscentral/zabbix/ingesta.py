"""Ingesta desde la API de Zabbix (worker-ingesta, solo lectura).

zbx.problemas  cada 5 min: respaldo del webhook.
  problem.get + trigger.get → los problemas que Ops Central no tiene entran por el mismo
  camino que el webhook (episodio + refresco dirigido + acción sugerida). Los que Ops Central
  tiene abiertos y Zabbix ya no, se cierran como recuperados (se perdió el webhook de OK).

zbx.trends  cada hora: series de capacidad de los servidores del inventario.
  host.get → item.get (catálogo de metricas.py) → trend.get incremental → serie_punto_1h.
  Las series fs: alimentan el modelo de llenado; al terminar se pide un scoring.

Lógica tomada de zabbix-capacity-mcp (colector y alarmas), adaptada al almacén de Ops Central.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import and_, func, select

from .. import estado
from ..almacen import Almacen, evento_zbx, serie_punto, utc
from ..cola import Cola
from ..inventario import Inventario
from . import metricas, procesar
from .cliente import ClienteZabbix
from .webhook import Evento

DIA = 86400


# ====================================================================== cliente
def crear_cliente(cfg, secretos, inventario: Inventario, *, transporte=None) -> ClienteZabbix:
    """URL de la configuración y token de solo lectura desde el gestor de secretos."""
    z = cfg["zabbix"]
    if not z.get("url"):
        raise RuntimeError("falta [zabbix] url en la configuración")
    cred = secretos.leer(z["token_ref"], motivo="lectura de Zabbix")
    return ClienteZabbix(z["url"], cred.get("token"), verificar_tls=z.get("verificar_tls", True),
                         timeout=z.get("timeout_s", 30), user_agent=z.get("user_agent", "OpsCentral/0.1"),
                         transporte=transporte)


# ====================================================================== problemas
def _evento(p: dict, trigger: dict) -> Evento:
    hosts = trigger.get("hosts") or []
    sev = p.get("severity") if p.get("severity") not in (None, "") else trigger.get("priority", 0)
    clock = int(p.get("clock") or time.time())
    return Evento(eventid=str(p["eventid"]), host=(hosts[0]["host"] if hosts else "").strip(),
                  nombre=str(p.get("name") or trigger.get("description") or "")[:500],
                  severidad=max(0, min(int(sev or 0), 5)), problema=True,
                  inicio=datetime.fromtimestamp(clock, timezone.utc),
                  en_mantenimiento=str(p.get("suppressed", "0")) == "1",
                  etiquetas={t["tag"]: t.get("value", "") for t in p.get("tags") or [] if "tag" in t},
                  crudo={"origen": "problem.get", "eventid": p["eventid"], "objectid": p.get("objectid"),
                         "hosts": [h.get("host") for h in hosts], "severity": sev, "clock": clock,
                         "suppressed": p.get("suppressed"), "tags": p.get("tags") or []})


def sincronizar_problemas(almacen: Almacen, cola: Cola, inv: Inventario, cli: ClienteZabbix, cfg) -> dict:
    z = cfg["zabbix"]
    sev_min = int(z.get("severidad_min", 2))
    abiertos = cli.problemas(sev_min)
    trig = {t["triggerid"]: t for t in cli.triggers(sorted({p["objectid"] for p in abiertos if p.get("objectid")}))}
    ids_zbx = {str(p["eventid"]) for p in abiertos}

    with almacen.tx() as con:
        conocidos = {r[0] for r in con.execute(select(evento_zbx.c.eventid))}
        en_ops = [r[0] for r in con.execute(select(evento_zbx.c.eventid).where(and_(
            evento_zbx.c.estado == "PROBLEM", evento_zbx.c.severidad >= sev_min)))]

    nuevos, sugerencias = 0, []
    for p in abiertos:
        if str(p["eventid"]) in conocidos:          # ya llegó por webhook o en una pasada anterior
            continue
        r = procesar.procesar(almacen, cola, inv, _evento(p, trig.get(p.get("objectid"), {})),
                              ventana_min=z["ventana_episodio_min"], refresco_min_s=cfg["bd"]["refresco_min_s"])
        nuevos += 1
        if r.get("sugerencia"):
            sugerencias.append({"evento": p["eventid"], "accion": r["sugerencia"]})

    recuperados = 0
    ahora = utc()
    for eid in en_ops:
        if eid in ids_zbx:
            continue
        with almacen.tx() as con:
            f = con.execute(select(evento_zbx.c.host, evento_zbx.c.nombre, evento_zbx.c.severidad)
                            .where(evento_zbx.c.eventid == eid)).first()
        procesar.procesar(almacen, cola, inv, Evento(
            eventid=eid, host=f.host, nombre=f.nombre, severidad=f.severidad, problema=False, inicio=ahora,
            en_mantenimiento=False, etiquetas={}, crudo={"origen": "problem.get", "recuperado_por": "sincronizacion"}),
            ventana_min=z["ventana_episodio_min"], refresco_min_s=cfg["bd"]["refresco_min_s"])
        recuperados += 1

    sin_inventario = sorted({e.host for e in (_evento(p, trig.get(p.get("objectid"), {})) for p in abiertos)
                             if e.host and inv.por_zabbix(e.host) is None})
    r = {"version": cli.version, "abiertos": len(abiertos), "nuevos": nuevos, "recuperados": recuperados,
         "severidad_min": sev_min, "sugerencias": sugerencias, "hosts_sin_inventario": sin_inventario}
    estado.guardar(almacen, "zbx:problemas", "zbx.problemas", r, origen="zabbix")
    return r


# ====================================================================== capacidad (trends)
def _ultimo_ts(almacen: Almacen, serie: str) -> datetime | None:
    with almacen.tx() as con:
        t = con.execute(select(func.max(serie_punto.c.ts)).where(serie_punto.c.serie == serie)).scalar()
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _ultimo_valor(m: metricas.Metrica, v) -> float | None:
    try:
        return metricas.valor(m, float(v))
    except (TypeError, ValueError):
        return None


def recolectar_capacidad(almacen: Almacen, inv: Inventario, cli: ClienteZabbix, cfg) -> dict:
    z = cfg["zabbix"]
    dias = int(z.get("dias_trends", 30))
    max_items = int(z.get("max_items_host", 40))
    servidores = inv.hosts_zabbix("servidor")
    ahora = int(time.time())

    hosts = cli.hosts(sorted(servidores)) if servidores else []
    por_id = {h["hostid"]: h["host"] for h in hosts}
    encontrados = {h["host"].upper() for h in hosts}
    faltan = sorted(set(servidores) - encontrados)

    # Un ítem por serie: si hay dos (p. ej. mem utilization y pavailable) gana el de menor prioridad
    elegidos: dict[str, tuple[dict, metricas.Metrica]] = {}
    por_host: dict[str, int] = {}
    for it in cli.items_numericos(list(por_id)) if por_id else []:
        host = por_id.get(it["hostid"], "").upper()
        sm = metricas.serie_para(host, it["key_"])
        if not sm:
            continue
        serie, m = sm
        previo = elegidos.get(serie)
        if previo and previo[1].prioridad <= m.prioridad:
            continue
        if not previo:
            if por_host.get(host, 0) >= max_items:
                continue
            por_host[host] = por_host.get(host, 0) + 1
        elegidos[serie] = (it, m)

    series, puntos = [], 0
    for serie, (it, m) in sorted(elegidos.items()):
        ult = _ultimo_ts(almacen, serie)
        desde = ahora - dias * DIA if ult is None else int(ult.timestamp()) + 1
        if desde > ahora:
            continue
        filas = cli.trends([it["itemid"]], desde, ahora)
        pts = {}
        for f in filas:
            try:
                pts[int(f["clock"])] = metricas.valor(m, float(f["value_avg"]))
            except (KeyError, TypeError, ValueError):
                continue
        if pts:
            with almacen.tx() as con:
                con.execute(serie_punto.insert(), [{"serie": serie, "ts": datetime.fromtimestamp(t, timezone.utc),
                                                    "valor": v} for t, v in sorted(pts.items())])
        puntos += len(pts)
        series.append({"serie": serie, "key": it["key_"], "metrica": m.id, "nuevos": len(pts),
                       "ultimo": _ultimo_valor(m, it.get("lastvalue"))})

    r = {"version": cli.version, "servidores": len(servidores), "en_zabbix": len(encontrados),
         "faltan_en_zabbix": faltan, "series": series, "puntos_nuevos": puntos, "dias": dias}
    estado.guardar(almacen, "zbx:capacidad", "zbx.capacidad", r, origen="zabbix")
    return r


def probar(cfg, secretos, inv: Inventario) -> dict:
    """Prueba de conexión (CLI): versión, modo de autenticación y qué hosts del inventario existen."""
    cli = crear_cliente(cfg, secretos, inv)
    try:
        cli.conectar()
        nombres = sorted(inv.hosts_zabbix())
        en_zbx = {h["host"].upper() for h in cli.hosts(nombres)} if nombres else set()
        return {"url": cli.url, "version": cli.version, "autenticacion": cli.modo_auth,
                "hosts_inventario": len(nombres), "encontrados": len(en_zbx),
                "faltan": sorted(set(nombres) - en_zbx)}
    finally:
        cli.cerrar()
