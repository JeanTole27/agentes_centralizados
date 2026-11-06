"""Semáforo de salud por base (mapa de calor de la flota)."""
from __future__ import annotations

UMBRALES = {"ts_amarillo": 85.0, "ts_rojo": 95.0, "dg_amarillo": 85.0, "dg_rojo": 92.0, "procesos_amarillo": 0.8}
NIVEL = {"verde": 0, "amarillo": 1, "rojo": 2, "sin_dato": 3}


def evaluar(instancia: dict | None, tablespaces: list[dict], diskgroups: list[dict], *,
            bloqueadas: int = 0, invalidos_nuevos: int = 0, u: dict = UMBRALES) -> dict:
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
        if p >= u["ts_rojo"]:
            motivos.append(("rojo", f"tablespace {t['tablespace_name']} al {p:.0f} %"))
        elif p >= u["ts_amarillo"]:
            motivos.append(("amarillo", f"tablespace {t['tablespace_name']} al {p:.0f} %"))
    for d in diskgroups:
        p = float(d.get("pct_usado") or 0)
        if p >= u["dg_rojo"]:
            motivos.append(("rojo", f"diskgroup {d['name']} al {p:.0f} %"))
        elif p >= u["dg_amarillo"]:
            motivos.append(("amarillo", f"diskgroup {d['name']} al {p:.0f} %"))
    if bloqueadas >= 5:
        motivos.append(("rojo", f"{bloqueadas} sesiones bloqueadas"))
    elif bloqueadas:
        motivos.append(("amarillo", f"{bloqueadas} sesiones bloqueadas"))
    if invalidos_nuevos:
        motivos.append(("amarillo", f"{invalidos_nuevos} objetos inválidos fuera de la línea base"))
    semaforo = max((m[0] for m in motivos), key=lambda x: NIVEL[x], default="verde")
    return {"semaforo": semaforo, "motivos": [m[1] for m in motivos]}
