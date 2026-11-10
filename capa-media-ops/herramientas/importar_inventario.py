# -*- coding: utf-8 -*-
"""Importa el inventario de la linea base (capa_media/config/servers_drp.json)
y genera inventario/servidores.json agrupado por HOST.

- Separa dominios con saltos de linea dentro de la misma celda del Excel.
- Unifica ambientes por host y reporta hosts con IPs distintas.
- No guarda credenciales (el .env de capa_media NO se usa: se recomienda llave SSH).
"""
import json
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from cmops.config import escribir_json, leer_json, ruta  # noqa: E402


def main(origen):
    with open(origen, encoding="utf-8") as f:
        d = json.load(f)
    hosts = {}
    avisos = []
    for s in d.get("servers", []):
        h = (s.get("hostname") or "").strip().upper()
        if not h:
            avisos.append("registro sin hostname: %s" % s.get("alias"))
            continue
        amb = (s.get("environment") or "").strip().lower()
        clave = h
        if h in hosts and s.get("ip") and hosts[h]["ip"] != s["ip"]:
            # mismo hostname con otra IP (p.ej. PROD y DRP con el mismo nombre en el Excel):
            # se registra como host separado <HOST>-<ambiente> para no perder el servidor
            clave = "%s-%s" % (h, amb or "alt")
            if clave not in hosts:
                avisos.append("%s aparece con otra IP (%s, %s) -> registrado como %s" % (h, s["ip"], amb, clave))
        e = hosts.setdefault(clave, {"hostname": clave, "hostname_excel": h, "ip": s.get("ip"),
                                     "ssh_port": s.get("ssh_port") or 22, "ambientes": [], "dominios": [],
                                     "ips_alternas": [], "habilitado": True})
        if s.get("ip") and s["ip"] != e["ip"] and s["ip"] not in e["ips_alternas"]:
            e["ips_alternas"].append(s["ip"])
        if amb and amb not in e["ambientes"]:
            e["ambientes"].append(amb)
        for nom in str(s.get("dominio") or "").replace("\r", "").split("\n"):
            nom = nom.strip()
            if nom and not any(x["nombre"].lower() == nom.lower() and x["ambiente"] == amb for x in e["dominios"]):
                e["dominios"].append({"nombre": nom, "ambiente": amb})
        if s.get("enabled") is False:
            e["habilitado"] = False
    for h, e in hosts.items():
        if len(e["ambientes"]) > 1:
            avisos.append("%s figura en varios ambientes: %s" % (h, ", ".join(e["ambientes"])))
    # conservar datos agregados despues de la importacion (usuario SSH por servidor)
    previo = (leer_json(ruta("inventario", "servidores.json"), {}) or {}).get("servidores", {})
    for h, e in hosts.items():
        if previo.get(h, {}).get("ssh_usuario"):
            e["ssh_usuario"] = previo[h]["ssh_usuario"]
    salida = {
        "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "origen": os.path.basename(origen),
        "origen_importado": d.get("metadata", {}).get("imported_at"),
        "avisos": avisos,
        "servidores": dict(sorted(hosts.items())),
    }
    escribir_json(ruta("inventario", "servidores.json"), salida)
    print("servidores: %d | avisos: %d -> inventario/servidores.json" % (len(hosts), len(avisos)))
    for a in avisos[:30]:
        print("  AVISO: " + a)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: importar_inventario.py <servers_drp.json>")
    main(sys.argv[1])
