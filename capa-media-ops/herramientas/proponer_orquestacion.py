# -*- coding: utf-8 -*-
"""Propone las instalaciones WebLogic de un host a partir de lo DESCUBIERTO por el
modulo de recoleccion (inventario/hechos/<HOST>.json).

Agrupa los dominios por (propietario en el SO, MW_HOME): cada grupo es una
instalacion con su propio usuario_so. Asi no se asume ningun usuario fijo.

  python herramientas/proponer_orquestacion.py HOST            -> muestra la propuesta
  python herramientas/proponer_orquestacion.py HOST --aplicar  -> la guarda

Al aplicar:
  - las instalaciones nuevas quedan marcadas "por_validar": true
  - en instalaciones existentes solo se AGREGAN dominios que falten (no se pisa lo
    validado a mano: admin_url, nm_host, trust_keystore, servers...)
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from cmops.config import escribir_json, leer_json, nombre_seguro, ruta  # noqa: E402


def proponer(hechos):
    grupos = {}
    for d in (hechos or {}).get("weblogic", []):
        prop = d.get("propietario") or ""
        if not prop or not d.get("ruta"):
            continue
        mw = d.get("mw_home") or d["ruta"].split("/user_projects/")[0]
        grupos.setdefault((prop, mw), []).append(d)
    insts = []
    usados = {}
    for (prop, mw), doms in sorted(grupos.items()):
        usados[prop] = usados.get(prop, 0) + 1
        iid = prop if usados[prop] == 1 else "%s-%d" % (prop, usados[prop])
        base = os.path.dirname(doms[0]["ruta"]) if all(
            os.path.dirname(x["ruta"]) == os.path.dirname(doms[0]["ruta"]) for x in doms) else "${MW_HOME}/user_projects/domains"
        home = mw.split("/Oracle/Middleware")[0].split("/Middleware")[0]
        insts.append({
            "id": iid, "usuario_so": prop, "mw_home": mw, "domains_base": base,
            "java_home": "", "wlst": "${MW_HOME}/oracle_common/common/bin/wlst.sh",
            "nm_host": "", "trust_keystore": "", "ruta": home + "/wl-orquestacion",
            "nm_usuario": "", "admin_usuario": "", "por_validar": True,
            "nota": "Propuesta automatica desde la recoleccion. Validar nm_host, admin_url, java_home y trust_keystore.",
            "dominios": [{
                "nombre": d["dominio"], "dir": os.path.basename(d["ruta"]), "domain_name": d["dominio"],
                "admin_name": d.get("admin_name") or "AdminServer", "admin_url": "",
                "nm_port": d.get("nm_port", ""), "nm_type": d.get("nm_tipo") or "plain",
                "servers": d.get("managed_servers", []), "propietario": prop,
            } for d in doms],
        })
    return insts


def main(host, aplicar=False):
    host = nombre_seguro(host.upper(), "host")
    hechos = leer_json(ruta("inventario", "hechos", host + ".json"), None)
    if not hechos:
        sys.exit("ERROR: no hay hechos de %s. Ejecute primero 'Recolectar informacion' sobre el host." % host)
    insts = proponer(hechos)
    if not insts:
        sys.exit("ERROR: la recoleccion de %s no encontro dominios con propietario identificado." % host)
    for i in insts:
        print("instalacion %-12s usuario_so=%-10s MW_HOME=%s" % (i["id"], i["usuario_so"], i["mw_home"]))
        for d in i["dominios"]:
            print("    %-24s NM %s/%s  MS: %s" % (d["nombre"], d["nm_port"] or "?", d["nm_type"], " ".join(d["servers"]) or "-"))
    if not aplicar:
        print("\n(sin cambios; use --aplicar para guardarla en inventario/configuracion_modulos.json)")
        return insts
    p = ruta("inventario", "configuracion_modulos.json")
    cm = leer_json(p, {}) or {}
    h = cm.setdefault("orquestacion", {}).setdefault("hosts", {}).setdefault(host, {})
    h.setdefault("instalaciones", [])
    actuales = {i.get("id"): i for i in h["instalaciones"]}
    for nueva in insts:
        # la misma instalacion puede existir con otro id: se compara por usuario y MW_HOME
        ex = actuales.get(nueva["id"]) or next(
            (i for i in h["instalaciones"] if i.get("usuario_so") == nueva["usuario_so"]
             and (not i.get("mw_home") or i.get("mw_home") == nueva["mw_home"])), None)
        if not ex:
            h["instalaciones"].append(nueva)
            print("+ instalacion nueva %s (por validar)" % nueva["id"])
            continue
        for k in ("mw_home", "domains_base"):
            if not ex.get(k):
                ex[k] = nueva[k]
        nombres = set(d["nombre"] for d in ex.get("dominios", []))
        agregados = [d for d in nueva["dominios"] if d["nombre"] not in nombres]
        ex.setdefault("dominios", []).extend(agregados)
        if agregados:
            ex["por_validar"] = True
        print("~ instalacion %s: %d dominio(s) agregado(s)" % (ex["id"], len(agregados)))
    escribir_json(p, cm)
    print("guardado en %s" % p)
    return insts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: proponer_orquestacion.py HOST [--aplicar]")
    main(sys.argv[1], "--aplicar" in sys.argv[2:])
