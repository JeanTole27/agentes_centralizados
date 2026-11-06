"""Snapshot ANTES / DESPUÉS de objetos inválidos y comparación con la línea base (F2).

Regla central: solo son responsabilidad del cambio los objetos que
  - están inválidos DESPUÉS,
  - no estaban inválidos ANTES,
  - no están en la línea base (ADMINBD.TBL_BASE_OBJETOS_INVALIDOS),
  - y no son de la papelera (BIN$).
Solo esos se pueden recompilar desde Ops Central.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

Clave = tuple[str, str, str]


def clave(o: dict) -> Clave:
    # Nombres tal como los devuelve Oracle: los BIN$ y los identificadores entre comillas
    # distinguen mayúsculas, así que no se normalizan (solo el tipo).
    return (str(o["owner"]), str(o["object_name"]), str(o["object_type"]).upper())


def es_papelera(o: dict) -> bool:
    return str(o["object_name"]).startswith("BIN$")


def armar(base: str, cambio: str, momento: str, invalidos: list[dict], extra: dict | None = None) -> dict:
    objetos = sorted(({"owner": k[0], "object_name": k[1], "object_type": k[2]} for k in {clave(o) for o in invalidos}),
                     key=lambda o: (o["owner"], o["object_type"], o["object_name"]))
    datos = {"base": base, "cambio": cambio, "momento": momento,
             "tomado": datetime.now(timezone.utc).isoformat(), "invalidos": objetos, "total": len(objetos),
             "extra": extra or {}}
    datos["huella"] = hashlib.sha256(json.dumps(objetos, sort_keys=True).encode()).hexdigest()
    return datos


def comparar(antes: dict, despues: dict, linea_base: list[dict]) -> dict:
    a = {clave(o): o for o in antes["invalidos"]}
    d = {clave(o): o for o in despues["invalidos"]}
    lb = {clave(o) for o in linea_base}

    nuevos, en_lb, papelera = [], [], []
    for k, o in sorted(d.items()):
        if k in a:
            continue
        if es_papelera(o):
            papelera.append(o)
        elif k in lb:
            en_lb.append(o)
        else:
            nuevos.append(o)
    corregidos = [o for k, o in sorted(a.items()) if k not in d]
    persistentes = [o for k, o in sorted(d.items()) if k in a]
    veredicto = "sin_nuevos" if not nuevos else "nuevos_por_cambio"
    return {
        "base": despues["base"], "cambio": despues["cambio"],
        "antes": {"tomado": antes["tomado"], "total": antes["total"]},
        "despues": {"tomado": despues["tomado"], "total": despues["total"], "momento": despues["momento"]},
        "nuevos": nuevos, "corregidos": corregidos, "persistentes": persistentes,
        "en_linea_base": en_lb, "papelera_excluidos": papelera,
        "resumen": {"nuevos": len(nuevos), "corregidos": len(corregidos), "persistentes": len(persistentes),
                    "en_linea_base": len(en_lb), "papelera": len(papelera)},
        "veredicto": veredicto,
    }
