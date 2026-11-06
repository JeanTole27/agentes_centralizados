"""M3 · Anomalías frente a la línea normal por hora de la semana.

Para cada métrica (sesiones activas, AAS, redo por hora, conexiones de datasource,
stuck threads...) se compara el valor actual con la mediana y la MAD del mismo
día y hora en las semanas anteriores. Puntaje robusto: z = 0,6745·(x − mediana)/MAD.
Así un lunes 09:00 no se compara con un domingo 03:00.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def evaluar(serie: str, historia: list[tuple[datetime, float]], actual: tuple[datetime, float], *,
            umbral_z: float = 3.5, min_semanas: int = 3) -> dict:
    ts, x = actual
    misma_hora = [v for t, v in historia if t.weekday() == ts.weekday() and t.hour == ts.hour and t < ts]
    if len(misma_hora) < min_semanas:
        return {"serie": serie, "estado": "datos_insuficientes", "muestras": len(misma_hora)}
    arr = np.array(misma_hora, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    escala = mad if mad > 0 else max(abs(med) * 0.05, 1e-6)
    z = 0.6745 * (x - med) / escala
    veces = (x / med) if med else None
    anomalo = abs(z) >= umbral_z
    momento = f"un {DIAS[ts.weekday()]} {ts:%H}:00"
    if veces is not None and veces >= 1:
        texto = f"{veces:.1f} veces lo normal para {momento}".replace(".", ",")
    elif veces is not None:
        texto = f"{(1 - veces) * 100:.0f} % por debajo de lo normal para {momento}"
    else:
        texto = f"valor {x:g} (lo normal para {momento} es 0)"
    return {"serie": serie, "estado": "ok", "anomalo": anomalo, "z": round(float(z), 2), "valor": x,
            "normal": med, "mad": mad, "veces": None if veces is None else round(veces, 2),
            "muestras": len(misma_hora), "explicacion": texto, "modelo": "M3-hora-semana-v1"}
