"""Semáforo de salud por base (mapa de calor de la flota)."""
from __future__ import annotations

import re

UMBRALES = {"ts_amarillo": 85.0, "ts_rojo": 95.0, "dg_amarillo": 85.0, "dg_rojo": 92.0, "procesos_amarillo": 0.8,
            # protección (solo se evalúa si la sección se leyó)
            "fra_amarillo": 85.0, "fra_rojo": 95.0,
            "respaldo_bd_h": 36.0,        # último DB FULL / DB INCR correcto
            "respaldo_arch_h": 8.0,       # último ARCHIVELOG correcto
            "dg_lag_amarillo_s": 1800, "dg_lag_rojo_s": 7200}
NIVEL = {"verde": 0, "amarillo": 1, "rojo": 2, "sin_dato": 3}


def segundos_intervalo(valor) -> float | None:
    """'+00 01:02:03' (V$DATAGUARD_STATS) -> 3723.0."""
    if valor is None:
        return None
    m = re.match(r"\s*\+?(\d+)\s+(\d+):(\d+):(\d+)", str(valor))
    if not m:
        return None
    d, h, mi, s = (int(x) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def evaluar_proteccion(proteccion: dict | None, *, exigir_respaldo: bool, u: dict = UMBRALES) -> list[tuple[str, str]]:
    """FRA, respaldos RMAN y Data Guard. `proteccion` = {"fra": [...], "respaldo": [...], "dataguard": {...}};
    una clave ausente es una sección que no se leyó y no se evalúa."""
    p = proteccion or {}
    motivos: list[tuple[str, str]] = []
    for f in p.get("fra") or []:
        if not f.get("name") or f.get("pct_usado") is None:
            continue
        pct = float(f["pct_usado"])
        if pct >= u["fra_rojo"]:
            motivos.append(("rojo", f"FRA al {pct:.0f} % (sin lo recuperable)"))
        elif pct >= u["fra_amarillo"]:
            motivos.append(("amarillo", f"FRA al {pct:.0f} % (sin lo recuperable)"))
    if "respaldo" in p and exigir_respaldo:
        ult = {str(r.get("input_type") or "").upper(): r.get("horas") for r in p.get("respaldo") or []}
        bd = [float(h) for t, h in ult.items() if t.startswith("DB") and h is not None]
        if not bd:
            motivos.append(("rojo", "sin respaldo RMAN de la base en 30 días"))
        elif min(bd) > u["respaldo_bd_h"]:
            motivos.append(("amarillo", f"último respaldo de la base hace {min(bd):.0f} h"))
        arch = ult.get("ARCHIVELOG")
        if arch is not None and float(arch) > u["respaldo_arch_h"]:
            motivos.append(("amarillo", f"último respaldo de archivelogs hace {float(arch):.0f} h"))
    dg = p.get("dataguard") or {}
    for d in dg.get("destinos") or []:
        nombre = d.get("dest_name") or f"destino {d.get('dest_id')}"
        if str(d.get("status") or "").upper() not in ("VALID", "DEFERRED") or d.get("error"):
            motivos.append(("rojo", f"standby {nombre} con error: {(d.get('error') or d.get('status') or '')[:80]}"))
        elif d.get("gap_status") and str(d["gap_status"]).upper() not in ("NO GAP", ""):
            motivos.append(("amarillo", f"standby {nombre}: {d['gap_status']}"))
    for nombre, valor in (dg.get("lag") or {}).items():
        s = segundos_intervalo(valor)
        if s is None:
            continue
        if s >= u["dg_lag_rojo_s"]:
            motivos.append(("rojo", f"Data Guard: {nombre} de {s / 60:.0f} min"))
        elif s >= u["dg_lag_amarillo_s"]:
            motivos.append(("amarillo", f"Data Guard: {nombre} de {s / 60:.0f} min"))
    return motivos


def evaluar(instancia: dict | None, tablespaces: list[dict], diskgroups: list[dict], *,
            bloqueadas: int = 0, invalidos_nuevos: int = 0, proteccion: dict | None = None,
            exigir_respaldo: bool = False, u: dict = UMBRALES) -> dict:
    if not instancia:
        return {"semaforo": "sin_dato", "motivos": ["sin respuesta de la base"]}
    motivos: list[tuple[str, str]] = []
    if instancia.get("status") != "OPEN":
        motivos.append(("rojo", f"instancia en estado {instancia.get('status')}"))
    procesos = instancia.get("procesos") or 0
    if procesos and instancia.get("sesiones", 0) / procesos >= u["procesos_amarillo"]:
        motivos.append(("amarillo", "sesiones cerca del límite de procesos"))
    for t in tablespaces:
        p = float(t.get("pct_usado") or 0)
        tipo = {"UNDO": " (UNDO activo)", "TEMPORARY": " (TEMP en uso)"}.get(str(t.get("tipo") or "").upper(), "")
        if p >= u["ts_rojo"]:
            motivos.append(("rojo", f"tablespace {t['tablespace_name']}{tipo} al {p:.0f} %"))
        elif p >= u["ts_amarillo"]:
            motivos.append(("amarillo", f"tablespace {t['tablespace_name']}{tipo} al {p:.0f} %"))
    for d in diskgroups:
        p = float(d.get("pct_usado") or 0)
        if d.get("usable_file_mb") is not None and float(d["usable_file_mb"]) < 0:
            motivos.append(("rojo", f"diskgroup {d['name']} sin espacio para reconstruir el espejo "
                                    f"(usable {float(d['usable_file_mb']):.0f} MB)"))
        elif p >= u["dg_rojo"]:
            motivos.append(("rojo", f"diskgroup {d['name']} al {p:.0f} % de lo utilizable"))
        elif p >= u["dg_amarillo"]:
            motivos.append(("amarillo", f"diskgroup {d['name']} al {p:.0f} % de lo utilizable"))
    prot = evaluar_proteccion(proteccion, exigir_respaldo=exigir_respaldo, u=u)
    motivos += prot
    if bloqueadas >= 5:
        motivos.append(("rojo", f"{bloqueadas} sesiones bloqueadas"))
    elif bloqueadas:
        motivos.append(("amarillo", f"{bloqueadas} sesiones bloqueadas"))
    if invalidos_nuevos:
        motivos.append(("amarillo", f"{invalidos_nuevos} objetos inválidos fuera de la línea base"))
    semaforo = max((m[0] for m in motivos), key=lambda x: NIVEL[x], default="verde")
    # semáforo propio de la protección (FRA, RMAN, Data Guard) para la columna de la flota; None = no se leyó
    sem_prot = None if not proteccion else max((m[0] for m in prot), key=lambda x: NIVEL[x], default="verde")
    return {"semaforo": semaforo, "motivos": [m[1] for m in motivos], "proteccion_semaforo": sem_prot}
