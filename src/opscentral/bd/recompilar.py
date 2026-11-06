"""Recompilación acotada a los objetos nuevos por el cambio (02 §4.1).

- Solo objetos que la comparación marcó como `nuevos` (nunca línea base ni BIN$).
- Orden: tipos → paquetes → cuerpos → vistas → procedimientos/funciones → triggers → sinónimos.
- Hasta 3 pasadas para resolver dependencias.
- Cada objeto se compila con ADMINBD.PKG_OPS_CENTRAL.RECOMPILAR_OBJETO (cuenta OPS_DBA);
  el SQL lo arma el paquete con DBMS_ASSERT, no Ops Central.
"""
from __future__ import annotations

from typing import Callable

from .snapshot import clave, es_papelera

ORDEN = ["TYPE", "TYPE BODY", "PACKAGE", "PACKAGE BODY", "VIEW", "MATERIALIZED VIEW", "PROCEDURE", "FUNCTION",
         "TRIGGER", "SYNONYM"]
SENTENCIA = {  # solo para vista previa (bd.inv.generar); la ejecución real la hace el paquete
    "PACKAGE": "ALTER PACKAGE {o}.{n} COMPILE", "PACKAGE BODY": "ALTER PACKAGE {o}.{n} COMPILE BODY",
    "TYPE": "ALTER TYPE {o}.{n} COMPILE", "TYPE BODY": "ALTER TYPE {o}.{n} COMPILE BODY",
    "PROCEDURE": "ALTER PROCEDURE {o}.{n} COMPILE", "FUNCTION": "ALTER FUNCTION {o}.{n} COMPILE",
    "TRIGGER": "ALTER TRIGGER {o}.{n} COMPILE", "VIEW": "ALTER VIEW {o}.{n} COMPILE",
    "MATERIALIZED VIEW": "ALTER MATERIALIZED VIEW {o}.{n} COMPILE", "SYNONYM": "ALTER SYNONYM {o}.{n} COMPILE",
}


def plan(nuevos: list[dict]) -> list[dict]:
    validos = [o for o in nuevos if not es_papelera(o) and o["object_type"].upper() in SENTENCIA]
    validos.sort(key=lambda o: (ORDEN.index(o["object_type"].upper()), o["owner"], o["object_name"]))
    out = []
    for o in validos:
        t = o["object_type"].upper()
        s = (f"ALTER PUBLIC SYNONYM {o['object_name']} COMPILE" if t == "SYNONYM" and o["owner"] == "PUBLIC"
             else SENTENCIA[t].format(o=o["owner"], n=o["object_name"]))
        out.append({**o, "sentencia": s})
    return out


def ejecutar(con, nuevos: list[dict], *, emitir: Callable[[str], None], max_pasadas: int = 3) -> dict:
    pendientes = plan(nuevos)
    permitidos = {clave(o) for o in pendientes}
    resultado: dict[tuple, dict] = {clave(o): {**o, "estado": "INVALID", "pasada": None, "error": None}
                                    for o in pendientes}
    for pasada in range(1, max_pasadas + 1):
        if not pendientes:
            break
        emitir(f"Pasada {pasada}: {len(pendientes)} objeto(s)")
        siguen = []
        for o in pendientes:
            k = clave(o)
            if k not in permitidos:          # defensa: nunca algo fuera del plan
                raise PermissionError(f"objeto fuera del plan: {k}")
            try:
                estado = con.llamar("RECOMPILAR_OBJETO", {"propietario": o["owner"], "objeto": o["object_name"],
                                                          "tipo_objeto": o["object_type"].upper()})
                resultado[k].update(estado=estado, pasada=pasada, error=None)
            except Exception as e:  # el error de un objeto no detiene a los demás
                estado = "ERROR"
                resultado[k].update(estado="ERROR", pasada=pasada, error=str(e)[:500])
            emitir(f"  {o['object_type']} {o['owner']}.{o['object_name']} → {estado}")
            if estado != "VALID" and estado != "ERROR":
                siguen.append(o)
        pendientes = siguen
    filas = list(resultado.values())
    return {"objetos": filas, "validos": sum(1 for f in filas if f["estado"] == "VALID"),
            "siguen_invalidos": sum(1 for f in filas if f["estado"] != "VALID")}
