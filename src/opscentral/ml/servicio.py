"""Scoring horario (ml.scoring): predice cada serie y guarda el resultado EN SOMBRA.

Series de capacidad (M1): ts:<base>:<tablespace>, asm:<base>:<dg>, fs:<host>:<punto>, fra:<base>
Series de heap (M2):      heap:<host>:<managed server>
Las predicciones vigentes se resumen en estado_actual 'ml:riesgos' para el Inicio de cada perfil.
Publicar a Zabbix (zbx.publicar) solo se habilita en E7, si la medición en sombra cumple 04 §6.3.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, select, update

from .. import estado
from ..almacen import Almacen, prediccion, serie_punto, utc
from .anomalias import evaluar as evaluar_anomalia
from .llenado import Punto, predecir

CAPACIDAD = ("ts:", "asm:", "fs:", "fra:")


def _activo(serie: str) -> str:
    partes = serie.split(":")
    return partes[1] if len(partes) > 1 else serie


def _anomalias(almacen: Almacen, cfg, series: list[str]) -> list[dict]:
    """M3: compara el último punto de cada serie con lo normal para ese día y hora de la semana
    (las semanas anteriores). Solo series con historia suficiente; las demás se omiten."""
    semanas = int(cfg["ml"].get("anomalias_semanas", 6))
    umbral = float(cfg["ml"].get("anomalias_umbral_z", 3.5))
    desde = utc() - timedelta(days=7 * semanas + 1)
    zona = utc().tzinfo
    out = []
    for s in series:
        with almacen.tx() as con:
            filas = con.execute(select(serie_punto.c.ts, serie_punto.c.valor).where(and_(
                serie_punto.c.serie == s, serie_punto.c.ts >= desde)).order_by(serie_punto.c.ts)).all()
        if len(filas) < 2:
            continue
        pts = [(t if t.tzinfo else t.replace(tzinfo=zona), float(v)) for t, v in filas]
        r = evaluar_anomalia(s, pts[:-1], pts[-1], umbral_z=umbral)
        if r.get("estado") == "ok" and r["anomalo"]:
            out.append({**r, "activo": _activo(s), "momento": pts[-1][0].isoformat(), "en_sombra": True})
    out.sort(key=lambda x: -abs(x["z"]))
    return out


def scoring(almacen: Almacen, cfg, *, dias_historia: int = 30) -> dict:
    desde = utc() - timedelta(days=dias_historia)
    with almacen.tx() as con:
        series = [r[0] for r in con.execute(select(serie_punto.c.serie).distinct())]
    hechas, riesgos = 0, []
    for s in series:
        if not (s.startswith(CAPACIDAD) or s.startswith("heap:")):
            continue
        with almacen.tx() as con:
            filas = con.execute(select(serie_punto.c.ts, serie_punto.c.valor).where(and_(
                serie_punto.c.serie == s, serie_punto.c.ts >= desde)).order_by(serie_punto.c.ts)).all()
        pts = [Punto(t if t.tzinfo else t.replace(tzinfo=utc().tzinfo), float(v)) for t, v in filas]
        r = predecir(s, pts, umbrales=tuple(cfg["ml"]["umbrales"]), ventana_dias=cfg["ml"]["ventana_dias"],
                     min_puntos=cfg["ml"]["min_puntos"], modo="heap" if s.startswith("heap:") else "capacidad")
        with almacen.tx() as con:
            # La predicción anterior deja de estar vigente aunque esta pasada no produzca una nueva:
            # así Prevención y el resumen de riesgos del Inicio salen siempre de la misma pasada.
            con.execute(update(prediccion).where(and_(prediccion.c.serie == s, prediccion.c.vigente.is_(True)))
                        .values(vigente=False))
        if r.get("estado") != "ok":
            continue
        modelo = "M2" if s.startswith("heap:") else "M1"
        with almacen.tx() as con:
            con.execute(prediccion.insert().values(serie=s, activo=_activo(s), modelo=modelo, version=r["modelo"],
                                                   calculada=utc(), datos=r, vigente=True, en_sombra=True))
        hechas += 1
        d = r["umbrales"].get("100") or {}
        if d.get("dias") is not None and d["dias"] < 30:
            riesgos.append({"serie": s, "activo": _activo(s), "modelo": modelo, "dias_100": d["dias"],
                            "fecha": d["fecha"], "confianza": r["confianza"], "explicacion": r["explicacion"],
                            "en_sombra": True})
    riesgos.sort(key=lambda x: x["dias_100"])
    estado.guardar(almacen, "ml:riesgos", "ml.riesgos", {"riesgos": riesgos, "en_sombra": True}, origen="ml")
    anomalias = _anomalias(almacen, cfg, series)
    estado.guardar(almacen, "ml:anomalias", "ml.anomalias", {"anomalias": anomalias, "en_sombra": True}, origen="ml")
    return {"series": hechas, "riesgos": len(riesgos), "anomalias": len(anomalias)}
