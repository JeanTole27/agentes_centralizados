"""Análisis de los resultados de las consultas de BD: hallazgos con nivel, no solo filas.

Cada función recibe {plantilla: filas} y devuelve {"hallazgos": [...], "resumen": {...}}.
Un hallazgo es {"nivel": "rojo" | "amarillo" | "info", "texto": str}. El veredicto de la
ejecución es el nivel más alto ("verde" si no hay hallazgos rojos ni amarillos).

Los umbrales se pueden ajustar en [bd.umbrales_analisis] sin tocar código.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

UMBRALES = {
    "idle_tx_amarillo_s": 600, "idle_tx_rojo_s": 3600,
    "fra_amarillo": 80.0, "fra_rojo": 90.0, "archivelog_pico_factor": 3.0,
    "dg_lag_amarillo_s": 300, "dg_lag_rojo_s": 1800,
    "rman_full_max_h": 30, "rman_arch_max_h": 6,
    "temp_sesion_amarillo_mb": 10240,
    "shrink_min_mb": 1024,
    "plan_regresion_factor": 1.5,
    "papelera_amarilla_mb": 10240,
}
NIVEL = {"verde": 0, "info": 0, "amarillo": 1, "rojo": 2}

# Privilegios de sistema que un usuario de aplicación no debería tener
PRIV_PODEROSOS = re.compile(r"(\bANY\b|GRANT ANY|ALTER SYSTEM|ALTER DATABASE|BECOME USER|EXEMPT ACCESS POLICY|"
                            r"SELECT ANY DICTIONARY|ADMINISTER DATABASE TRIGGER|DROP USER|ALTER USER)")
ROLES_PODEROSOS = {"DBA", "IMP_FULL_DATABASE", "EXP_FULL_DATABASE", "DATAPUMP_IMP_FULL_DATABASE",
                   "DATAPUMP_EXP_FULL_DATABASE", "SELECT_CATALOG_ROLE", "EXECUTE_CATALOG_ROLE", "SCHEDULER_ADMIN"}


def _h(nivel: str, texto: str) -> dict:
    return {"nivel": nivel, "texto": texto}


def veredicto(hallazgos: list[dict]) -> str:
    n = max((NIVEL[h["nivel"]] for h in hallazgos), default=0)
    return {0: "verde", 1: "amarillo", 2: "rojo"}[n]


def _num(v, defecto=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return defecto


def _intervalo_s(v) -> float | None:
    """'+00 00:05:12' (v$dataguard_stats) → segundos."""
    if v is None:
        return None
    m = re.match(r"\s*\+?(\d+)\s+(\d+):(\d+):(\d+)", str(v))
    if not m:
        return None
    d, h, mi, s = (int(x) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ====================================================================== sesiones
def idle_tx(d: dict, u: dict = UMBRALES) -> dict:
    filas = d.get("idle_tx", [])
    out = []
    for f in filas:
        s = _num(f.get("segundos_inactiva"))
        quien = f"{f.get('username')}@{f.get('machine')} (SID {f.get('sid')},{f.get('serial')})"
        if s >= u["idle_tx_rojo_s"]:
            out.append(_h("rojo", f"{quien} lleva {s / 60:.0f} min inactiva con una transacción abierta"))
        elif s >= u["idle_tx_amarillo_s"]:
            out.append(_h("amarillo", f"{quien} lleva {s / 60:.0f} min inactiva con una transacción abierta"))
    return {"hallazgos": out, "resumen": {"sesiones": len(filas),
                                          "undo_bloques": sum(int(_num(f.get("bloques_undo"))) for f in filas)}}


# ====================================================================== salud
def fra(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    uso = (d.get("fra_uso") or [None])[0]
    if not uso:
        out.append(_h("amarillo", "La base no tiene FRA configurada (db_recovery_file_dest vacío)"))
    else:
        p = _num(uso.get("pct_usado"))
        if p >= u["fra_rojo"]:
            out.append(_h("rojo", f"FRA al {p:.0f} % (descontando lo recuperable): riesgo de detener la base"))
        elif p >= u["fra_amarillo"]:
            out.append(_h("amarillo", f"FRA al {p:.0f} % (descontando lo recuperable)"))
    horas = d.get("archivelog_hora") or []
    mb = [_num(h.get("mb")) for h in horas]
    if len(mb) >= 4:
        base = sorted(mb)[len(mb) // 2] or 1
        pico = max(mb)
        if pico >= u["archivelog_pico_factor"] * base:
            out.append(_h("amarillo", f"Pico de archivelogs: {pico:.0f} MB en una hora (mediana {base:.0f} MB)"))
    return {"hallazgos": out, "resumen": {"pct_usado": uso and uso.get("pct_usado"),
                                          "mb_24h": round(sum(mb)), "horas": len(horas)}}


def dataguard(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    rol = (d.get("dg_rol") or [{}])[0]
    stats = {s["name"]: s for s in d.get("dg_stats") or []}
    gaps = d.get("dg_gaps") or []
    if rol.get("database_role") == "PRIMARY" and not stats:
        out.append(_h("info", "Base primaria: el lag se mide en la standby (ejecutar sobre la standby)"))
    for nombre in ("transport lag", "apply lag"):
        s = _intervalo_s((stats.get(nombre) or {}).get("value"))
        if s is None:
            continue
        if s >= u["dg_lag_rojo_s"]:
            out.append(_h("rojo", f"{nombre}: {s / 60:.0f} min"))
        elif s >= u["dg_lag_amarillo_s"]:
            out.append(_h("amarillo", f"{nombre}: {s / 60:.0f} min"))
    for g in gaps:
        out.append(_h("rojo", f"Gap de archivelogs en el hilo {g.get('hilo')}: secuencias "
                              f"{g.get('secuencia_desde')}–{g.get('secuencia_hasta')}"))
    if rol.get("force_logging") == "NO" and rol.get("database_role") == "PRIMARY":
        out.append(_h("amarillo", "FORCE LOGGING desactivado en la primaria: operaciones NOLOGGING no llegan a la standby"))
    return {"hallazgos": out, "resumen": {"rol": rol.get("database_role"), "proteccion": rol.get("protection_mode"),
                                          "gaps": len(gaps)}}


def rman(d: dict, u: dict = UMBRALES, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    filas = d.get("rman_7d") or []
    ok = [f for f in filas if str(f.get("status", "")).startswith("COMPLETED")]
    fallidos = [f for f in filas if f.get("status") in ("FAILED", "COMPLETED WITH ERRORS", "COMPLETED WITH WARNINGS")]
    ultimo: dict[str, datetime] = {}
    for f in ok:
        t = _aware(f.get("end_time") or f.get("start_time"))
        tipo = str(f.get("input_type"))
        if t and (tipo not in ultimo or t > ultimo[tipo]):
            ultimo[tipo] = t
    out = []
    datos = [t for k, t in ultimo.items() if k.startswith(("DB FULL", "DB INCR"))]
    if not datos:
        out.append(_h("rojo", "Sin backup completo ni incremental exitoso en 7 días"))
    elif ahora - max(datos) > timedelta(hours=u["rman_full_max_h"]):
        out.append(_h("rojo", f"Último backup de datos exitoso hace {(ahora - max(datos)).total_seconds() / 3600:.0f} h"))
    arch = ultimo.get("ARCHIVELOG")
    if arch is None:
        out.append(_h("amarillo", "Sin backup de archivelogs exitoso en 7 días"))
    elif ahora - arch > timedelta(hours=u["rman_arch_max_h"]):
        out.append(_h("amarillo", f"Último backup de archivelogs hace {(ahora - arch).total_seconds() / 3600:.0f} h"))
    for f in fallidos[:10]:
        out.append(_h("amarillo" if "WARNING" in str(f.get("status")) else "rojo",
                      f"{f.get('input_type')} {f.get('status')} el {f.get('start_time')}"))
    return {"hallazgos": out, "resumen": {"trabajos_7d": len(filas), "fallidos": len(fallidos),
                                          "ultimo_por_tipo": {k: v.isoformat() for k, v in ultimo.items()}}}


def jobs(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    for j in d.get("jobs_problema") or []:
        nombre = f"{j.get('owner')}.{j.get('job_name')}"
        if j.get("state") in ("BROKEN", "FAILED"):
            out.append(_h("rojo", f"Job {nombre} en estado {j.get('state')}"))
        elif j.get("enabled") == "FALSE":
            out.append(_h("amarillo", f"Job {nombre} deshabilitado"))
        elif _num(j.get("failure_count")):
            out.append(_h("amarillo", f"Job {nombre} con {int(_num(j.get('failure_count')))} fallo(s) acumulado(s)"))
    fallos = Counter(f"{f.get('owner')}.{f.get('job_name')}" for f in d.get("jobs_fallidos_24h") or [])
    for nombre, n in fallos.most_common(10):
        out.append(_h("rojo" if n >= 3 else "amarillo", f"{nombre} falló {n} vez/veces en 24 h"))
    return {"hallazgos": out, "resumen": {"con_problema": len(d.get("jobs_problema") or []),
                                          "fallos_24h": sum(fallos.values())}}


def undo_temp(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    undo = d.get("undo_24h") or []
    e1555 = sum(int(_num(f.get("ora_01555"))) for f in undo)
    sin_esp = sum(int(_num(f.get("sin_espacio"))) for f in undo)
    if e1555:
        out.append(_h("rojo", f"{e1555} error(es) ORA-01555 (snapshot too old) en 24 h"))
    if sin_esp:
        out.append(_h("rojo", f"{sin_esp} error(es) de falta de espacio en UNDO en 24 h"))
    maxq = max((_num(f.get("maxquerylen")) for f in undo), default=0)
    for s in d.get("temp_sesiones") or []:
        if _num(s.get("mb")) >= u["temp_sesion_amarillo_mb"]:
            out.append(_h("amarillo", f"{s.get('username')} (SID {s.get('sid')}, SQL {s.get('sql_id')}) usa "
                                      f"{_num(s.get('mb')) / 1024:.1f} GB de TEMP"))
    return {"hallazgos": out, "resumen": {"ora_01555": e1555, "consulta_mas_larga_s": maxq,
                                          "temp_mb_top": sum(_num(s.get("mb")) for s in d.get("temp_sesiones") or [])}}


RE_ORA = re.compile(r"ORA-(\d{5})")
ORA_CRITICOS = {"00600", "07445", "04031", "01578", "00257", "16038", "19809", "01652", "27072", "00353"}


def alertlog(d: dict, u: dict = UMBRALES) -> dict:
    cont: Counter = Counter()
    ultimo: dict[str, str] = {}
    for f in d.get("alertlog_24h") or []:
        for c in set(RE_ORA.findall(str(f.get("mensaje") or ""))):
            cont[c] += 1
            ultimo.setdefault(c, str(f.get("mensaje"))[:200])
    out = []
    for c, n in cont.most_common(15):
        nivel = "rojo" if c in ORA_CRITICOS else "amarillo"
        out.append(_h(nivel, f"ORA-{c} × {n}: {ultimo[c]}"))
    return {"hallazgos": out, "resumen": {"codigos": dict(cont.most_common()), "lineas": len(d.get("alertlog_24h") or [])}}


def esperas(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    ahora = d.get("esperas_ahora") or []
    total = sum(int(_num(f.get("sesiones"))) for f in ahora)
    for f in ahora[:5]:
        n = int(_num(f.get("sesiones")))
        if total and n >= 5 and n / total >= 0.5:
            out.append(_h("amarillo", f"{n} de {total} sesiones activas esperan '{f.get('event')}' ({f.get('wait_class')})"))
    top = (d.get("esperas_sistema") or [None])[0]
    if top:
        out.append(_h("info", f"Mayor espera acumulada: {top.get('event')} ({top.get('wait_class')})"))
    return {"hallazgos": out, "resumen": {"sesiones_esperando": total}}


IGNORAR_DERIVA = {"control_files", "db_unique_name", "fal_server", "fal_client", "log_archive_config",
                  "log_archive_dest_1", "log_archive_dest_2", "instance_name", "instance_number", "local_listener",
                  "dg_broker_config_file1", "dg_broker_config_file2", "service_names", "thread", "undo_tablespace",
                  "audit_file_dest", "core_dump_dest", "diagnostic_dest", "spfile"}


def deriva_parametros(origen: list[dict], destino: list[dict], ignorar: set[str] = IGNORAR_DERIVA) -> list[dict]:
    a = {f["name"]: f.get("value") for f in origen}
    b = {f["name"]: f.get("value") for f in destino}
    out = []
    for n in sorted(set(a) | set(b)):
        if n in ignorar or a.get(n) == b.get(n):
            continue
        out.append({"parametro": n, "origen": a.get(n), "destino": b.get(n)})
    return out


def parametros(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    p = {f["name"]: f.get("value") for f in d.get("parametros") or []}
    if str(p.get("audit_trail", "")).upper() in ("NONE", "FALSE"):
        out.append(_h("amarillo", "audit_trail desactivado"))
    if str(p.get("remote_login_passwordfile", "")).upper() == "SHARED":
        out.append(_h("amarillo", "remote_login_passwordfile = SHARED"))
    deriva = d.get("deriva")
    if deriva:
        out.append(_h("amarillo", f"{len(deriva)} parámetro(s) distintos frente a {d.get('base_comparar')}"))
    return {"hallazgos": out, "resumen": {"no_por_defecto": len(p), "deriva": len(deriva or [])}}


# ====================================================================== usuarios
def privilegios(d: dict, u: dict = UMBRALES) -> dict:
    out = []
    roles = [r["granted_role"] for r in d.get("usr_roles") or []]
    for r in roles:
        if r in ROLES_PODEROSOS:
            out.append(_h("rojo" if r == "DBA" else "amarillo", f"Tiene el rol {r}"))
    for p in d.get("usr_privs_sistema") or []:
        if PRIV_PODEROSOS.search(str(p.get("privilege"))):
            out.append(_h("amarillo", f"Privilegio de sistema {p['privilege']}"
                                      + (" con ADMIN OPTION" if p.get("admin_option") == "YES" else "")))
    concedibles = [p for p in d.get("usr_privs_objeto") or [] if p.get("grantable") == "YES"]
    if concedibles:
        out.append(_h("info", f"{len(concedibles)} privilegio(s) de objeto con GRANT OPTION"))
    return {"hallazgos": out, "resumen": {"roles": len(roles), "privs_sistema": len(d.get("usr_privs_sistema") or []),
                                          "privs_objeto": len(d.get("usr_privs_objeto") or [])}}


# ====================================================================== espacio
def shrink(d: dict, u: dict = UMBRALES) -> dict:
    filas = [f for f in d.get("shrink_candidatos") or [] if _num(f.get("recuperable_mb")) >= u["shrink_min_mb"]]
    total = sum(_num(f.get("recuperable_mb")) for f in filas)
    out = [_h("info", f"{f.get('segment_owner')}.{f.get('segment_name')}: {_num(f.get('recuperable_mb')) / 1024:.1f} GB "
                      "recuperables") for f in filas[:10]]
    if not d.get("shrink_candidatos"):
        out.append(_h("info", "El Segment Advisor no tiene recomendaciones (¿corrió la tarea automática?)"))
    return {"hallazgos": out, "resumen": {"candidatos": len(filas), "recuperable_gb": round(total / 1024, 1)}}


def papelera(d: dict, u: dict = UMBRALES, bloque_kb: int = 8) -> dict:
    filas = d.get("papelera") or []
    por_esquema: Counter = Counter()
    for f in filas:
        por_esquema[f.get("owner")] += _num(f.get("bloques")) * bloque_kb / 1024
    total = sum(por_esquema.values())
    out = []
    if total >= u["papelera_amarilla_mb"]:
        out.append(_h("amarillo", f"La papelera ocupa ~{total / 1024:.1f} GB (bloques de {bloque_kb} KB)"))
    return {"hallazgos": out, "resumen": {"objetos": len(filas),
                                          "mb_por_esquema": {k: round(v) for k, v in por_esquema.most_common()}}}


def stats(d: dict, u: dict = UMBRALES) -> dict:
    filas = d.get("stats_obsoletas") or []
    por_esquema = Counter(f.get("owner") for f in filas)
    out = [_h("amarillo", f"{n} tabla(s)/partición(es) con estadísticas obsoletas en {e}")
           for e, n in por_esquema.most_common(10)]
    return {"hallazgos": out, "resumen": {"objetos": len(filas), "por_esquema": dict(por_esquema)}}


# ====================================================================== rendimiento
def plan(d: dict, u: dict = UMBRALES) -> dict:
    hist = [h for h in d.get("plan_historia") or [] if _num(h.get("ejecuciones"))]
    actual = (d.get("plan_actual") or [None])[0]
    out = []
    if not hist:
        out.append(_h("info", "Sin historia en AWR para este SQL_ID (requiere Diagnostics Pack)"))
        return {"hallazgos": out, "resumen": {"planes": 0}}
    mejor = min(hist, key=lambda h: _num(h.get("ms_por_ejec"), 1e18))
    if actual:
        act = next((h for h in hist if h.get("plan_hash_value") == actual.get("plan_hash_value")), None)
        ms_act = _num(actual.get("ms_por_ejec"), _num((act or {}).get("ms_por_ejec")))
        ms_mejor = _num(mejor.get("ms_por_ejec"))
        if (actual.get("plan_hash_value") != mejor.get("plan_hash_value") and ms_mejor
                and ms_act >= u["plan_regresion_factor"] * ms_mejor):
            out.append(_h("rojo", f"Regresión de plan: el plan actual {actual.get('plan_hash_value')} tarda "
                                  f"{ms_act:.1f} ms/ejec; el plan {mejor.get('plan_hash_value')} tardaba {ms_mejor:.1f}"))
    if len(hist) > 1:
        out.append(_h("info", f"{len(hist)} planes distintos en la historia"))
    return {"hallazgos": out, "resumen": {"planes": len(hist), "mejor_plan": mejor.get("plan_hash_value"),
                                          "plan_actual": actual and actual.get("plan_hash_value")}}


# ====================================================================== comparaciones
def diff_esquema(origen: list[dict], destino: list[dict]) -> dict:
    def k(o):
        return (o["object_type"], o["object_name"])
    a, b = {k(o): o for o in origen}, {k(o): o for o in destino}
    solo_o = [dict(zip(("object_type", "object_name"), x)) for x in sorted(set(a) - set(b))]
    solo_d = [dict(zip(("object_type", "object_name"), x)) for x in sorted(set(b) - set(a))]
    estado = [{"object_type": t, "object_name": n, "origen": a[(t, n)].get("status"), "destino": b[(t, n)].get("status")}
              for (t, n) in sorted(set(a) & set(b)) if a[(t, n)].get("status") != b[(t, n)].get("status")]
    out = []
    if solo_o:
        out.append(_h("amarillo", f"{len(solo_o)} objeto(s) solo en el origen"))
    if solo_d:
        out.append(_h("amarillo", f"{len(solo_d)} objeto(s) solo en el destino"))
    if estado:
        out.append(_h("amarillo", f"{len(estado)} objeto(s) con estado distinto (VALID/INVALID)"))
    return {"hallazgos": out, "solo_en_origen": solo_o, "solo_en_destino": solo_d, "estado_distinto": estado,
            "resumen": {"origen": len(a), "destino": len(b), "iguales": len(set(a) & set(b)) - len(estado)}}


POR_ACCION = {
    "bd.ses.idle_tx": (("idle_tx",), idle_tx),
    "bd.salud.fra": (("fra_uso", "archivelog_hora"), fra),
    "bd.salud.dataguard": (("dg_rol", "dg_stats", "dg_gaps"), dataguard),
    "bd.salud.rman": (("rman_7d",), rman),
    "bd.salud.jobs": (("jobs_problema", "jobs_fallidos_24h"), jobs),
    "bd.salud.undo_temp": (("undo_24h", "temp_sesiones"), undo_temp),
    "bd.salud.alertlog": (("alertlog_24h",), alertlog),
    "bd.salud.esperas": (("esperas_sistema", "esperas_ahora"), esperas),
    "bd.salud.parametros": (("parametros",), parametros),
    "bd.usr.privilegios": (("usr_roles", "usr_privs_sistema", "usr_privs_objeto"), privilegios),
    "bd.ts.shrink": (("shrink_candidatos",), shrink),
    "bd.papelera": (("papelera",), papelera),
    "bd.parches": (("parches",), None),
    "bd.perf.plan": (("plan_historia", "plan_actual"), plan),
    "bd.perf.longops": (("longops",), None),
    "bd.stats.obsoletas": (("stats_obsoletas",), stats),
}
