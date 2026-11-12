"""Paquete de evidencia descargable de un grupo de snapshot (ANTES / DESPUÉS / FINAL).

Pedido en la demo: poder adjuntar al cambio (o al caso de Jira) la evidencia de que el despliegue
no dejó objetos inválidos, o de cuáles dejó y cómo se recompilaron. El ZIP contiene:

  evidencia.html          resumen legible (para abrir o imprimir a PDF)
  comparacion.json        última comparación guardada (nuevos, corregidos, persistentes, línea base)
  snapshots/<MOMENTO>.json cada snapshot tal como se tomó, con su huella SHA-256
  ejecuciones.json        las ejecuciones que tomaron, compararon o recompilaron, con su salida
  MANIFIESTO.json         SHA-256 de cada archivo del paquete y quién lo descargó
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import zipfile
from datetime import datetime

from sqlalchemy import and_, false, or_, select

from ..almacen import Almacen, ejecucion, linea, snapshot as t_snapshot, utc

ACCIONES = ("bd.snap.tomar", "bd.snap.comparar", "bd.inv.recompilar")


def _j(o) -> str:
    return json.dumps(o, indent=2, ensure_ascii=False, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x))


def datos(almacen: Almacen, base: str, grupo: str) -> dict:
    with almacen.tx() as con:
        snaps = [dict(f._mapping) for f in con.execute(
            select(t_snapshot).where(and_(t_snapshot.c.base == base, t_snapshot.c.cambio == grupo))
            .order_by(t_snapshot.c.tomado))]
        ids = {s["ejecucion_id"] for s in snaps if s.get("ejecucion_id")}
        ejs = [dict(f._mapping) for f in con.execute(
            select(ejecucion).where(and_(ejecucion.c.destino == base, ejecucion.c.accion.in_(ACCIONES),
                                         or_(ejecucion.c.id.in_(ids) if ids else false(),
                                             ejecucion.c.cambio == grupo)))
            .order_by(ejecucion.c.creada))]
        for e in ejs:
            e["salida"] = [f.texto for f in con.execute(
                select(linea.c.texto).where(linea.c.ejecucion_id == e["id"]).order_by(linea.c.n))]
    return {"snapshots": snaps, "ejecuciones": ejs}


def _html(base: str, grupo: str, comp: dict | None, d: dict, quien: str, ahora: str) -> str:
    e = html.escape
    filas_snap = "".join(
        f"<tr><td>{e(s['momento'])}</td><td>{e(str(s['tomado']))}</td><td>{s['datos'].get('total', '')}</td>"
        f"<td><code>{e(s['huella'][:16])}…</code></td></tr>" for s in d["snapshots"])

    def tabla(titulo: str, objs: list[dict]) -> str:
        if not objs:
            return ""
        f = "".join(f"<tr><td>{e(o['owner'])}</td><td>{e(o['object_name'])}</td><td>{e(o['object_type'])}</td></tr>"
                    for o in objs)
        return f"<h3>{e(titulo)} ({len(objs)})</h3><table><tr><th>Esquema</th><th>Objeto</th><th>Tipo</th></tr>{f}</table>"

    if comp:
        r = comp["resumen"]
        veredicto = ("El cambio no dejó objetos inválidos nuevos." if comp["veredicto"] == "sin_nuevos"
                     else f"El cambio dejó {r['nuevos']} objeto(s) inválido(s) nuevo(s).")
        cuerpo = (f"<p class='v {'ok' if comp['veredicto'] == 'sin_nuevos' else 'mal'}'>{e(veredicto)}</p>"
                  f"<p>Nuevos: <b>{r['nuevos']}</b> · Corregidos: <b>{r['corregidos']}</b> · Persistentes: "
                  f"<b>{r['persistentes']}</b> · En línea base: <b>{r['en_linea_base']}</b> · Papelera: {r['papelera']}</p>"
                  + (f"<p class='aviso'>{e(comp['aviso_linea_base'])}</p>" if comp.get("aviso_linea_base") else "")
                  + tabla("Nuevos por el cambio", comp["nuevos"]) + tabla("Corregidos", comp["corregidos"])
                  + tabla("Persistentes (ya estaban inválidos antes)", comp["persistentes"])
                  + tabla("En línea base (no se recompilan)", comp["en_linea_base"]))
    else:
        cuerpo = "<p class='aviso'>Todavía no hay comparación para este grupo: se incluyen solo los snapshots.</p>"
    ejs = "".join(
        f"<tr><td>{e(x['id'][:10])}</td><td>{e(x['accion'])}</td><td>{e(x['solicitante'])}</td>"
        f"<td>{e(str(x['creada']))}</td><td>{e(x['estado'])}</td></tr>" for x in d["ejecuciones"])
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Evidencia {e(base)} {e(grupo)}</title>
<style>body{{font:14px/1.45 "Segoe UI",Arial,sans-serif;margin:32px;color:#16202f}}h1{{font-size:20px}}
table{{border-collapse:collapse;width:100%;margin:6px 0 16px}}th,td{{border:1px solid #d5dbe5;padding:5px 8px;text-align:left;font-size:12.5px}}
th{{background:#f1f4f8}}.v{{font-weight:700;padding:10px;border-radius:6px}}.ok{{background:#dcf5e6;color:#15803d}}
.mal{{background:#fde2e2;color:#c5222c}}.aviso{{background:#fdf0d0;color:#92600a;padding:8px;border-radius:6px}}
.pie{{color:#5b6576;font-size:12px;margin-top:24px}}</style></head><body>
<h1>Evidencia de objetos inválidos · {e(base)} · {e(grupo)}</h1>
<p>Generado por Ops Central el {e(ahora)} para {e(quien)}.</p>
<h2>Resultado</h2>{cuerpo}
<h2>Snapshots</h2><table><tr><th>Momento</th><th>Tomado (UTC)</th><th>Inválidos</th><th>Huella</th></tr>{filas_snap}</table>
<h2>Ejecuciones</h2><table><tr><th>Id</th><th>Acción</th><th>Quién</th><th>Cuándo (UTC)</th><th>Estado</th></tr>{ejs}</table>
<p class="pie">La integridad de cada archivo del paquete se puede verificar con MANIFIESTO.json (SHA-256).</p>
</body></html>"""


def paquete(almacen: Almacen, base: str, grupo: str, comparacion: dict | None, *, quien: str) -> tuple[bytes, str, dict]:
    d = datos(almacen, base, grupo)
    if not d["snapshots"]:
        raise KeyError(f"no hay snapshots de {base} en el grupo {grupo}")
    ahora = utc().isoformat()
    archivos: dict[str, bytes] = {
        "evidencia.html": _html(base, grupo, comparacion, d, quien, ahora).encode("utf-8"),
        "ejecuciones.json": _j([{k: v for k, v in x.items() if k not in ("token_aprobacion",)}
                                for x in d["ejecuciones"]]).encode("utf-8"),
    }
    if comparacion:
        archivos["comparacion.json"] = _j(comparacion).encode("utf-8")
    vistos: dict[str, int] = {}
    for s in d["snapshots"]:
        vistos[s["momento"]] = vistos.get(s["momento"], 0) + 1
        sufijo = f"_{vistos[s['momento']]}" if vistos[s["momento"]] > 1 else ""
        archivos[f"snapshots/{s['momento']}{sufijo}.json"] = _j(s["datos"]).encode("utf-8")
    manifiesto = {"base": base, "grupo": grupo, "generado": ahora, "por": quien,
                  "archivos": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(archivos.items())}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in sorted(archivos.items()):
            z.writestr(k, v)
        z.writestr("MANIFIESTO.json", _j(manifiesto))
    nombre = f"evidencia_{base}_{grupo.replace('/', '-')}_{utc():%Y%m%d-%H%M}.zip"
    resumen = {"snapshots": len(d["snapshots"]), "ejecuciones": len(d["ejecuciones"]),
               "veredicto": (comparacion or {}).get("veredicto"), "sha256_zip": hashlib.sha256(buf.getvalue()).hexdigest()}
    return buf.getvalue(), nombre, resumen
