#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_reporte_diskgroups.py
═════════════════════════════
Reporte de Diskgroups ASM por ambiente (skill reporte-diskgroups) generado por
script, sin modelo de lenguaje.

  • Solo lectura: una consulta a v$asm_diskgroup por servidor (un alias cualquiera;
    si la instancia no responde se prueba el siguiente alias del mismo servidor).
  • Agrupado por servidor, nunca por criticidad; dentro de cada servidor, de mayor
    a menor % de llenado. Servidores en el orden del inventario.
  • Clusters RAC: servidores que reportan exactamente los mismos diskgroups
    (nombre, total_mb, free_mb) se consolidan en una sola fila de grupo.
  • Filtro por defecto ≥ 50 %. CRITICO ≥ 90, WARNING 70-89, MODERADO 50-69.
  • Archivo: reporte_diskgroups_<ambiente>_<YYYYMMDD>.html

Uso (paquete portable):
    python generar_reporte_diskgroups.py --env prod
    python generar_reporte_diskgroups.py --env test --umbral 70
    python generar_reporte_diskgroups.py --env prod --muestra
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import Callable

try:
    from . import comun as c
except ImportError:  # paquete portable
    import reportes_comun as c  # type: ignore

esc, num = c.esc, c.num

UMBRAL_DEFECTO = 50.0
CRITICO, WARNING = 90.0, 70.0
MAX_PARALELO = 10

SQL_DG = """
    SELECT name, total_mb, free_mb, ROUND((1 - free_mb/NULLIF(total_mb,0))*100, 2) AS pct_used
      FROM v$asm_diskgroup
     ORDER BY pct_used DESC"""

ERRORES_NO_REINTENTAR = ("ORA-01017", "ORA-12170", "ORA-28000")


def estado(pct: float) -> tuple[str, str]:
    """(texto, clase css) según el rango de llenado."""
    if pct >= CRITICO:
        return "CRITICO", "critical"
    if pct >= WARNING:
        return "WARNING", "warning"
    return "MODERADO", "moderate"


def servidor_de(entry: dict) -> str:
    """Servidor de una entrada: campo 'servidor' si existe; si no, la IP/host del DSN."""
    if entry.get("servidor"):
        return str(entry["servidor"])
    dsn = entry.get("dsn") or ""
    m = re.match(r"^\s*(?:[a-z]+://)?([^:/\s]+)", dsn, re.I)
    return m.group(1) if m else entry.get("alias", "?")


def servicio_de(entry: dict) -> str:
    dsn = entry.get("dsn") or ""
    return dsn.split("/")[-1].strip().upper() if "/" in dsn else entry.get("alias", "").upper()


def _codigo(error: str) -> str:
    m = re.search(r"ORA-\d{5}|DPY-\d{4}", error or "")
    return m.group(0) if m else ""


def _consultar_servidor(srv: str, entradas: list[dict], abrir: Callable[[dict], AbstractContextManager]) -> dict:
    """Prueba los alias del servidor en orden hasta que uno responda."""
    intentos = []
    for e in entradas:
        try:
            with abrir(e) as client:
                filas = c.q(client, SQL_DG)
            return {"servidor": srv, "alias": e["alias"], "filas": filas, "intentos": intentos, "error": None}
        except Exception as ex:  # noqa: BLE001
            msg = str(ex).strip().splitlines()[0] if str(ex).strip() else repr(ex)
            intentos.append({"alias": e["alias"], "error": msg})
            if _codigo(msg) in ERRORES_NO_REINTENTAR:
                break
    return {"servidor": srv, "alias": None, "filas": [], "intentos": intentos,
            "error": intentos[-1]["error"] if intentos else "sin alias"}


ETAPAS = ["Barrido de servidores", "v$asm_diskgroup", "Análisis de llenado", "Generar HTML"]


def recolectar(entradas: list[dict], abrir: Callable[[dict], AbstractContextManager],
               avance: Callable[[str], None] = lambda _e: None) -> tuple[list[dict], dict]:
    """Agrupa por servidor (orden del inventario) y consulta en paralelo."""
    avance(ETAPAS[0])
    servidores: dict[str, list[dict]] = {}
    for e in entradas:
        servidores.setdefault(servidor_de(e), []).append(e)
    avance(ETAPAS[1])
    with ThreadPoolExecutor(max_workers=min(MAX_PARALELO, max(1, len(servidores)))) as ex:
        futuros = [ex.submit(_consultar_servidor, s, es, abrir) for s, es in servidores.items()]
        resultados = [f.result() for f in futuros]
    return resultados, servidores


def consolidar(resultados: list[dict], servidores: dict[str, list[dict]]) -> list[dict]:
    """Regla RAC: firmas idénticas de diskgroups → un solo grupo con todas las IPs."""
    grupos: list[dict] = []
    por_firma: dict[tuple, dict] = {}
    for r in resultados:
        if r["error"] or not r["filas"]:
            continue
        firma = tuple(sorted((f["name"], c.a_float(f["total_mb"]), c.a_float(f["free_mb"])) for f in r["filas"]))
        bases = [servicio_de(e) for e in servidores[r["servidor"]]]
        if firma in por_firma:
            g = por_firma[firma]
            g["servidores"].append(r["servidor"])
            g["bases"] += [b for b in bases if b not in g["bases"]]
            g["aliases"].append(r["alias"])
            continue
        g = {"servidores": [r["servidor"]], "bases": list(dict.fromkeys(bases)), "aliases": [r["alias"]],
             "diskgroups": sorted(r["filas"], key=lambda f: -(c.a_float(f.get("pct_used")) or 0))}
        por_firma[firma] = g
        grupos.append(g)
    return grupos


def _etiqueta_ips(servs: list[str]) -> str:
    """10.7.0.34 / 10.7.0.35 → '10.7.0.34/35' (formato del skill); otros nombres se unen con ' / '."""
    if len(servs) == 1:
        return servs[0]
    pref = [s.rsplit(".", 1) for s in servs]
    if all(len(p) == 2 and p[0] == pref[0][0] and p[1].isdigit() for p in pref):
        return pref[0][0] + "." + "/".join(p[1] for p in pref)
    return " / ".join(servs)


CSS = """
  *{box-sizing:border-box;}
  body{background:#1a1a2e;color:#e0e0e0;font-family:'Segoe UI',Tahoma,sans-serif;margin:0;padding:24px;}
  .wrap{max-width:1400px;margin:0 auto;}
  .header{background:linear-gradient(135deg,#16213e,#0f3460);border-radius:12px;padding:26px 32px;margin-bottom:22px;box-shadow:0 4px 18px rgba(0,0,0,.35);}
  .header h1{color:#e94560;margin:0 0 6px;font-size:1.7rem;}
  .header .sub{color:#b8c1ec;font-size:.95rem;}
  .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:20px;}
  .summary-card{background:#16213e;border:1px solid #0f3460;border-radius:10px;padding:18px 20px;}
  .summary-card .label{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:#9aa5ce;}
  .summary-card .value{color:#e94560;font-size:2rem;font-weight:700;margin-top:4px;}
  .filter-info{background:#16213e;border-left:4px solid #e94560;border-radius:8px;padding:10px 16px;margin-bottom:18px;font-size:.9rem;}
  .table-wrap{overflow-x:auto;}
  table{width:100%;border-collapse:collapse;background:#16213e;border-radius:10px;overflow:hidden;font-size:.9rem;}
  thead{background:#0f3460;}
  th{color:#e94560;text-transform:uppercase;font-size:.78rem;letter-spacing:.04em;padding:12px 10px;text-align:left;}
  td{padding:10px;border-bottom:1px solid #22304f;vertical-align:middle;}
  tbody tr:hover{background:#1a2744;}
  .server-group{background:#0f3460;}
  .server-group td{border-top:2px solid #e94560;font-weight:700;color:#fff;}
  .num{text-align:right;font-variant-numeric:tabular-nums;}
  .pct-bar{position:relative;height:22px;background:#1a1a2e;border-radius:11px;min-width:160px;overflow:hidden;}
  .pct-bar .fill{height:100%;border-radius:11px;}
  .pct-label{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:.78rem;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);}
  .critical{background:linear-gradient(90deg,#e94560,#c0392b);}
  .warning{background:linear-gradient(90deg,#f39c12,#e67e22);}
  .moderate{background:linear-gradient(90deg,#27ae60,#2ecc71);}
  .badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:.75rem;font-weight:700;}
  .badge-critical{background:#c0392b;color:#fff;} .badge-warning{background:#e67e22;color:#fff;}
  .badge-moderate{background:#27ae60;color:#fff;}
  .notas{background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:16px 22px;margin-top:20px;}
  .notas h3{color:#e94560;margin-top:0;}
  .notas li{margin:4px 0;}
  .mono{font-family:Consolas,'Courier New',monospace;font-size:.85rem;}
  .aviso{background:#3b2a0f;border-left:4px solid #f39c12;border-radius:8px;padding:10px 16px;margin-bottom:18px;}
  footer{color:#7f8bb3;font-size:.8rem;text-align:center;margin-top:24px;}
"""


def render(meta: dict, grupos: list[dict], filtrados: list[dict], notas: list[str]) -> str:
    umbral = meta["umbral"]
    criticos = sum(1 for g in filtrados for d in g["diskgroups"] if d["_pct"] >= CRITICO)
    warnings = sum(1 for g in filtrados for d in g["diskgroups"] if WARNING <= d["_pct"] < CRITICO)
    total_dg = sum(len(g["diskgroups"]) for g in filtrados)
    filas: list[str] = []
    n = 0
    for g in filtrados:
        tipo = "Cluster RAC" if len(g["servidores"]) > 1 else "Servidor"
        filas.append(f'<tr class="server-group"><td colspan="9">{tipo} — {esc(" / ".join(g["servidores"]))}'
                     f' <span class="mono" style="font-weight:400;opacity:.75">(consultado vía {esc(", ".join(g["aliases"]))})</span></td></tr>')
        for d in g["diskgroups"]:
            n += 1
            pct = d["_pct"]
            txt, cls = estado(pct)
            total, libre = c.a_float(d.get("total_mb")) or 0, c.a_float(d.get("free_mb")) or 0
            filas.append(
                f'<tr><td>{n}</td><td class="mono">{esc(_etiqueta_ips(g["servidores"]))}</td>'
                f'<td>{esc(", ".join(g["bases"]))}</td><td><strong>{esc(d.get("name"))}</strong></td>'
                f'<td class="num">{num(total / 1024, 0)}</td><td class="num">{num((total - libre) / 1024, 0)}</td>'
                f'<td class="num">{num(libre / 1024, 0)}</td>'
                f'<td><div class="pct-bar"><div class="fill {cls}" style="width: {min(pct, 100):.2f}%"></div>'
                f'<span class="pct-label">{pct:.2f}%</span></div></td>'
                f'<td><span class="badge badge-{cls}">{txt}</span></td></tr>')
    if not filas:
        filas.append(f'<tr><td colspan="9">Ningún diskgroup supera el umbral de {umbral:g}%.</td></tr>')
    top = sorted(((d["_pct"], d, g) for g in filtrados for d in g["diskgroups"]), key=lambda x: -x[0])[:3]
    resumen = []
    for i, (pct, d, g) in enumerate(top):
        total, libre = c.a_float(d.get("total_mb")) or 0, c.a_float(d.get("free_mb")) or 0
        etiqueta = "Mayor urgencia" if i == 0 else f"Urgencia {i + 1}"
        donde = ("cluster " if len(g["servidores"]) > 1 else "servidor ") + _etiqueta_ips(g["servidores"])
        resumen.append(f"<li><strong>{etiqueta}:</strong> {esc(d.get('name'))} en {esc(donde)} al "
                       f"<strong>{pct:.2f}%</strong> — solo {num(libre / 1024, 0)} GB libres de "
                       f"{total / 1024 / 1024:,.1f} TB.</li>")
    if not resumen:
        resumen.append(f"<li>Sin diskgroups por encima del {umbral:g}% en {esc(meta['ambiente'].upper())}.</li>")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reporte de Diskgroups ASM — {esc(meta['ambiente'].upper())} — {esc(meta['date'])}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<div class="header">
  <h1>Reporte de Diskgroups ASM — {esc(meta['ambiente'].upper())}</h1>
  <div class="sub">Generado {esc(meta['datetime'])} (COT, {c.COT_TZ}) · Filtro: llenado ≥ {umbral:g}% · Agrupado por servidor/cluster</div>
</div>
{meta.get('aviso', '')}
<div class="summary">
  <div class="summary-card"><div class="label">Servidores consultados</div><div class="value">{len(grupos)}</div></div>
  <div class="summary-card"><div class="label">Diskgroups ≥ {umbral:g}%</div><div class="value">{total_dg}</div></div>
  <div class="summary-card"><div class="label">Críticos (≥90%)</div><div class="value">{criticos}</div></div>
  <div class="summary-card"><div class="label">Warning (70-89%)</div><div class="value">{warnings}</div></div>
</div>
<div class="filter-info"><strong>Criterio de inclusión:</strong> diskgroups con porcentaje de llenado ≥ {umbral:g}%
  (ROUND((1 - free_mb/total_mb)*100, 2) sobre v$asm_diskgroup). Ambiente {esc(meta['ambiente'].upper())}:
  {meta['n_bases']} base(s) en {meta['n_servidores']} servidor(es) del inventario.</div>
<div class="table-wrap">
<table>
  <thead><tr><th>#</th><th>Servidor (IP)</th><th>Bases de datos asociadas</th><th>Diskgroup</th><th>Total (GB)</th>
  <th>Usado (GB)</th><th>Libre (GB)</th><th>% Llenado</th><th>Estado</th></tr></thead>
  <tbody>
  {chr(10).join(filas)}
  </tbody>
</table>
</div>
<div class="notas">
  <h3>Notas y exclusiones</h3>
  <ul>{''.join(f'<li>{x}</li>' for x in notas) or '<li>Sin novedades: todos los servidores respondieron.</li>'}</ul>
</div>
<div class="notas">
  <h3>Leyenda de estados</h3>
  <p><span class="badge badge-critical">CRITICO</span> ≥ 90% — riesgo inmediato de quedarse sin espacio.<br>
  <span class="badge badge-warning">WARNING</span> 70% – 89% — planear ampliación o limpieza.<br>
  <span class="badge badge-moderate">MODERADO</span> 50% – 69% — seguimiento.</p>
</div>
<div class="notas">
  <h3>Resumen ejecutivo</h3>
  <ul>{''.join(resumen)}</ul>
</div>
<footer>Reporte generado por script de solo lectura ({esc(meta['herramienta'])}) · {esc(meta['datetime'])} COT</footer>
</div>
</body>
</html>
"""


def construir(entradas: list[dict], abrir: Callable[[dict], AbstractContextManager], ambiente: str,
              umbral: float = UMBRAL_DEFECTO, aviso: str = "", herramienta: str = "",
              progreso=None) -> tuple[str, str, dict]:
    """entradas: bases del ambiente (alias, dsn, [servidor]); abrir(entrada) → cliente de solo lectura."""
    avance = progreso or (lambda _etapa: None)
    now = c.ahora()
    resultados, servidores = recolectar(entradas, abrir, avance)
    avance(ETAPAS[2])
    grupos = consolidar(resultados, servidores)
    notas: list[str] = []
    for r in resultados:
        alt = r["intentos"]
        if r["error"]:
            cod = _codigo(r["error"])
            notas.append(f"Servidor <span class='mono'>{esc(r['servidor'])}</span>: error de conexión "
                         f"{esc(cod or '')} — {esc(r['error'])} (alias probados: "
                         f"{esc(', '.join(i['alias'] for i in alt))}).")
        elif not r["filas"]:
            notas.append(f"Servidor <span class='mono'>{esc(r['servidor'])}</span>: sin ASM "
                         f"(0 filas en v$asm_diskgroup vía {esc(r['alias'])}).")
        elif alt:
            notas.append(f"Servidor <span class='mono'>{esc(r['servidor'])}</span>: se usó el alias alternativo "
                         f"{esc(r['alias'])} porque {esc(', '.join(i['alias'] for i in alt))} no respondió.")
    filtrados = []
    for g in grupos:
        for d in g["diskgroups"]:
            d["_pct"] = c.a_float(d.get("pct_used")) or 0.0
        dgs = [d for d in g["diskgroups"] if d["_pct"] >= umbral]
        if dgs:
            filtrados.append({**g, "diskgroups": dgs})
        else:
            notas.append(f"{'Cluster' if len(g['servidores']) > 1 else 'Servidor'} "
                         f"<span class='mono'>{esc(' / '.join(g['servidores']))}</span>: excluido, todos sus "
                         f"diskgroups están bajo el {umbral:g}%.")
    for g in grupos:
        if len(g["servidores"]) > 1:
            notas.append(f"Cluster RAC {esc(' / '.join(g['servidores']))}: reportan los mismos diskgroups "
                         "(nombre, total y libre idénticos); se reporta una sola vez.")
    meta = {"ambiente": ambiente, "umbral": float(umbral), "date": now.strftime("%Y-%m-%d"),
            "datetime": now.strftime("%Y-%m-%d %H:%M"), "n_bases": len(entradas), "n_servidores": len(servidores),
            "aviso": aviso, "herramienta": herramienta or "generar_reporte_diskgroups.py"}
    avance(ETAPAS[3])
    html_txt = render(meta, grupos, filtrados, notas)
    todos = [d for g in filtrados for d in g["diskgroups"]]
    criticos = [d for d in todos if d["_pct"] >= CRITICO]
    overall = "CRITICAL" if criticos else "WARNING" if any(d["_pct"] >= WARNING for d in todos) else "OK"
    resumen = {"overall_status": overall, "servidores_consultados": len(grupos), "diskgroups": len(todos),
               "criticos": len(criticos), "warning": sum(1 for d in todos if WARNING <= d["_pct"] < CRITICO),
               "errores": sum(1 for r in resultados if r["error"]),
               "top": [{"diskgroup": d.get("name"), "pct": pct, "servidores": g["servidores"]}
                       for pct, d, g in sorted(((d["_pct"], d, g) for g in filtrados for d in g["diskgroups"]),
                                               key=lambda x: -x[0])[:3]]}
    for g in grupos:
        for d in g["diskgroups"]:
            d.pop("_pct", None)
    fname = f"reporte_diskgroups_{(ambiente or 'all').lower()}_{now.strftime('%Y%m%d')}.html"
    c.log(f"Diskgroups {ambiente}: {len(grupos)} grupo(s), {len(todos)} DG ≥ {umbral:g}% · overall={overall}")
    return fname, html_txt, resumen


def main() -> int:
    p = c.argumentos("Genera el reporte HTML de diskgroups ASM de un ambiente.", por_ambiente=True)
    p.add_argument("--umbral", type=float, default=UMBRAL_DEFECTO, help="Llenado mínimo a incluir (default 50).")
    args = p.parse_args()
    if not args.env:
        c.log("ERROR: indica el ambiente con --env (prod, test, …).")
        return 2
    inventario = c.cargar_inventario(args)
    entradas = [dict(e, alias=a) for a, e in inventario.items()
                if (e.get("environment") or "").lower() == args.env.lower()]
    if not entradas:
        c.log(f"ERROR: no hay BD con environment='{args.env}' en el inventario.")
        return 2
    nombre, html_txt, resumen = construir(entradas, lambda e: c.abrir_cliente(e, args.muestra), args.env,
                                          args.umbral)
    print(c.escribir(c.carpeta_salida(args, args.env), nombre, html_txt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
