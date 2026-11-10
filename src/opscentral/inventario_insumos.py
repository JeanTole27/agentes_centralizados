"""Genera el inventario maestro desde los insumos reales (H4 / E2).

Fuentes (rutas en [inventario.insumos]):
  servidores_cm   capa-media-ops/inventario/servidores.json   servidores, IPs, ambientes y dominios WebLogic
  bases           oracle-agent-kiro-master/config/databases.json   alias, DSN y ambiente de cada base
  bases_csv       oracle-agent-kiro-master/docs/databases.csv      criticidad y equipo dueño (opcional)
  mapa_oracle     zabbix-capacity-mcp-main/capacity-watchdog/config/oracle_map.yaml   host de BD ↔ IP (opcional)
  relaciones      config/relaciones.json   dominio → base (opcional; los insumos no la traen)

Reglas:
- Un servidor con varios ambientes queda con el más protegido (PROD > DRP > QA > TEST > DESA).
- Un dominio que aparece en varios nodos con el mismo nombre y ambiente es UNO solo, en clúster.
  Si el mismo nombre existe en ambientes distintos, el id lleva el ambiente: "simon (DRP)".
- El servidor de cada base sale de la IP del DSN: nombre del mapa de Oracle si lo conoce,
  si no, el servidor de Capa Media con esa IP, y si no, "BD-<ip>".
- Las credenciales nunca vienen en el inventario.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

AMBIENTE = {"prod": "PROD", "produccion": "PROD", "drp": "DRP", "stage": "QA", "qa": "QA", "pruebas": "TEST",
            "test": "TEST", "desarrollo": "DESA", "desa": "DESA", "dev": "DESA"}
ORDEN = ["PROD", "DRP", "QA", "TEST", "DESA"]
CRITICIDAD = {"alto": "alta", "alta": "alta", "medio": "media", "media": "media", "bajo": "baja", "baja": "baja"}


def _amb(v: str) -> str:
    return AMBIENTE.get(str(v or "").strip().lower(), str(v or "").upper())


def _mas_protegido(ambs) -> str:
    ambs = [a for a in ambs if a]
    return min(ambs, key=lambda a: ORDEN.index(a) if a in ORDEN else len(ORDEN)) if ambs else ""


def _dsn(dsn: str) -> tuple[str, int, str]:
    m = re.match(r"^/*([^:/]+):(\d+)/(.+)$", dsn.strip())
    if not m:
        raise ValueError(f"DSN no reconocido: {dsn}")
    return m.group(1), int(m.group(2)), m.group(3)


def _mapa_oracle(ruta: Path | None) -> dict[str, str]:
    """oracle_map.yaml → {ip: host}. Se lee el comentario '# 10.7.0.47' o la IP de los dsn."""
    if not ruta or not ruta.exists():
        return {}
    import yaml
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    out = {}
    for host, instancias in datos.items():
        for i in instancias or []:
            dsn = (i or {}).get("dsn")
            if dsn:
                out[_dsn(dsn)[0]] = str(host).upper()
    return out


def generar(rutas: dict[str, Path | None]) -> tuple[dict, list[str]]:
    avisos: list[str] = []
    cm = json.loads(Path(rutas["servidores_cm"]).read_text(encoding="utf-8"))
    servidores: dict[str, dict] = {}
    por_ip: dict[str, str] = {}
    dominios: dict[tuple[str, str], dict] = {}
    for host, s in sorted(cm.get("servidores", {}).items()):
        if s.get("habilitado") is False:
            continue
        hid = host.upper()
        amb = _mas_protegido(_amb(a) for a in s.get("ambientes") or [])
        servidores[hid] = {"id": hid, "ip": s.get("ip"), "ambiente": amb, "zabbix_host": hid, "equipo": "cm",
                           "ssh_port": s.get("ssh_port", 22), "ambientes": sorted({_amb(a) for a in s.get("ambientes") or []})}
        if s.get("ip"):
            por_ip.setdefault(s["ip"], hid)
        for d in s.get("dominios") or []:
            clave = (d["nombre"], _amb(d.get("ambiente")) or amb)
            dominios.setdefault(clave, {"nombre": d["nombre"], "ambiente": clave[1], "servidores": []})
            dominios[clave]["servidores"].append(hid)

    nombres_por_amb = defaultdict(set)
    for nombre, amb in dominios:
        nombres_por_amb[nombre].add(amb)
    lista_dom = []
    for (nombre, amb), d in sorted(dominios.items()):
        did = nombre if len(nombres_por_amb[nombre]) == 1 else f"{nombre} ({amb})"
        srv = sorted(set(d["servidores"]))
        lista_dom.append({"id": did, "nombre": nombre, "servidor": srv[0], "servidores": srv, "ambiente": amb})

    # ---- bases
    extra_csv: dict[str, dict] = {}
    if rutas.get("bases_csv") and Path(rutas["bases_csv"]).exists():
        with open(rutas["bases_csv"], encoding="utf-8") as f:
            for r in csv.DictReader(f):
                extra_csv[r["alias"].strip().lower()] = r
    mapa = _mapa_oracle(rutas.get("mapa_oracle"))
    kit = json.loads(Path(rutas["bases"]).read_text(encoding="utf-8")).get("databases", [])
    entradas = []
    for b in kit:
        try:
            entradas.append((b, *_dsn(b["dsn"])))
        except ValueError as e:
            avisos.append(str(e))
    usos = defaultdict(int)
    for _, _, _, servicio in entradas:
        usos[servicio.upper()] += 1
    bases, vistos, compartidos = [], set(), 0
    for b, ip, puerto, servicio in entradas:
        # Cada alias del kit es una conexión distinta (nodos RAC, copias PROD/TEST con el mismo servicio).
        # id = servicio si es único; si no, el alias, que el kit garantiza único.
        unico = usos[servicio.upper()] == 1
        compartidos += not unico
        bid = re.sub(r"[^A-Za-z0-9_]", "_", servicio if unico else b["alias"]).upper()[:30]
        if bid in vistos:
            avisos.append(f"id {bid} repetido ({b['alias']}): se conserva la primera entrada")
            continue
        vistos.add(bid)
        srv = mapa.get(ip) or por_ip.get(ip) or f"BD-{ip}"
        amb = _amb(b.get("environment"))
        if srv not in servidores:
            servidores[srv] = {"id": srv, "ip": ip, "ambiente": amb, "zabbix_host": srv if not srv.startswith("BD-") else None,
                               "equipo": "dba"}
        else:
            servidores[srv]["ambiente"] = _mas_protegido([servidores[srv]["ambiente"], amb])
            if servidores[srv]["equipo"] == "cm":
                avisos.append(f"{srv} es servidor de Capa Media y también aloja la base {bid}")
        x = extra_csv.get(b["alias"].lower(), {})
        bases.append({"id": bid, "alias": b["alias"], "servicio": servicio.upper(), "servidor": srv, "ambiente": amb,
                      "criticidad": CRITICIDAD.get(str(x.get("criticality", "")).lower(), "sin definir"),
                      "equipo_dueno": x.get("owner_team") or None, "dsn": f"{ip}:{puerto}/{servicio}",
                      "zabbix_host": None})
    if compartidos:
        avisos.append(f"{compartidos} conexión(es) comparten nombre de servicio con otra (RAC o PROD/TEST): "
                      "se identifican por su alias del kit")
    sin_nombre = sorted(s for s in servidores if s.startswith("BD-"))
    if sin_nombre:
        avisos.append(f"{len(sin_nombre)} servidor(es) de BD sin nombre conocido (se identifican por IP): "
                      "agregarlos a mapa_oracle para cruzarlos con Zabbix")

    relaciones = []
    rel = rutas.get("relaciones")
    if rel and Path(rel).exists():
        ids_dom = {d["id"] for d in lista_dom}
        ids_base = {b["id"] for b in bases}
        for r in json.loads(Path(rel).read_text(encoding="utf-8")):
            if r.get("dominio") in ids_dom and str(r.get("base", "")).upper() in ids_base:
                relaciones.append({"dominio": r["dominio"], "base": r["base"].upper(),
                                   "datasource": r.get("datasource")})
            else:
                avisos.append(f"relación ignorada (dominio o base fuera del inventario): {r}")
    else:
        avisos.append("sin relaciones dominio → base: Capa Media verá todas las bases hasta cargarlas "
                      "(config/relaciones.json o lectura de datasources)")

    inv = {"_descripcion": "Generado por 'python -m opscentral generar-inventario' desde los insumos reales. "
                           "No editar a mano: regenerar.",
           "servidores": sorted(servidores.values(), key=lambda s: s["id"]), "dominios": lista_dom,
           "bases": sorted(bases, key=lambda b: b["id"]), "relaciones": relaciones}
    return inv, avisos
