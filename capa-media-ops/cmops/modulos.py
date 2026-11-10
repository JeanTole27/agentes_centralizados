# -*- coding: utf-8 -*-
"""Catalogo de acciones y planificacion (accion + parametros -> lista de Pasos).

Toda validacion de entrada ocurre aqui, antes de que algo llegue a un servidor.
"""
import re
import shlex

from .config import nombre_seguro, ruta
from .ejecutor import Paso

q = shlex.quote


class ErrorValidacion(Exception):
    pass


# --------------------------------------------------------------------------
# Catalogo. solo_lectura=True -> cualquier rol puede ejecutarla.
# --------------------------------------------------------------------------
ACCIONES = {
    "sys.conectividad": {"modulo": "sistema", "nombre": "Probar conexion SSH", "solo_lectura": True},
    "rec.recolectar": {"modulo": "recoleccion", "nombre": "Recolectar informacion del servidor", "solo_lectura": True},
    "orq.status": {"modulo": "orquestacion", "nombre": "Estado de dominios WebLogic", "solo_lectura": True},
    "orq.verificar": {"modulo": "orquestacion", "nombre": "Verificar credenciales y propietarios", "solo_lectura": True},
    "orq.credenciales": {"modulo": "orquestacion", "nombre": "Registrar credenciales Node Manager / consola", "solo_lectura": False},
    "orq.inventory": {"modulo": "orquestacion", "nombre": "Tomar snapshot de Managed Servers", "solo_lectura": False},
    "orq.stop": {"modulo": "orquestacion", "nombre": "Bajar dominios WebLogic", "solo_lectura": False},
    "orq.start": {"modulo": "orquestacion", "nombre": "Subir dominios WebLogic", "solo_lectura": False},
    "usr.listar": {"modulo": "usuarios_wl", "nombre": "Listar usuarios WebLogic", "solo_lectura": True},
    "usr.preparar": {"modulo": "usuarios_wl", "nombre": "Preparar dominio (truststore + credenciales)", "solo_lectura": False},
    "usr.aplicar": {"modulo": "usuarios_wl", "nombre": "Aplicar acciones de usuarios WebLogic", "solo_lectura": False},
    "frm.validar": {"modulo": "frmweb", "nombre": "Validar frmweb huerfanos (dry-run)", "solo_lectura": True},
    "frm.mitigar": {"modulo": "frmweb", "nombre": "Mitigar frmweb huerfanos", "solo_lectura": False},
}

RE_VALOR_BASH = re.compile(r"^[A-Za-z0-9_./:${}@=,\- ]*$")
RE_USUARIO_WL = re.compile(r"^[A-Za-z0-9_.@\-]{1,64}$")
RE_GRUPO_WL = re.compile(r"^[A-Za-z0-9_. \-]{1,64}$")
ACCIONES_USR = ("CREAR", "ELIMINAR", "ADMIN", "GRUPO")


def _lista(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _bool(v):
    return v in (True, "true", "1", "si", "s", "on", 1)


def _entero(v, defecto, minimo, maximo, que):
    if v in (None, ""):
        return defecto
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ErrorValidacion("%s debe ser numerico" % que)
    if n < minimo or n > maximo:
        raise ErrorValidacion("%s fuera de rango (%d-%d)" % (que, minimo, maximo))
    return n


def _bash_val(v, que):
    v = "" if v is None else str(v)
    if not RE_VALOR_BASH.match(v):
        raise ErrorValidacion("valor no permitido en %s: %r" % (que, v))
    return v


class Plan(object):
    def __init__(self, accion, pasos, hosts, es_prod, resumen, publicos, paralelo=False):
        self.accion = accion
        self.pasos = pasos
        self.hosts = hosts
        self.es_prod = es_prod
        self.resumen = resumen
        self.publicos = publicos      # parametros sin secretos (para auditoria)
        self.paralelo = paralelo


# --------------------------------------------------------------------------
# Generacion de wl-config.sh por host (reemplaza la edicion manual)
# --------------------------------------------------------------------------
def generar_wl_config(hostname, oc):
    def v(k, defecto=""):
        return _bash_val(oc.get(k, defecto), k)

    lin = [
        "#!/bin/bash",
        "# =============================================================================",
        "# wl-config.sh  -  GENERADO por Capa Media Ops para %s. NO EDITAR EN EL SERVIDOR." % hostname,
        "# Fuente: inventario/configuracion_modulos.json (orquestacion.hosts.%s)" % hostname,
        "# =============================================================================",
        'export MW_HOME="%s"' % v("mw_home"),
        'export DOMAINS_BASE="%s"' % v("domains_base", "${MW_HOME}/user_projects/domains"),
        'export JAVA_HOME="%s"' % v("java_home"),
        'export WLST="%s"' % v("wlst", "${MW_HOME}/oracle_common/common/bin/wlst.sh"),
        'export NM_HOST="%s"' % v("nm_host"),
        'export TRUST_KEYSTORE="%s"' % v("trust_keystore"),
        'export WLST_SSL_JVM_ARGS="-Dweblogic.security.SSL.ignoreHostnameVerification=true '
        '-Dweblogic.security.TrustKeyStore=CustomTrust -Dweblogic.security.CustomTrustKeyStoreFileName=${TRUST_KEYSTORE} '
        '-Dweblogic.security.CustomTrustKeyStoreType=JKS"',
        'export WL_ORQ_HOME="%s"' % v("ruta"),
        'export STATE_DIR="${WL_ORQ_HOME}/state"',
        "",
        "DOMAINS=(",
    ]
    doms = oc.get("dominios", [])
    for d in doms:
        lin.append("  %s" % nombre_seguro(d["nombre"], "dominio"))
    lin += [")", ""]
    for k in ("DIR", "DOMAIN_NAME", "ADMIN_NAME", "ADMIN_URL", "NM_PORT", "NM_TYPE", "SERVERS"):
        lin.append("declare -A DOM_%s" % k)
    for d in doms:
        n = d["nombre"]
        lin.append("")
        lin.append("# ---- %s" % n)
        lin.append('DOM_DIR[%s]="%s"' % (n, _bash_val(d.get("dir", n), "dir")))
        lin.append('DOM_DOMAIN_NAME[%s]="%s"' % (n, _bash_val(d.get("domain_name", n), "domain_name")))
        lin.append('DOM_ADMIN_NAME[%s]="%s"' % (n, _bash_val(d.get("admin_name", "AdminServer"), "admin_name")))
        lin.append('DOM_ADMIN_URL[%s]="%s"' % (n, _bash_val(d.get("admin_url", ""), "admin_url")))
        lin.append('DOM_NM_PORT[%s]="%s"' % (n, _bash_val(d.get("nm_port", ""), "nm_port")))
        lin.append('DOM_NM_TYPE[%s]="%s"' % (n, _bash_val(d.get("nm_type", "plain"), "nm_type")))
        servers = [nombre_seguro(s, "managed server") for s in d.get("servers", [])]
        lin.append('DOM_SERVERS[%s]="%s"' % (n, " ".join(servers)))
    lin += [
        "",
        'wl_domain_home() { echo "${DOMAINS_BASE}/${DOM_DIR[$1]}"; }',
        "wl_domain_exists() {",
        '  local d="$1"',
        '  [[ -n "${DOM_DIR[$d]:-}" ]] && [[ -d "$(wl_domain_home "$d")" ]]',
        "}",
        "wl_all_domains() { printf '%s\\n' \"${DOMAINS[@]}\"; }",
        "",
    ]
    return "\n".join(lin)


def dominio_env(d):
    """Archivo de parametros por dominio para el modulo usuarios-wl."""
    campos = [
        ("ADMIN_URL", "t3s://%s:%s" % (d.get("admin_ip", ""), d.get("admin_puerto", ""))),
        ("ADMIN_IP", d.get("admin_ip", "")),
        ("ADMIN_PUERTO", d.get("admin_puerto", "")),
        ("WLST", d.get("wlst", "")),
        ("KEYTOOL", d.get("keytool", "")),
        ("SECURITY_REALM", d.get("realm", "myrealm")),
        ("DOMINIO_ID", d.get("id", "")),
        ("TLS_PROTOCOLOS", d.get("tls_protocolos", "TLSv1.2,TLSv1.3")),
        ("TLS_MINIMO", d.get("tls_minimo", "TLSv1.2")),
    ]
    lin = ["# GENERADO por Capa Media Ops. NO EDITAR EN EL SERVIDOR."]
    for k, val in campos:
        lin.append('%s="%s"' % (k, _bash_val(val, k)))
    return "\n".join(lin) + "\n"


# --------------------------------------------------------------------------
# Planificacion
# --------------------------------------------------------------------------
def planificar(inv, cfg, accion, params, secretos):
    if accion not in ACCIONES:
        raise ErrorValidacion("accion desconocida: %s" % accion)
    params = params or {}
    secretos = secretos or {}
    fn = {
        "sys.conectividad": _plan_conectividad,
        "rec.recolectar": _plan_recolectar,
        "orq.status": _plan_orq,
        "orq.inventory": _plan_orq,
        "orq.verificar": _plan_orq,
        "orq.credenciales": _plan_orq,
        "orq.stop": _plan_orq,
        "orq.start": _plan_orq,
        "usr.listar": _plan_usr,
        "usr.preparar": _plan_usr,
        "usr.aplicar": _plan_usr,
        "frm.validar": _plan_frm,
        "frm.mitigar": _plan_frm,
    }[accion]
    try:
        return fn(inv, cfg, accion, params, secretos)
    except (KeyError, ValueError) as e:
        raise ErrorValidacion(str(e).strip("'\""))


def _hosts_param(inv, params):
    hosts = _lista(params.get("hosts"))
    if not hosts:
        raise ErrorValidacion("debe indicar al menos un host")
    if len(hosts) > 300:
        raise ErrorValidacion("maximo 300 hosts por trabajo")
    return [inv.host(h) for h in hosts]


def _plan_conectividad(inv, cfg, accion, params, secretos):
    hs = _hosts_param(inv, params)
    pasos = [Paso(h, "Conexion SSH a %s (%s)" % (h["hostname"], h["ip"]),
                  comando='echo "CMOPS_OK host=$(hostname) usuario=$(id -un) so=$(uname -sr)"',
                  sim={"tipo": "conectividad"}, timeout=60) for h in hs]
    return Plan(accion, pasos, [h["hostname"] for h in hs], any(h["es_prod"] for h in hs),
                "Probar conexion SSH en %d servidor(es)" % len(hs), {"hosts": [h["hostname"] for h in hs]},
                paralelo=True)


def _plan_recolectar(inv, cfg, accion, params, secretos):
    hs = _hosts_param(inv, params)
    with open(ruta("modulos", "recoleccion", "recolectar_info.sh"), "rb") as f:
        script = f.read().replace(b"\r\n", b"\n")
    pasos = [Paso(h, "Recoleccion en %s (%s)" % (h["hostname"], h["ip"]),
                  comando="bash -s", stdin=script, sim={"tipo": "recoleccion"},
                  post="guardar_hechos", timeout=300) for h in hs]
    return Plan(accion, pasos, [h["hostname"] for h in hs], any(h["es_prod"] for h in hs),
                "Recolectar informacion (solo lectura) en %d servidor(es)" % len(hs),
                {"hosts": [h["hostname"] for h in hs]}, paralelo=True)


def instalaciones_de(oc):
    """Un host puede tener varias instalaciones WebLogic (p.ej. wlogic12c y wlogic14c),
    cada una con su propio usuario de SO, MW_HOME, ruta y dominios.
    Compatibilidad: una entrada sin 'instalaciones' se trata como una sola instalacion."""
    if oc.get("instalaciones"):
        return oc["instalaciones"]
    inst = {k: v for k, v in oc.items() if k not in ("ssh_ip", "ssh_port", "ssh_usuario", "nota")}
    inst.setdefault("id", oc.get("usuario_so") or "principal")
    return [inst]


def resolver_instalacion(inv, params):
    hostname = nombre_seguro(params.get("host", ""), "host")
    oc = inv.orquestacion().get(hostname)
    if not oc:
        raise ErrorValidacion("el host %s no tiene configuracion de orquestacion" % hostname)
    insts = instalaciones_de(oc)
    iid = params.get("instalacion") or insts[0]["id"]
    inst = next((i for i in insts if i.get("id") == iid), None)
    if not inst:
        raise ErrorValidacion("instalacion %s no existe en %s" % (iid, hostname))
    if not inst.get("usuario_so"):
        raise ErrorValidacion("la instalacion %s de %s no tiene usuario_so definido" % (iid, hostname))
    return hostname, oc, inst


def _plan_orq(inv, cfg, accion, params, secretos):
    hostname, oc, inst = resolver_instalacion(inv, params)
    h = inv.host(hostname, "orquestacion")
    conf = {d["nombre"]: d for d in inst.get("dominios", [])}
    if accion == "orq.credenciales":
        doms = _lista(params.get("dominio"))
        if len(doms) != 1:
            raise ErrorValidacion("indique un solo dominio para registrar credenciales")
    else:
        doms = _lista(params.get("dominios")) or list(conf.keys())
    if not doms:
        raise ErrorValidacion("la instalacion %s de %s no tiene dominios configurados "
                              "(recolecte informacion y use 'cm proponer-orquestacion %s')" % (inst["id"], hostname, hostname))
    for d in doms:
        if d not in conf:
            raise ErrorValidacion("dominio %s no configurado en %s/%s" % (d, hostname, inst["id"]))
    csv = ",".join(doms)
    paralelo = _bool(params.get("paralelo"))
    bajar_nm = _bool(params.get("bajar_nm"))
    timeout = _entero(params.get("timeout"), 120 if accion == "orq.stop" else 300, 30, 1800, "timeout")
    R = _bash_val(inst["ruta"], "ruta")
    b = "cd %s/bin || exit 1; " % q(R)
    par = " -p all" if paralelo else ""
    stdin = None
    publicos = {"host": hostname, "instalacion": inst["id"], "usuario_so": inst["usuario_so"], "dominios": doms}
    if accion == "orq.status":
        cmd = b + "./wl-services.sh status -d %s" % q(csv)
    elif accion == "orq.verificar":
        cmd = b + "./wl-credenciales.sh --verificar %s" % q(csv)
    elif accion == "orq.inventory":
        cmd = b + "./wl-services.sh inventory -d %s" % q(csv)
    elif accion == "orq.credenciales":
        nmu = str(secretos.get("nm_usuario") or "").strip()
        nmp = str(secretos.get("nm_password") or "")
        adu = str(secretos.get("admin_usuario") or "").strip()
        adp = str(secretos.get("admin_password") or "")
        if not RE_USUARIO_WL.match(nmu) or not nmp:
            raise ErrorValidacion("usuario y clave del Node Manager son obligatorios")
        if (adu or adp) and (not RE_USUARIO_WL.match(adu) or not adp):
            raise ErrorValidacion("para la credencial de consola indique usuario y clave (o deje ambos vacios)")
        for v in (nmp, adp):
            if "\n" in v or "\r" in v:
                raise ErrorValidacion("las claves no pueden contener saltos de linea")
        stdin = ("%s\n%s\n%s\n%s\n" % (nmu, nmp, adu, adp)).encode("utf-8")
        cmd = b + "./wl-credenciales.sh %s" % q(doms[0])
        publicos.update(nm_usuario=nmu, admin_usuario=adu or None)
    elif accion == "orq.stop":
        cmd = b + ("echo '>> Snapshot previo a la baja'; ./wl-services.sh inventory -d %s || "
                   "{ echo 'ERROR: no se pudo tomar el snapshot; se aborta la baja'; exit 1; }; "
                   "echo '>> Bajando dominios'; ./wl-services.sh stop -d %s -y -t %d%s%s") % (
            q(csv), q(csv), timeout, par, " --stop-nm" if bajar_nm else "")
    else:
        cmd = b + "./wl-services.sh start -d %s -y -t %d%s" % (q(csv), timeout, par)
    if accion in ("orq.stop", "orq.start"):
        publicos.update(paralelo=paralelo, bajar_nm=bajar_nm, timeout=timeout)
    despl = {"origen": ruta("modulos", "wl-orquestacion"), "destino": R,
             "extras": {"bin/wl-config.sh": generar_wl_config("%s (%s)" % (hostname, inst["id"]), inst)},
             "excluir": ["wl-config.sh"]}
    sim = {"tipo": "orq", "accion": accion.split(".")[1], "host": "%s/%s" % (hostname, inst["id"]),
           "usuario_so": inst["usuario_so"], "bajar_nm": bajar_nm,
           "nm_usuario": publicos.get("nm_usuario"), "admin_usuario": publicos.get("admin_usuario"),
           "dominios": [{"nombre": d, "admin": conf[d].get("admin_name", "AdminServer"),
                         "servers": conf[d].get("servers", []),
                         "propietario": conf[d].get("propietario") or inst["usuario_so"]} for d in doms]}
    paso = Paso(h, "%s en %s [%s, usuario %s]: %s" % (ACCIONES[accion]["nombre"], hostname, inst["id"],
                                                      inst["usuario_so"], ", ".join(doms)),
                comando=cmd, stdin=stdin, desplegar=despl, usuario_so=inst["usuario_so"], sim=sim,
                timeout=timeout * max(1, len(doms)) + 600)
    return Plan(accion, [paso], [hostname], h["es_prod"],
                "%s - %s/%s (%d dominio(s))" % (ACCIONES[accion]["nombre"], hostname, inst["id"], len(doms)),
                publicos)




def _texto_acciones(acciones):
    """Valida la tabla de acciones y devuelve (texto_para_stdin, version_publica)."""
    if not acciones:
        raise ErrorValidacion("debe indicar al menos una accion de usuario")
    if len(acciones) > 200:
        raise ErrorValidacion("maximo 200 acciones por trabajo")
    lin, pub = [], []
    for i, a in enumerate(acciones, 1):
        acc = str(a.get("accion", "")).upper().strip()
        usr = str(a.get("usuario", "")).strip()
        pwd = str(a.get("password", ""))
        desc = str(a.get("descripcion", "")).strip()
        grp = str(a.get("grupo", "")).strip()
        if acc not in ACCIONES_USR:
            raise ErrorValidacion("fila %d: accion invalida %r" % (i, acc))
        if not RE_USUARIO_WL.match(usr):
            raise ErrorValidacion("fila %d: usuario invalido" % i)
        if usr.lower() == "weblogic" and acc == "ELIMINAR":
            raise ErrorValidacion("fila %d: no se permite eliminar el usuario weblogic" % i)
        if grp and not RE_GRUPO_WL.match(grp):
            raise ErrorValidacion("fila %d: grupo invalido" % i)
        for campo, val in (("password", pwd), ("descripcion", desc)):
            if "|" in val or "\n" in val or "\r" in val:
                raise ErrorValidacion("fila %d: %s no puede contener | ni saltos de linea" % (i, campo))
        if acc == "CREAR":
            if len(pwd) < 8:
                raise ErrorValidacion("fila %d: CREAR requiere password de minimo 8 caracteres" % i)
            if pwd.lower() == usr.lower():
                raise ErrorValidacion("fila %d: el password no puede ser igual al usuario" % i)
        if acc == "GRUPO" and not grp:
            raise ErrorValidacion("fila %d: GRUPO requiere el nombre del grupo" % i)
        lin.append("|".join([acc, usr, pwd if acc == "CREAR" else "", desc, grp]))
        pub.append({"accion": acc, "usuario": usr, "descripcion": desc, "grupo": grp,
                    "password": "********" if acc == "CREAR" else ""})
    return ("\n".join(lin) + "\n").encode("utf-8"), pub


def _plan_usr(inv, cfg, accion, params, secretos):
    did = nombre_seguro(params.get("dominio", ""), "dominio")
    d = inv.usuarios_wl().get(did)
    if not d:
        raise ErrorValidacion("dominio %s no configurado para gestion de usuarios" % did)
    d = dict(d, id=did)
    if not d.get("usuario_so"):
        raise ErrorValidacion("el dominio %s no tiene usuario_so definido" % did)
    h = inv.host(d["host"])
    R = _bash_val(d["ruta"], "ruta")
    despl = {"origen": ruta("modulos", "usuarios-wl"), "destino": R, "extras": {"dominio.env": dominio_env(d)}}
    publicos = {"dominio": did, "host": d["host"]}
    stdin = None
    sim = {"tipo": "usr", "accion": accion.split(".")[1], "dominio": did}
    if accion == "usr.listar":
        cmd = "cd %s && LISTAR_SOLO=1 bash ejecutar_wlst.sh gestionar_usuarios_weblogic.py" % q(R)
    elif accion == "usr.preparar":
        au = str(secretos.get("admin_usuario") or d.get("admin_usuario") or "").strip()
        ap = str(secretos.get("admin_password") or "")
        if not RE_USUARIO_WL.match(au) or not ap or "\n" in ap:
            raise ErrorValidacion("usuario y password del administrador son obligatorios")
        stdin = ("%s\n%s\ny\n" % (au, ap)).encode("utf-8")
        cmd = "cd %s && bash preparar_dominio.sh" % q(R)
        publicos["admin_usuario"] = au
    else:
        stdin, pub = _texto_acciones(params.get("acciones") or secretos.get("acciones"))
        cmd = "cd %s && bash aplicar_acciones.sh" % q(R)
        publicos["acciones"] = pub
        sim["acciones"] = pub
    paso = Paso(h, "%s - %s" % (ACCIONES[accion]["nombre"], did), comando=cmd, stdin=stdin,
                desplegar=despl, usuario_so=d.get("usuario_so"), sim=sim, timeout=900)
    return Plan(accion, [paso], [d["host"]], h["es_prod"],
                "%s - dominio %s (%s)" % (ACCIONES[accion]["nombre"], did, d["host"]), publicos)


def _plan_frm(inv, cfg, accion, params, secretos):
    hostname = nombre_seguro(params.get("host", ""), "host")
    fh = inv.frmweb().get(hostname)
    if not fh:
        raise ErrorValidacion("el host %s no tiene configuracion frmweb" % hostname)
    h = inv.host(hostname, "frmweb")
    R = _bash_val(fh.get("ruta") or (cfg.get("ruta_remota_defecto", "/tmp/cmops") + "/frmweb"), "ruta")
    lote = _entero(params.get("lote"), 20, 1, 100, "lote")
    usr_so = fh.get("usuario_so")
    if not usr_so:
        raise ErrorValidacion("el host %s no tiene usuario_so definido para frmweb" % hostname)
    if accion == "frm.validar":
        cmd = "USUARIO_ESPERADO=%s bash %s/mitigar_frmweb_huerfanos.sh --dry-run" % (q(usr_so), q(R))
    else:
        cmd = "USUARIO_ESPERADO=%s bash %s/mitigar_frmweb_huerfanos.sh --yes %d" % (q(usr_so), q(R), lote)
    despl = {"origen": ruta("modulos", "frmweb-huerfanos"), "destino": R}
    paso = Paso(h, "%s en %s" % (ACCIONES[accion]["nombre"], hostname), comando=cmd, desplegar=despl,
                usuario_so=fh.get("usuario_so"),
                sim={"tipo": "frm", "accion": accion.split(".")[1], "lote": lote,
                     "managed": fh.get("managed_servers", [])}, timeout=3600)
    return Plan(accion, [paso], [hostname], h["es_prod"],
                "%s - %s" % (ACCIONES[accion]["nombre"], hostname),
                {"host": hostname, "lote": lote if accion == "frm.mitigar" else None})
