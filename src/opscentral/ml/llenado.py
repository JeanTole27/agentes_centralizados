"""M1 · Tiempo hasta llenarse (filesystem, diskgroup ASM, tablespace, FRA) y M2 · heap JVM.

Método (versión 1, robusta y explicable):
1. Se detectan purgas (caídas bruscas). Con 2 o más en la ventana la serie es
   "diente de sierra" (FRA, archivelog, logs) y se modela pico a pico.
   Con una sola, se usa el tramo posterior a la última purga.
2. Se agrega a bloques de 6 h tomando el máximo (conservador para capacidad)
   y se ajusta una pendiente de Theil-Sen (mediana de pendientes por pares).
3. Si la pendiente de los últimos 3 días difiere más de 50 % de la de la ventana,
   hay cambio de tendencia y se usa la reciente.
4. Banda P10–P90: intervalo de Sen para la pendiente (80 %).
5. Base de comparación: timeleft() de Zabbix = mínimos cuadrados lineales sobre la serie horaria.
M2 usa el mínimo diario (heap después del GC): si el piso sube, hay fuga.
statsforecast (ETS estacional) entra como segundo candidato cuando haya backtest (E4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

VERSION = "M1-theilsen-v1"
Z80 = 1.2816  # cuantil normal para un intervalo de 80 % (P10–P90)


@dataclass
class Punto:
    ts: datetime
    valor: float


def _dias(ts: list[datetime]) -> np.ndarray:
    t0 = ts[0]
    return np.array([(t - t0).total_seconds() / 86400 for t in ts])


def theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """(pendiente, intercepto, pendiente_baja, pendiente_alta) con el intervalo de Sen al 80 %."""
    n = len(x)
    i, j = np.triu_indices(n, k=1)
    dx = x[j] - x[i]
    ok = dx > 0
    pend = np.sort((y[j] - y[i])[ok] / dx[ok])
    N = len(pend)
    if N == 0:
        return 0.0, float(np.median(y)), 0.0, 0.0
    m = float(np.median(pend))
    b = float(np.median(y - m * x))
    c = Z80 * np.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    lo = int(np.clip(np.floor((N - c) / 2), 0, N - 1))
    hi = int(np.clip(np.ceil((N + c) / 2), 0, N - 1))
    return m, b, float(pend[lo]), float(pend[hi])


def _bloques(ts: list[datetime], y: np.ndarray, horas: int, func) -> tuple[list[datetime], np.ndarray]:
    grupos: dict[datetime, list[float]] = {}
    for t, v in zip(ts, y):
        k = t.replace(minute=0, second=0, microsecond=0)
        k = k - timedelta(hours=k.hour % horas) if horas < 24 else k.replace(hour=0)
        grupos.setdefault(k, []).append(float(v))
    claves = sorted(grupos)
    return claves, np.array([func(grupos[k]) for k in claves])


def _purgas(y: np.ndarray) -> list[int]:
    d = np.diff(y)
    if len(d) < 3:
        return []
    mad = float(np.median(np.abs(d - np.median(d)))) or 0.01
    umbral = max(3.0, 8 * mad)
    return [i + 1 for i, v in enumerate(d) if v < -umbral]


def _r2(x, y, m, b) -> float:
    res = y - (m * x + b)
    tot = y - y.mean()
    st = float((tot ** 2).sum())
    return 1.0 - float((res ** 2).sum()) / st if st > 0 else 0.0


def timeleft_zabbix(x: np.ndarray, y: np.ndarray, umbral: float) -> float | None:
    """Equivalente a timeleft(...,100) de Zabbix: ajuste lineal por mínimos cuadrados."""
    if len(x) < 2:
        return None
    m, b = np.polyfit(x, y, 1)
    if m <= 0:
        return None
    return max(0.0, (umbral - (m * x[-1] + b)) / m)


def predecir(serie: str, puntos: list[Punto], *, umbrales=(85, 95, 100), ventana_dias: int = 14,
             min_puntos: int = 24, modo: str = "capacidad") -> dict:
    """modo = capacidad (M1) | heap (M2)."""
    puntos = sorted(puntos, key=lambda p: p.ts)
    if len(puntos) < min_puntos:
        return {"serie": serie, "estado": "datos_insuficientes", "puntos": len(puntos)}
    fin = puntos[-1].ts
    puntos = [p for p in puntos if p.ts >= fin - timedelta(days=ventana_dias)]
    ts = [p.ts for p in puntos]
    y = np.array([p.valor for p in puntos], dtype=float)
    actual = float(y[-1])
    notas: list[str] = []

    if modo == "heap":
        bts, by = _bloques(ts, y, 24, min)
        notas.append("mínimo diario del heap después del GC")
        modelo = "M2-piso-gc-v1"
    else:
        purgas = _purgas(y)
        if len(purgas) >= 2:
            # diente de sierra: picos antes de cada purga + el valor actual
            idx = [i - 1 for i in purgas]
            bts = [ts[i] for i in idx] + [ts[-1]]
            by = np.array([y[i] for i in idx] + [y[-1]])
            notas.append(f"serie en diente de sierra ({len(purgas)} purgas): se proyectan los picos")
        else:
            if purgas:
                ini = purgas[-1]
                notas.append(f"purga detectada el {ts[ini]:%d-%m %H:%M}: se usa el tramo posterior")
                ts, y = ts[ini:], y[ini:]
            bts, by = _bloques(ts, y, 6, max)
        modelo = VERSION

    if len(by) < 3:
        return {"serie": serie, "estado": "datos_insuficientes", "puntos": len(puntos)}
    x = _dias(bts)
    m, b, lo, hi = theil_sen(x, by)
    desde = bts[0]
    reciente = x >= x[-1] - 3
    if reciente.sum() >= 6 and (~reciente).sum() >= 6:
        mr, br, lor, hir = theil_sen(x[reciente], by[reciente])
        if m > 0 and abs(mr - m) / m > 0.5 or m <= 0 < mr:
            notas.append(f"cambio de tendencia: {mr:.2f} %/día en los últimos 3 días frente a {m:.2f} %/día")
            m, b, lo, hi = mr, br, lor, hir
            desde = bts[int(np.argmax(reciente))]
    r2 = _r2(x, by, m, b)
    n = len(by)
    confianza = "alta" if r2 >= 0.8 and n >= 20 else "media" if r2 >= 0.5 and n >= 8 else "baja"
    base_nivel = float(by[-1]) if modo == "heap" else max(actual, float(by[-1]))

    def cruce(u: float, pendiente: float):
        if pendiente <= 0:
            return None
        return max(0.0, (u - base_nivel) / pendiente)

    xs_h = _dias(ts)
    resultado = {}
    for u in umbrales:
        d50, d10, d90 = cruce(u, m), cruce(u, hi), cruce(u, lo)
        resultado[str(u)] = {
            "dias": None if d50 is None else round(d50, 2),
            "fecha": None if d50 is None else (fin + timedelta(days=d50)).isoformat(),
            "fecha_p10": None if d10 is None else (fin + timedelta(days=d10)).isoformat(),
            "fecha_p90": None if d90 is None else (fin + timedelta(days=d90)).isoformat(),
            "ya_superado": base_nivel >= u,
        }
    tl = timeleft_zabbix(xs_h, y, 100.0)
    if m <= 0:
        explicacion = f"sin crecimiento sostenido desde {desde:%d-%m} ({m:.2f} %/día)"
    else:
        explicacion = f"pendiente actual {m:.2f} %/día desde el {desde:%d-%m}"
    if notas:
        explicacion += "; " + "; ".join(notas)
    return {
        "serie": serie, "estado": "ok", "modelo": modelo, "actual": round(actual, 2),
        "pendiente_dia": round(m, 4), "pendiente_p10": round(hi, 4), "pendiente_p90": round(lo, 4),
        "umbrales": resultado, "confianza": confianza, "r2": round(r2, 3), "puntos": len(puntos),
        "ventana_desde": desde.isoformat(), "ultimo_dato": fin.isoformat(),
        "timeleft_zabbix_dias": None if tl is None else round(tl, 2),
        "explicacion": explicacion,
    }


def serie_sintetica(dias: int = 20, inicio: float = 60.0, pendiente: float = 1.5, ruido: float = 0.4,
                    purgas_cada_h: int | None = None, salto_purga: float = 20.0, semilla: int = 7,
                    fin: datetime | None = None) -> list[Punto]:
    """Para pruebas y para la demo en simulación."""
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
