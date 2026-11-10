"""Series sintéticas para pruebas del modelo (no forman parte del producto)."""
from datetime import datetime, timedelta, timezone

import numpy as np

from opscentral.ml.llenado import Punto


def serie_sintetica(dias: int = 20, inicio: float = 60.0, pendiente: float = 1.5, ruido: float = 0.4,
                    purgas_cada_h: int | None = None, salto_purga: float = 20.0, semilla: int = 7,
                    fin: datetime | None = None) -> list[Punto]:
    rnd = np.random.default_rng(semilla)
    fin = fin or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    out, v = [], inicio
    n = dias * 24
    for h in range(n):
        v += pendiente / 24 + rnd.normal(0, ruido / 5)
        if purgas_cada_h and h and h % purgas_cada_h == 0:
            v -= salto_purga
        out.append(Punto(fin - timedelta(hours=n - 1 - h), float(min(100.0, v + rnd.normal(0, ruido)))))
    return out
