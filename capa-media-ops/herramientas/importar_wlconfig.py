# -*- coding: utf-8 -*-
"""Convierte un wl-config.sh existente (hecho a mano en un servidor) en la entrada
orquestacion.hosts.<HOST> de inventario/configuracion_modulos.json.

A partir de ahi, el wl-config.sh del servidor se GENERA desde el inventario central.
"""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from cmops.config import escribir_json, leer_json, ruta  # noqa: E402

CLAVES = {"DIR": "dir", "DOMAIN_NAME": "domain_name", "ADMIN_NAME": "admin_name", "ADMIN_URL": "admin_url",
          "NM_PORT": "nm_port", "NM_TYPE": "nm_type", "SERVERS": "servers"}
EXPORTS = {"MW_HOME": "mw_home", "DOMAINS_BASE": "domains_base", "JAVA_HOME": "java_home", "WLST": "wlst",
           "NM_HOST": "nm_host", "TRUST_KEYSTORE": "trust_keystore", "WL_ORQ_HOME": "ruta"}


def parsear(texto):
    oc = {}
    for m in re.finditer(r'^export\s+([A-Z_]+)="([^"]*)"', texto, re.M):
        if m.group(1) in EXPORTS:
            oc[EXPORTS[m.group(1)]] = m.group(2)
    m = re.search(r"^DOMAINS=\((.*?)\)", texto, re.M | re.S)
    nombres = [x for x in re.split(r"\s+", m.group(1)) if x and not x.startswith("#")] if m else []
    doms = {n: {"nombre": n} for n in nombres}
    for m in re.finditer(r'^DOM_([A-Z_]+)\[([^\]]+)\]="([^"]*)"', texto, re.M):
        k, n, v = m.groups()
        if n in doms and k in CLAVES:
            doms[n][CLAVES[k]] = v.split() if k == "SERVERS" else v
    oc["dominios"] = [doms[n] for n in nombres]
    return oc


def main(archivo, host, usuario_so, ssh_ip=None, instalacion=None):
    """Agrega/reemplaza UNA instalacion (identificada por su id, por defecto el usuario_so)
    dentro de orquestacion.hosts.<HOST>.instalaciones. Otras instalaciones del host se conservan."""
    with open(archivo, encoding="utf-8") as f:
        inst = parsear(f.read())
    inst = dict([("id", instalacion or usuario_so), ("usuario_so", usuario_so)] + list(inst.items()))
    p = ruta("inventario", "configuracion_modulos.json")
    cm = leer_json(p, {}) or {}
    hosts = cm.setdefault("orquestacion", {}).setdefault("hosts", {})
    h = hosts.setdefault(host.upper(), {})
    if "instalaciones" not in h:          # formato antiguo -> nuevo
        h.clear()
        h["instalaciones"] = []
    if ssh_ip:
        h["ssh_ip"] = ssh_ip
    h["instalaciones"] = [i for i in h["instalaciones"] if i.get("id") != inst["id"]] + [inst]
    escribir_json(p, cm)
    print("host %s / instalacion %s (usuario %s): %d dominios, %d managed servers -> %s" % (
        host.upper(), inst["id"], usuario_so, len(inst["dominios"]),
        sum(len(d.get("servers", [])) for d in inst["dominios"]), p))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("uso: importar_wlconfig.py <wl-config.sh> <HOST> <usuario_so> [ssh_ip] [instalacion]")
    main(*sys.argv[1:6])
