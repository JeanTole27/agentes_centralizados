"""Opciones de las pantallas de Capa Media para TODO el inventario de capa-media-ops.

Fuentes (dentro de la carpeta de capa-media-ops):
- inventario/servidores.json            → los servidores y sus dominios WebLogic (inventario general).
- inventario/configuracion_modulos.json → los que ya tienen configuración detallada por módulo
                                          (instalaciones, usuario de SO, rutas, credenciales).

Un servidor o dominio sin configuración detallada igual aparece: el puente le arma una configuración
estándar en memoria al ejecutar (solo en el modo simulación de cmops; en modo real hay que generarla
primero con "Recolectar" + `cm proponer-orquestacion`). Solo lectura: no hay usuarios ni claves en la salida.
"""
import json
import re
from pathlib import Path

AMBIENTES_CMOPS = {"prod": "PROD", "produccion": "PROD", "drp": "DRP", "pruebas": "TEST", "test": "TEST",
                   "qa": "QA", "stage": "QA", "desarrollo": "DESA", "desa": "DESA"}
_NO_SEGURO = re.compile(r"[^A-Za-z0-9_.\-]")


def nombre_cmops(nombre):
    """cmops usa el nombre del dominio en comandos: solo letras, números, _ . - (máx. 80)."""
    return _NO_SEGURO.sub("_", nombre.strip())[:80] or "dominio"


def leer(ruta):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def dominios_de(servidor):
    """[{id, nombre}] sin repetidos: id es el nombre que acepta cmops, nombre el original."""
    vistos, out = set(), []
    for d in servidor.get("dominios") or []:
        n = str(d.get("nombre") or "").strip()
        i = nombre_cmops(n) if n else ""
        if i and i not in vistos:
            vistos.add(i)
            out.append({"id": i, "nombre": n})
    return out


def opciones(ruta_cmops, ambiente_de):
    """ambiente_de(host) -> ambiente según el inventario de Ops Central ("" si no está)."""
    ruta_cmops = Path(ruta_cmops)
    srv = (leer(ruta_cmops / "inventario" / "servidores.json").get("servidores") or {})
    m = leer(ruta_cmops / "inventario" / "configuracion_modulos.json")
    orq_conf = (m.get("orquestacion") or {}).get("hosts") or {}
    usr_conf = (m.get("usuarios_wl") or {}).get("dominios") or {}
    frm_conf = (m.get("frmweb") or {}).get("hosts") or {}

    def amb(host):
        a = ambiente_de(host)
        if a:
            return a
        ambs = (srv.get(host) or {}).get("ambientes") or []
        return AMBIENTES_CMOPS.get(str(ambs[0]).lower(), str(ambs[0]).upper()) if ambs else ""

    orq = []
    for host in sorted(set(srv) | set(orq_conf)):
        oc = orq_conf.get(host)
        if oc:
            insts = oc.get("instalaciones") or [{**oc, "id": oc.get("usuario_so") or "principal"}]
            instalaciones = [{"id": i.get("id"), "usuario_so": i.get("usuario_so", ""),
                              "dominios": [{"id": d["nombre"], "nombre": d["nombre"]} for d in i.get("dominios") or []
                                           if d.get("nombre")]} for i in insts]
        else:
            instalaciones = [{"id": "principal", "usuario_so": "", "dominios": dominios_de(srv.get(host) or {})}]
        orq.append({"host": host, "ambiente": amb(host), "configurado": bool(oc), "instalaciones": instalaciones})

    usr, vistos = [], set()
    for did, d in sorted(usr_conf.items()):                       # los configurados primero, tal cual
        usr.append({"dominio": did, "nombre": did, "host": d.get("host", ""), "ambiente": amb(d.get("host", "")),
                    "configurado": True})
        vistos.add((did, d.get("host", "")))
    for host in sorted(srv):                                      # el resto del inventario (dominio + servidor)
        for d in dominios_de(srv[host]):
            if (d["id"], host) not in vistos and d["id"] not in usr_conf:
                vistos.add((d["id"], host))
                usr.append({"dominio": d["id"], "nombre": d["nombre"], "host": host, "ambiente": amb(host),
                            "configurado": False})

    frm = [{"host": host, "ambiente": amb(host), "configurado": host in frm_conf,
            "descripcion": (frm_conf.get(host) or {}).get("descripcion", ""),
            "managed_servers": (frm_conf.get(host) or {}).get("managed_servers") or []}
           for host in sorted(set(srv) | set(frm_conf))]
    return {"orquestacion": orq, "usuarios_wl": usr, "frmweb": frm,
            "fuente": "capa-media-ops/inventario/servidores.json + configuracion_modulos.json"}


def completar_configuracion(modulos, servidores, accion, params, cfg):
    """Si el servidor/dominio de la acción no tiene configuración detallada en cmops, agrega una estándar
    (en memoria) a partir del inventario general. Devuelve un aviso para la consola, o "" si no hizo falta.
    En modo real de cmops no inventa rutas ni usuarios: lanza ValueError con lo que hay que hacer."""
    mod = accion.split(".")[0]
    host = str(params.get("host") or "").strip()
    simulacion = bool(cfg.get("modo_simulacion"))
    base = (cfg.get("ruta_remota_defecto") or "/tmp/cmops").rstrip("/")
    usuario_so = cfg.get("usuario_so_defecto") or "oracle"

    def sin_config(que):
        if not simulacion:
            raise ValueError(f"{que} no tiene configuración en capa-media-ops: recolecta la información del servidor "
                             "(Recolectar) y genera su configuración con 'cm proponer-orquestacion' antes de operar")

    if mod == "orq" and host:
        hosts = modulos.setdefault("orquestacion", {}).setdefault("hosts", {})
        if host not in hosts:
            sin_config(f"el servidor {host}")
            doms = dominios_de(servidores.get(host) or {})
            hosts[host] = {"instalaciones": [{"id": "principal", "usuario_so": usuario_so, "ruta": f"{base}/wl-orquestacion",
                                              "dominios": [{"nombre": d["id"]} for d in doms]}]}
            return (f"{host} no tiene configuración detallada en capa-media-ops: se usa una estándar con sus "
                    f"{len(doms)} dominio(s) del inventario (usuario {usuario_so}, demo en simulación)")
    if mod == "usr":
        did = str(params.get("dominio") or "").strip()
        doms = modulos.setdefault("usuarios_wl", {}).setdefault("dominios", {})
        if did and did not in doms:
            sin_config(f"el dominio {did}")
            ip = (servidores.get(host) or {}).get("ip", "")
            doms[did] = {"host": host, "usuario_so": usuario_so, "ruta": f"{base}/usuarios-wl/{did}",
                         "admin_ip": ip, "admin_puerto": 7001, "realm": "myrealm"}
            return (f"{did} no tiene configuración detallada en capa-media-ops: se usa una estándar en {host} "
                    "(AdminServer en el puerto 7001, demo en simulación)")
    if mod == "frm" and host:
        hosts = modulos.setdefault("frmweb", {}).setdefault("hosts", {})
        if host not in hosts:
            sin_config(f"el servidor {host}")
            hosts[host] = {"usuario_so": usuario_so, "managed_servers": []}
            return f"{host} no tiene configuración frmweb en capa-media-ops: se usa una estándar (demo en simulación)"
    return ""
