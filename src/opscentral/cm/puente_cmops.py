#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Puente Ops Central -> Capa Media Ops.

Compatible con Python 3.6+ y solo librería estándar, igual que cmops: se ejecuta
con el intérprete del motor de Capa Media, en su propio proceso (los dos motores
no se fusionan, 01 §7).

Entrada (stdin, JSON):
  {"ruta_cmops", "accion", "params", "secretos", "persona", "rol", "cambio",
   "simulacion", "llave", "ejecucion"}
Salida (stdout, una línea JSON por evento):
  {"t": "linea", "texto": ...}   ...   {"t": "fin", "estado": ..., "trabajo": ...}
  {"t": "error", "tipo": ..., "mensaje": ...}
"""
import json
import os
import sys
import time


def emitir(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
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
    # Ops Central puede FORZAR simulación, nunca quitarla: si cmops está en simulación, sigue así.
    if req.get("simulacion"):
        cfg["modo_simulacion"] = True
    if req.get("llave"):
        cfg.setdefault("ssh", {})
        cfg["ssh"]["llave"] = req["llave"]       # ssh usa <llave>-cert.pub automáticamente
        cfg["ssh"]["control_master"] = False     # cada ejecución con su certificado
        cfg["ssh"]["verificar_host"] = True      # StrictHostKeyChecking=yes

    g = GestorTrabajos(cfg, Inventario(cfg), Auditoria())
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
