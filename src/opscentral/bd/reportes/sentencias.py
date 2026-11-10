#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_sentencias_costosas.py
══════════════════════════════
Reporte interactivo de Sentencias Costosas (skill reporte-sentencias-costosas)
generado por script, sin modelo de lenguaje.

  • Solo lectura (perfil consultor / OPS_CONSULTA).
  • Top SQL por CPU, ELAPSED o GETS (v$sqlarea), CPU en tiempo real de sesiones
    activas y wait events.
  • Origen de cada sql_id del top 10: v$sql.program_id → dba_objects (PL/SQL),
    module/action, sesión activa (plsql_entry_object_id) y clasificación del
    programa (dblink, Job, JDBC, COBOL, sqlplus…). Texto completo por partes.
  • "Por qué es costosa" y recomendaciones con reglas fijas sobre las métricas
    y el texto de la sentencia.
  • HTML autocontenido con JavaScript vanilla: clic en sql_id despliega el panel.
  • Archivo: reporte_sentencias_costosas_<alias>_<YYYYMMDD>.html

Uso (paquete portable):
    python generar_sentencias_costosas.py --alias tronador
    python generar_sentencias_costosas.py --alias tronador --criterio CPU --top 30
    python generar_sentencias_costosas.py --alias tronador --muestra
"""
from __future__ import annotations

import re
import sys
from typing import Any

try:
    from . import comun as c
    from . import hoja_vida as hv
except ImportError:  # paquete portable
    import reportes_comun as c  # type: ignore
    import generar_hoja_vida as hv  # type: ignore

esc, num, Section = c.esc, c.num, c.Section

CRITERIOS = {"CPU": "cpu_time", "ELAPSED": "elapsed_time", "GETS": "buffer_gets"}
TOP_DEFECTO, INVESTIGAR = 20, 10
PARTE, MAX_PARTES = 4000, 3

_TOP = """
    SELECT * FROM (
        SELECT sql_id, parsing_schema_name, module, executions,
               ROUND(elapsed_time/1e6, 2) AS elapsed_sec,
               ROUND(cpu_time/1e6, 2) AS cpu_sec,
               ROUND(elapsed_time/1e6/NULLIF(executions,0), 4) AS elapsed_por_exec,
               ROUND(cpu_time/1e6/NULLIF(executions,0), 4) AS cpu_por_exec,
               buffer_gets, ROUND(buffer_gets/NULLIF(executions,0)) AS gets_por_exec,
               disk_reads, rows_processed, plan_hash_value, program_id,
               SUBSTR(sql_text, 1, 200) AS sql_text
        FROM v$sqlarea
        WHERE executions > 0 AND parsing_schema_name NOT IN ('SYS')
        ORDER BY {col} DESC
    ) WHERE ROWNUM <= :top_n"""

SQL = {
    **{f"top_{k}": _TOP.format(col=v) for k, v in CRITERIOS.items()},
    "sesiones": """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'ACTIVE' AND type = 'USER' THEN 1 ELSE 0 END) AS activas
        FROM v$session""",
    "cpu_activas": """
        SELECT * FROM (
            SELECT s.sql_id, s.sql_child_number, s.username, s.program, s.machine,
                   s.sid, s.serial# AS serial, s.last_call_et AS seconds_active,
                   ROUND(sq.cpu_time/1e6, 2) AS cpu_sec_total,
                   ROUND(sq.elapsed_time/1e6, 2) AS elapsed_sec_total,
                   sq.executions,
                   ROUND(sq.cpu_time/NULLIF(sq.executions,0)/1e6, 4) AS cpu_per_exec_sec,
                   sq.buffer_gets, sq.disk_reads,
                   SUBSTR(sq.sql_text, 1, 200) AS sql_text
            FROM v$session s
            JOIN v$sql sq ON s.sql_id = sq.sql_id AND s.sql_child_number = sq.child_number
            WHERE s.status = 'ACTIVE' AND s.type = 'USER'
              AND s.wait_class != 'Idle' AND s.sql_id IS NOT NULL
            ORDER BY sq.cpu_time DESC
        ) WHERE ROWNUM <= 20""",
    "waits": """
        SELECT * FROM (
            SELECT event, wait_class, total_waits,
                   ROUND(time_waited/100, 2) AS time_waited_sec,
                   ROUND(average_wait*10, 2) AS avg_wait_ms
            FROM v$system_event
            WHERE wait_class <> 'Idle'
            ORDER BY time_waited DESC
        ) WHERE ROWNUM <= 20""",
    "origen_sql": """
        SELECT * FROM (
            SELECT sq.sql_id, sq.module, sq.action, sq.parsing_schema_name,
                   sq.program_id, sq.program_line# AS program_line,
                   sq.first_load_time, TO_CHAR(sq.last_active_time,'YYYY-MM-DD HH24:MI') AS last_active_time,
                   o.owner, o.object_name, o.object_type
            FROM v$sql sq
            LEFT JOIN dba_objects o ON o.object_id = sq.program_id
            WHERE sq.sql_id = :sql_id
            ORDER BY sq.last_active_time DESC
        ) WHERE ROWNUM <= 1""",
    "origen_sesion": """
        SELECT * FROM (
            SELECT s.sid, s.serial# AS serial, s.username, s.osuser, s.program, s.module,
                   s.action, s.machine, s.process AS client_pid,
                   s.plsql_entry_object_id, s.plsql_entry_subprogram_id,
                   o.owner AS entry_owner, o.object_name AS entry_object, o.object_type AS entry_type
            FROM v$session s
            LEFT JOIN dba_objects o ON o.object_id = s.plsql_entry_object_id
            WHERE s.sql_id = :sql_id
        ) WHERE ROWNUM <= 5""",
    "largo": """
        SELECT * FROM (SELECT DBMS_LOB.GETLENGTH(sql_fulltext) AS total_length
                       FROM v$sql WHERE sql_id = :sql_id) WHERE ROWNUM <= 1""",
    "texto": """
        SELECT * FROM (SELECT DBMS_LOB.SUBSTR(sql_fulltext, 4000, :desde) AS parte
                       FROM v$sql WHERE sql_id = :sql_id) WHERE ROWNUM <= 1""",
}

# programa (v$session.program / module) → fuente probable
FUENTES = [
    (r"oracle@.*\(TNS V1-V3\)", "dblink desde otra BD",
     "Llega por dblink: identificar la BD de origen con parsing_schema_name y dba_db_links."),
    (r"oracle@.*\((J\d{3}|j\d{3})\)", "Job DBMS_SCHEDULER", "Ejecutado por un job del scheduler."),
    (r"JDBC Thin Client", "Aplicación Java (JDBC)", "Emitido por una aplicación Java vía JDBC."),
    (r"runcobol", "Programa COBOL batch", "Emitido por un programa COBOL batch."),
    (r"sqlplus", "Script SQL manual o crontab", "Ejecutado desde SQL*Plus (manual o crontab)."),
    (r"python", "Script Python", "Emitido por un script Python."),
    (r"emagent", "Oracle Enterprise Manager agent", "Consulta del agente de OEM."),
    (r"JarLauncher", "Aplicación Java standalone", "Aplicación Java standalone."),
]


def _q(client, nombre: str, params: dict | None = None, max_rows: int = 500) -> list[dict]:
    return c.q(client, SQL[nombre], params, max_rows)


def clasificar(programa: str | None, modulo: str | None, origen: dict) -> tuple[str, str]:
    for patron, fuente, mecanismo in FUENTES:
        for v in (programa, modulo):
            if v and re.search(patron, v, re.I):
                return fuente, mecanismo
    if (c.a_float(origen.get("program_id")) or 0) > 0:
        return f"PL/SQL compilado ({origen.get('object_type') or 'objeto'})", \
            "La sentencia vive dentro de un objeto PL/SQL (program_id > 0)."
    if modulo:
        return f"Aplicación ({modulo})", "SQL directo emitido por la aplicación indicada en MODULE."
    return "SQL directo (origen no identificado)", \
        "program_id = 0 y sin sesión activa: emitido directamente por un cliente."


def por_que(f: dict, texto: str) -> tuple[list[str], list[str]]:
    """Razones técnicas y recomendaciones, con reglas fijas."""
    razones, recs = [], []
    epx, gpx = c.a_float(f.get("elapsed_por_exec")) or 0, c.a_float(f.get("gets_por_exec")) or 0
    ejec = c.a_float(f.get("executions")) or 0
    cpu, ela = c.a_float(f.get("cpu_sec")) or 0, c.a_float(f.get("elapsed_sec")) or 0
    dr = (c.a_float(f.get("disk_reads")) or 0) / ejec if ejec else 0
    if epx >= 1:
        razones.append(f"Alto tiempo por ejecución: {num(epx, 2)} s.")
        recs.append("Revisar el plan de ejecución (DBMS_XPLAN.DISPLAY_CURSOR) y correr SQL Tuning Advisor.")
    if gpx >= 100_000:
        razones.append(f"Muchas lecturas lógicas por ejecución: {num(gpx, 0)} buffer gets.")
        recs.append("Buscar accesos FULL SCAN o joins sin índice adecuado; evaluar índices selectivos.")
    if dr >= 1_000:
        razones.append(f"I/O físico alto: {num(dr, 0)} lecturas de disco por ejecución.")
        recs.append("Verificar estadísticas de las tablas y la eficiencia del buffer cache.")
    if ejec >= 100_000:
        razones.append(f"Alta frecuencia: {num(ejec, 0)} ejecuciones acumuladas.")
        recs.append("Evaluar cacheo en la aplicación o reducir llamadas repetitivas (procesamiento por lotes).")
    if ela and cpu / ela < 0.3:
        razones.append(f"El {100 - 100 * cpu / ela:.0f} % del tiempo es espera, no CPU.")
        recs.append("Cruzar con los wait events de la sesión (bloqueos, I/O, red/dblink).")
    t = (texto or "").upper()
    if re.search(r"SELECT\s+\*", t):
        razones.append("Usa SELECT * (trae columnas innecesarias).")
        recs.append("Proyectar solo las columnas requeridas.")
    if re.search(r"LIKE\s+'%", t):
        razones.append("LIKE con comodín inicial impide usar índices B-tree.")
        recs.append("Evaluar índice de texto o reescribir el filtro.")
    if re.search(r"WHERE.*\b(UPPER|LOWER|TRUNC|TO_CHAR|NVL)\s*\(", t, re.S):
        razones.append("Funciones sobre columnas en el WHERE anulan el índice.")
        recs.append("Crear índice basado en función o reescribir el predicado.")
    if re.search(r"\bNOT\s+IN\s*\(\s*SELECT", t):
        razones.append("NOT IN con subconsulta (riesgo de anti-join ineficiente con NULLs).")
        recs.append("Reescribir con NOT EXISTS.")
    if t and ":" not in t and re.search(r"=\s*'[^']+'|=\s*\d+", t):
        razones.append("Literales sin variables bind (parseo duro y planes distintos).")
        recs.append("Usar variables bind en la aplicación.")
    if re.search(r"^\s*(UPDATE|DELETE)\b", t) and " WHERE " not in f" {t} ":
        razones.append("UPDATE/DELETE sin WHERE.")
        recs.append("Confirmar que el barrido completo es intencional.")
    if not razones:
        razones.append("Consumo acumulado alto por volumen total, sin una causa puntual evidente en métricas o texto.")
        recs.append("Monitorear la tendencia y revisar el plan si el consumo sigue creciendo.")
    return razones, list(dict.fromkeys(recs))


def severidad(participacion: float) -> str:
    return "critical" if participacion >= 25 else "high" if participacion >= 10 else \
        "medium" if participacion >= 5 else "low"


def col_origen(client, sql_id: str) -> dict:
    o = (_q(client, "origen_sql", {"sql_id": sql_id}) or [{}])[0]
    ses = _q(client, "origen_sesion", {"sql_id": sql_id})
    largo = int(c.a_float((_q(client, "largo", {"sql_id": sql_id}) or [{}])[0].get("total_length")) or 0)
    partes = []
    for i in range(min(MAX_PARTES, max(1, -(-largo // PARTE)))):
        r = _q(client, "texto", {"sql_id": sql_id, "desde": i * PARTE + 1})
        if not r or r[0].get("parte") is None:
            break
        partes.append(str(r[0]["parte"]))
    return {"origen": o, "sesiones": ses, "texto": "".join(partes), "largo": largo,
            "truncado": largo > PARTE * MAX_PARTES}


CSS = """
  *{box-sizing:border-box;}
  body{font-family:'Segoe UI',Tahoma,sans-serif;background:#f4f6fb;color:#263238;margin:0;}
  .container{max-width:1400px;margin:0 auto;padding:0 20px 40px;}
  .header{background:linear-gradient(135deg,#1a237e,#283593);color:#fff;padding:28px 32px;border-radius:0 0 12px 12px;margin-bottom:22px;}
  .header h1{margin:0 0 6px;font-size:1.65rem;}
  .header .meta{opacity:.9;font-size:.92rem;}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:22px;}
  .kpi{background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 2px 8px rgba(26,35,126,.08);border-top:4px solid #283593;}
  .kpi .l{font-size:.78rem;color:#607d8b;text-transform:uppercase;}
  .kpi .v{font-size:1.6rem;font-weight:700;color:#1a237e;margin-top:4px;}
  .section{background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);padding:20px 24px;margin-bottom:22px;}
  .section h2{color:#1a237e;margin:0 0 14px;font-size:1.2rem;}
  .tw{overflow-x:auto;}
  table{width:100%;border-collapse:collapse;font-size:.86rem;}
  th{background:#e8eaf6;color:#1a237e;text-align:left;padding:9px 10px;font-weight:600;}
  td{padding:8px 10px;border-bottom:1px solid #eceff1;vertical-align:top;}
  tbody tr.fila:hover{background:#f5f7ff;}
  .num{text-align:right;font-variant-numeric:tabular-nums;}
  .sqlid{font-family:Consolas,'Courier New',monospace;color:#283593;font-weight:700;cursor:pointer;text-decoration:underline dotted;background:none;border:0;padding:0;font-size:.9rem;}
  .mono,.sql{font-family:Consolas,'Courier New',monospace;}
  .sql{background:#f5f5f5;border-radius:6px;padding:12px;white-space:pre-wrap;word-break:break-word;font-size:.82rem;max-height:420px;overflow:auto;}
  .detalle td{background:#fafbff;}
  .detalle[hidden]{display:none;}
  .panel{display:grid;grid-template-columns:220px 1fr;gap:6px 16px;font-size:.86rem;}
  .panel .k{color:#607d8b;font-weight:600;}
  .badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.74rem;font-weight:700;color:#fff;text-transform:uppercase;}
  .critical{background:#c62828;} .high{background:#ef6c00;} .medium{background:#f9a825;color:#263238;} .low{background:#2e7d32;}
  ul.r{margin:4px 0 0;padding-left:18px;}
  .aviso{background:#fff8e1;border-left:4px solid #f9a825;border-radius:8px;padding:10px 16px;margin-bottom:18px;}
  .nc{background:#fff3e0;border-left:4px solid #ef6c00;padding:8px 14px;border-radius:0 6px 6px 0;font-size:.85rem;}
  footer{color:#78909c;font-size:.8rem;text-align:center;margin-top:10px;}
"""

JS = """
document.querySelectorAll('.sqlid').forEach(function (b) {
  b.addEventListener('click', function () {
    var d = document.getElementById('d-' + b.dataset.id);
    if (!d) return;
    d.hidden = !d.hidden;
    b.setAttribute('aria-expanded', String(!d.hidden));
  });
});
document.getElementById('todo').addEventListener('click', function () {
  var cerrados = document.querySelectorAll('.detalle[hidden]').length > 0;
  document.querySelectorAll('.detalle').forEach(function (d) { d.hidden = !cerrados; });
});
"""


def _flujo(fuente: str, f: dict, o: dict, ses: list[dict]) -> str:
    s = ses[0] if ses else {}
    pasos = [s.get("program") or f.get("module") or "cliente",
             f"sesión {s.get('username') or f.get('parsing_schema_name') or '?'}"]
    if s.get("entry_object"):
        pasos.append(f"{s.get('entry_owner')}.{s.get('entry_object')} ({s.get('entry_type')})")
    if o.get("object_name"):
        pasos.append(f"{o.get('owner')}.{o.get('object_name')} línea {o.get('program_line') or '?'}")
    pasos.append(f"SQL {f['sql_id']} (plan {f.get('plan_hash_value') or '?'})")
    return "\n".join(("  " * i) + ("└─ " if i else "") + p for i, p in enumerate(pasos))


def render(meta: dict, top: list[dict], det: dict[str, Section], cpu_act: Section, waits: Section,
           sesiones: dict, version: str) -> str:
    crit = meta["criterio"]
    col = {"CPU": "cpu_sec", "ELAPSED": "elapsed_sec", "GETS": "buffer_gets"}[crit]
    total = sum(c.a_float(f.get(col)) or 0 for f in top) or 1
    filas, hallazgos, plan = [], [], []
    for i, f in enumerate(top, 1):
        part = 100 * (c.a_float(f.get(col)) or 0) / total
        sev = severidad(part)
        sid = esc(f["sql_id"])
        filas.append(
            f'<tr class="fila"><td>{i}</td><td><button class="sqlid" data-id="{sid}" aria-expanded="false">{sid}</button></td>'
            f'<td>{esc(f.get("parsing_schema_name"))}</td><td>{esc(f.get("module") or "")}</td>'
            f'<td class="num">{num(f.get("executions"), 0)}</td><td class="num">{num(f.get("elapsed_sec"))}</td>'
            f'<td class="num">{num(f.get("cpu_sec"))}</td><td class="num">{num(f.get("elapsed_por_exec"), 4)}</td>'
            f'<td class="num">{num(f.get("buffer_gets"), 0)}</td><td class="num">{num(f.get("disk_reads"), 0)}</td>'
            f'<td class="num">{part:.1f}%</td><td><span class="badge {sev}">{sev}</span></td>'
            f'<td class="mono">{esc((f.get("sql_text") or "")[:90])}</td></tr>')
        d = det.get(f["sql_id"])
        if d is None:
            panel = ('<div class="panel"><div class="k">Origen</div><div>No investigado (fuera del top '
                     f'{INVESTIGAR}).</div><div class="k">Texto</div><div class="sql">{esc(f.get("sql_text"))}</div></div>')
        elif d.status == "NOT_COLLECTED":
            panel = c.nc(d, "Origen")
        else:
            o, ses, texto = d.data["origen"], d.data["sesiones"], d.data["texto"] or f.get("sql_text") or ""
            s0 = ses[0] if ses else {}
            fuente, mecanismo = clasificar(s0.get("program"), o.get("module") or f.get("module"), {**f, **o})
            razones, recs = por_que(f, texto)
            objeto = f"{o.get('owner')}.{o.get('object_name')}" if o.get("object_name") else "—"
            entrada = f"{s0.get('entry_owner')}.{s0.get('entry_object')}" if s0.get("entry_object") else "—"
            dblink = "Sí — ver parsing_schema_name y dba_db_links" if fuente.startswith("dblink") else "No (local)"
            panel = (f'<div class="panel">'
                     f'<div class="k">Tipo de objeto</div><div>{esc(o.get("object_type") or fuente)}</div>'
                     f'<div class="k">Owner</div><div>{esc(o.get("owner") or o.get("parsing_schema_name") or f.get("parsing_schema_name"))}</div>'
                     f'<div class="k">Package/Procedimiento</div><div class="mono">{esc(objeto)} · entrada PL/SQL: {esc(entrada)}</div>'
                     f'<div class="k">Línea</div><div>{esc(o.get("program_line") or "—")}</div>'
                     f'<div class="k">Base de datos origen</div><div>{esc(dblink)}</div>'
                     f'<div class="k">Mecanismo de ejecución</div><div>{esc(fuente)} — {esc(mecanismo)}'
                     f'{"<br>Programa: " + esc(s0.get("program")) + " @ " + esc(s0.get("machine")) if s0 else ""}'
                     f'<br>Module/Action: {esc(o.get("module") or "—")} / {esc(o.get("action") or "—")}</div>'
                     f'<div class="k">Flujo de ejecución</div><div class="sql">{esc(_flujo(fuente, f, o, ses))}</div>'
                     f'<div class="k">Sentencia completa</div><div class="sql">{esc(texto)}'
                     f'{"<br>[… texto truncado a " + str(PARTE * MAX_PARTES) + " caracteres]" if d.data["truncado"] else ""}</div>'
                     f'<div class="k">Por qué es costosa</div><div><ul class="r">{"".join(f"<li>{esc(r)}</li>" for r in razones)}</ul></div>'
                     f'<div class="k">Recomendación</div><div><ul class="r">{"".join(f"<li>{esc(r)}</li>" for r in recs)}</ul></div>'
                     f'</div>')
            if sev in ("critical", "high"):
                hallazgos.append(f'<li><span class="badge {sev}">{sev}</span> <span class="mono">{sid}</span> '
                                 f'concentra el <strong>{part:.1f}%</strong> del {crit} del top — {esc(fuente)}; '
                                 f'{esc(razones[0])}</li>')
            plan.append((sev, f["sql_id"], recs[0], part))
        filas.append(f'<tr class="detalle" id="d-{sid}" hidden><td colspan="13">{panel}</td></tr>')
    orden = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    plan.sort(key=lambda x: (orden[x[0]], -x[3]))
    plan_html = "".join(f'<tr><td>{i}</td><td><span class="badge {s}">{s}</span></td><td class="mono">{esc(q)}</td>'
                        f'<td>{esc(r)}</td><td class="num">{p:.1f}%</td></tr>' for i, (s, q, r, p) in enumerate(plan, 1))

    def _tabla_simple(sec: Section, cols: list[tuple[str, str, bool]], vacio: str) -> str:
        if sec.status == "NOT_COLLECTED":
            return c.nc(sec, "Sección")
        if not sec.data:
            return f"<p>{esc(vacio)}</p>"
        head = "".join(f"<th>{esc(h)}</th>" for _, h, _ in cols)
        body = "".join("<tr class='fila'>" + "".join(
            f'<td class="{"num" if n else ""}">{num(r.get(k), 2) if n else esc(r.get(k))}</td>' for k, _, n in cols)
            + "</tr>" for r in sec.data)
        return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    max_cpu = max((c.a_float(f.get("cpu_sec")) or 0 for f in top), default=0)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sentencias costosas — {esc(meta['db_name'])} — {esc(meta['date'])}</title>
<style>{CSS}</style>
</head>
<body>
<div class="header"><div class="container" style="padding-bottom:0">
  <h1>Reporte de Sentencias Costosas — {esc(meta['db_name'])}</h1>
  <div class="meta">Alias {esc(meta['alias'])} · Ambiente {esc(meta['environment'].upper())} · Criterio: <strong>{esc(crit)}</strong> · Top {len(top)} · Generado {esc(meta['datetime'])} (COT, {c.COT_TZ}) · Perfil: {esc(meta['perfil'])} (solo lectura)</div>
</div></div>
<div class="container">
{meta.get('aviso', '')}
<div class="kpis">
  <div class="kpi"><div class="l">Sesiones activas (usuario)</div><div class="v">{num(sesiones.get('activas'), 0)}</div></div>
  <div class="kpi"><div class="l">Sesiones totales</div><div class="v">{num(sesiones.get('total'), 0)}</div></div>
  <div class="kpi"><div class="l">Máx. CPU de una sentencia</div><div class="v">{num(max_cpu, 0)} s</div></div>
  <div class="kpi"><div class="l">Versión Oracle</div><div class="v" style="font-size:1.1rem">{esc(version)}</div></div>
  <div class="kpi"><div class="l">Sentencias investigadas</div><div class="v">{sum(1 for v in det.values() if v.status == 'OK')}</div></div>
</div>
<div class="section">
  <h2>Sentencias por {esc(crit)} <button id="todo" class="sqlid" style="float:right;font-size:.8rem">expandir / contraer todo</button></h2>
  <p style="font-size:.84rem;color:#607d8b;margin-top:-6px">Clic en un sql_id para ver su origen, el texto completo, por qué es costosa y la recomendación.</p>
  <div class="tw"><table>
    <thead><tr><th>#</th><th>sql_id</th><th>Esquema</th><th>Module</th><th>Ejec.</th><th>Elapsed (s)</th><th>CPU (s)</th>
    <th>s/ejec</th><th>Buffer gets</th><th>Disk reads</th><th>% del top</th><th>Severidad</th><th>Texto</th></tr></thead>
    <tbody>{''.join(filas) or '<tr><td colspan="13">Sin sentencias en v$sqlarea.</td></tr>'}</tbody>
  </table></div>
</div>
<div class="section"><h2>CPU en tiempo real — sesiones activas ahora</h2>
{_tabla_simple(cpu_act, [("sql_id", "sql_id", False), ("username", "Usuario", False), ("program", "Programa", False),
                         ("machine", "Máquina", False), ("sid", "SID", False), ("seconds_active", "Seg. activa", True),
                         ("cpu_sec_total", "CPU total (s)", True), ("cpu_per_exec_sec", "CPU/ejec (s)", True)],
               "Sin sesiones de usuario activas consumiendo CPU en este momento.")}
</div>
<div class="section"><h2>Wait events (Top 20, sin Idle)</h2>
{_tabla_simple(waits, [("event", "Evento", False), ("wait_class", "Clase", False), ("total_waits", "Esperas", True),
                       ("time_waited_sec", "Tiempo (s)", True), ("avg_wait_ms", "Prom. (ms)", True)], "Sin datos.")}
</div>
<div class="section"><h2>Hallazgos críticos</h2>
  <ul>{''.join(hallazgos) or '<li>Ninguna sentencia concentra más del 10 % del consumo del top.</li>'}</ul>
</div>
<div class="section"><h2>Plan de acción</h2>
  <div class="tw"><table><thead><tr><th>#</th><th>Severidad</th><th>sql_id</th><th>Acción</th><th>% del top</th></tr></thead>
  <tbody>{plan_html or '<tr><td colspan="5">Sin acciones.</td></tr>'}</tbody></table></div>
  <p style="font-size:.82rem;color:#607d8b">Severidad por participación en el consumo del top: critical ≥ 25 %, high ≥ 10 %, medium ≥ 5 %, low &lt; 5 %.</p>
</div>
<footer>Generado por script de solo lectura ({esc(meta['herramienta'])}) · {esc(meta['datetime'])} COT · criterio {esc(crit)} · {len(top)} sentencias, {INVESTIGAR} investigadas</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""


# El diseño de la interfaz nombra "DBA_HIST_SQLSTAT" y "Planes"; el script lee v$sqlarea (no exige
# Diagnostics Pack) y no extrae planes, así que las etapas dicen lo que de verdad hace.
ETAPAS = ["Top SQL (v$sqlarea)", "Origen por módulo", "Texto completo y causas", "Generar HTML"]


def construir(client, alias: str, inv_entry: dict, criterio: str = "ELAPSED", top_n: int = TOP_DEFECTO,
              perfil: str = "consultor", progreso=None) -> tuple[str, str, dict]:
    avance = progreso or (lambda _etapa: None)
    criterio = criterio.upper() if criterio.upper() in CRITERIOS else "ELAPSED"
    avance(ETAPAS[0])
    environment = (inv_entry.get("environment") or "").lower()
    inst = c.safe_collect(client, hv.collect_instance)
    top_sec = c.safe_collect(client, lambda cl: _q(cl, f"top_{criterio}", {"top_n": int(top_n)}))
    if top_sec.status == "NOT_COLLECTED":
        raise RuntimeError(f"no se pudo leer v$sqlarea: {top_sec.error}")
    top = top_sec.data
    ses = c.safe_collect(client, lambda cl: (_q(cl, "sesiones") or [{}])[0])
    cpu_act = c.safe_collect(client, lambda cl: _q(cl, "cpu_activas"))
    waits = c.safe_collect(client, lambda cl: _q(cl, "waits"))
    avance(ETAPAS[1])
    det = {f["sql_id"]: c.safe_collect(client, col_origen, f["sql_id"]) for f in top[:INVESTIGAR]}
    avance(ETAPAS[2])
    db = inst.data["database"] if inst.status == "OK" else {}
    i = inst.data["instance"] if inst.status == "OK" else {}
    db_name = db.get("name") or alias.upper()
    now = c.ahora()
    meta = {"alias": alias, "environment": environment, "db_name": db_name, "criterio": criterio,
            "date": now.strftime("%Y-%m-%d"), "datetime": now.strftime("%Y-%m-%d %H:%M"), "perfil": perfil,
            "aviso": inv_entry.get("aviso", ""),
            "herramienta": inv_entry.get("herramienta") or "generar_sentencias_costosas.py"}
    avance(ETAPAS[3])
    html_txt = render(meta, top, det, cpu_act, waits, ses.data if ses.status == "OK" else {},
                      i.get("version_full") or i.get("version") or "N/A")
    col = {"CPU": "cpu_sec", "ELAPSED": "elapsed_sec", "GETS": "buffer_gets"}[criterio]
    total = sum(c.a_float(f.get(col)) or 0 for f in top) or 1
    sevs = [severidad(100 * (c.a_float(f.get(col)) or 0) / total) for f in top]
    overall = "CRITICAL" if "critical" in sevs else "WARNING" if "high" in sevs else "OK"
    resumen = {"overall_status": overall, "db_name": db_name, "criterio": criterio, "sentencias": len(top),
               "investigadas": sum(1 for v in det.values() if v.status == "OK"),
               "top": [{"sql_id": f["sql_id"], "severidad": s, col: f.get(col)} for f, s in zip(top[:5], sevs)]}
    alias_arch = re.sub(r"[^a-z0-9_]+", "_", alias.lower())
    fname = f"reporte_sentencias_costosas_{alias_arch}_{now.strftime('%Y%m%d')}.html"
    c.log(f"Sentencias {alias}: {len(top)} por {criterio}, {resumen['investigadas']} investigadas")
    return fname, html_txt, resumen


def main() -> int:
    def extra(p):
        p.add_argument("--criterio", default="ELAPSED", choices=sorted(CRITERIOS), help="Orden del top.")
        p.add_argument("--top", type=int, default=TOP_DEFECTO, help="Cantidad de sentencias (default 20).")
    return c.cli_por_base("Genera el reporte HTML de sentencias costosas con origen por sql_id.", construir,
                          extra, lambda a: {"criterio": a.criterio, "top_n": a.top})


if __name__ == "__main__":
    sys.exit(main())
