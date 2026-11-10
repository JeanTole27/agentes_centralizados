#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Puente Ops Central -> Capa Media Ops.

Compatible con Python 3.6+ y solo librería estándar, igual que cmops: se ejecuta
con el intérprete del motor de Capa Media, en su propio proceso (los dos motores
no se fusionan, 01 §7).

Entrada (stdin, JSON):
  {"ruta_cmops", "accion", "params", "secretos", "persona", "rol", "cambio",
   "llave", "ejecucion"}
Salida (stdout, una línea JSON por evento):
  {"t": "linea", "texto": ...}   ...   {"t": "fin", "estado": ..., "trabajo": ...}
  {"t": "error", "tipo": ..., "mensaje": ...}
"""
import json
import os
import sys
import time


def emitir(obj):
    # JSON en ASCII (escapes \u): no depende de la codificación de la consola de Windows.
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    req = json.loads(sys.stdin.read())
    raiz = req["ruta_cmops"]
    sys.path.insert(0, raiz)
    os.chdir(raiz)
    from cmops.config import cargar
    from cmops.inventario import Inventario
    from cmops.modulos import ErrorValidacion
    from cmops.seguridad import Auditoria
    from cmops.trabajos import Conflicto, GestorTrabajos, PermisoDenegado

    cfg = cargar()
    if req.get("llave"):
        cfg.setdefault("ssh", {})
        cfg["ssh"]["llave"] = req["llave"]       # ssh usa <llave>-cert.pub automáticamente
        cfg["ssh"]["control_master"] = False     # cada ejecución con su certificado
        cfg["ssh"]["verificar_host"] = True      # StrictHostKeyChecking=yes

    inv = Inventario(cfg)
    # Servidores/dominios del inventario sin configuración detallada por módulo: en simulación se completa
    # en memoria (nunca se escribe en configuracion_modulos.json); en modo real se rechaza con instrucciones.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "opscentral_cm_opciones", os.path.join(os.path.dirname(os.path.abspath(__file__)), "opciones.py"))
    opc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(opc)
    try:
        aviso = opc.completar_configuracion(inv.modulos, inv.servidores, req["accion"], req.get("params") or {}, cfg)
    except ValueError as e:
        emitir({"t": "error", "tipo": "ErrorValidacion", "mensaje": str(e)})
        return 2
    if aviso:
        emitir({"t": "linea", "texto": "[ops-central] " + aviso})

    g = GestorTrabajos(cfg, inv, Auditoria())
    sesion = {"usuario": "opscentral:%s#%s" % (req["persona"], req["ejecucion"]), "rol": req["rol"]}
    try:
        meta = g.crear(req["accion"], req.get("params") or {}, req.get("secretos") or {}, sesion,
                       req.get("cambio") or "")
    except (ErrorValidacion, PermisoDenegado, Conflicto) as e:
        emitir({"t": "error", "tipo": type(e).__name__, "mensaje": str(e)})
        return 2
    tid = meta["id"]
    visto = 0
    while True:
        r = g.obtener(tid, visto)
        for l in r["lineas"]:
            emitir({"t": "linea", "texto": l})
        visto += len(r["lineas"])
        if r["meta"]["estado"] not in ("en_cola", "ejecutando"):
            break
        time.sleep(0.3)
    emitir({"t": "fin", "estado": r["meta"]["estado"], "trabajo": tid,
            "evidencia_cmops": "evidencias/trabajos/%s.log" % tid})
    return 0 if r["meta"]["estado"] == "exitoso" else 1


if __name__ == "__main__":
    sys.exit(main())
