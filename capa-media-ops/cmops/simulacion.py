# -*- coding: utf-8 -*-
"""Ejecutor simulado: permite probar la interfaz y los flujos sin tocar servidores.

Mantiene un estado en memoria (MS RUNNING/SHUTDOWN, usuarios por dominio) para
que las acciones sean coherentes entre si (bajar -> status muestra SHUTDOWN).
"""
import hashlib
import json
import random
import threading
import time

from .ejecutor import empaquetar


class EjecutorSimulado(object):
    _lock = threading.Lock()
    _ms = {}        # (host, dom, srv) -> estado
    _admin = {}     # (host, dom) -> bool
    _snap = {}      # (host, dom) -> [srv]
    _usuarios = {}  # dominio -> {usuario: [grupos]}
    _cred = {}      # (host/instalacion, dom) -> {"nm": bool, "admin": bool}

    def __init__(self, cfg, retardo=0.12):
        self.cfg = cfg
        self.retardo = retardo

    # ----------------------------------------------------------- utilidades
    def _rnd(self, *clave):
        return random.Random(hashlib.md5("|".join(str(c) for c in clave).encode()).hexdigest())

    def _p(self, emitir, cancelado, linea, factor=1.0):
        if cancelado.is_set():
            raise _Cancelado()
        time.sleep(self.retardo * factor)
        emitir(linea)

    def _estado_ms(self, host, dom, srv):
        k = (host, dom, srv)
        if k not in self._ms:
            r = self._rnd(host, dom, srv)
            self._ms[k] = "SHUTDOWN" if r.random() < 0.12 else "RUNNING"
        return self._ms[k]

    def _creds(self, host, dom):
        k = (host, dom)
        if k not in self._cred:
            # algunos dominios arrancan SIN credenciales registradas para ejercitar ese flujo
            r = self._rnd("cred", host, dom).random()
            self._cred[k] = {"nm": r >= 0.3, "admin": r >= 0.55}
        return self._cred[k]

    def _admin_up(self, host, dom):
        return self._admin.setdefault((host, dom), True)

    # --------------------------------------------------------------- entrada
    def ejecutar_paso(self, paso, emitir, cancelado):
        try:
            emitir("[SIMULACION] ssh %s@%s -> %s" % (paso.host.get("ssh_usuario") or self.cfg["ssh"].get("usuario"),
                                                      paso.host["ip"], paso.host["hostname"]))
            if paso.usuario_so:
                emitir("[SIMULACION] sudo -n -u %s" % paso.usuario_so)
            if paso.desplegar:
                _, ver = empaquetar(paso.desplegar)
                self._p(emitir, cancelado, "CMOPS: modulo al dia en %s (version %s)" % (paso.desplegar["destino"], ver))
            t = paso.sim.get("tipo")
            fn = getattr(self, "_sim_" + t, None)
            if not fn:
                emitir("[SIMULACION] sin simulador para %s" % t)
                return 0
            return fn(paso, emitir, cancelado)
        except _Cancelado:
            emitir("CMOPS: cancelado por el usuario")
            return 130

    # ------------------------------------------------------------- sistema
    def _sim_conectividad(self, paso, emitir, cancelado):
        r = self._rnd("con", paso.host["hostname"])
        if r.random() < 0.08:
            self._p(emitir, cancelado, "ssh: connect to host %s port 22: Connection timed out" % paso.host["ip"], 4)
            return 255
        self._p(emitir, cancelado, "CMOPS_OK host=%s usuario=%s so=Linux 4.14.35-2047.el7uek.x86_64"
                % (paso.host["hostname"].lower(), paso.host.get("ssh_usuario") or self.cfg["ssh"].get("usuario")))
        return 0

    @staticmethod
    def _sim_dominio(r, h, i):
        """Dominio descubierto: cada instalacion tiene su propio usuario de SO."""
        prop = r.choice(["wlogic12c", "wlogic14c", "oracle"])
        mw = "/Oracle/app/%s/Middleware" % prop
        nom = "%s_dom%d" % (h.split("-")[0].lower()[:6], i)
        return {"dominio": nom, "ruta": "%s/user_projects/domains/%s" % (mw, nom),
                "version": "14.1.1.0.0" if prop == "wlogic14c" else r.choice(["12.2.1.4.0", "12.1.3.0.0"]),
                "servers": 3, "despliegues": r.randint(1, 30), "datasources": r.randint(0, 8),
                "propietario": prop, "mw_home": mw, "admin_name": "AdminServer",
                "nm_port": str(5556 + i), "nm_tipo": "ssl",
                "managed_servers": ["%s_MS%d" % (nom.upper(), k) for k in (1, 2)]}

    def _sim_recoleccion(self, paso, emitir, cancelado):
        h = paso.host["hostname"]
        r = self._rnd("rec", h)
        if r.random() < 0.05:
            self._p(emitir, cancelado, "ssh: connect to host %s port 22: No route to host" % paso.host["ip"], 3)
            return 255
        so = r.choice(["Oracle Linux Server release 7.9", "Oracle Linux Server release 6.10",
                       "Red Hat Enterprise Linux Server release 7.9 (Maipo)", "Oracle Linux Server release 8.8"])
        ram = r.choice([16, 32, 64, 125, 188])
        cpu = r.choice([4, 8, 12, 18])
        datos = {
            "hostname": h.lower(), "so": so, "kernel": "4.14.35-2047.%d.el7uek.x86_64" % r.randint(300, 520),
            "cpu_nucleos": cpu, "cpu_modelo": r.choice(["Intel Xeon Gold 6338 (Icelake)", "Intel Xeon Gold 6248 (Cascadelake)"]),
            "ram_total_gb": ram, "ram_usada_gb": int(ram * r.uniform(0.2, 0.85)), "swap_total_gb": 10, "swap_usada_gb": 0,
            "uptime_dias": r.randint(3, 700),
            "discos": [{"montaje": m, "tamano": t, "uso_pct": r.randint(5, 92)} for m, t in
                       (("/", "13G"), ("/oracle", "109G"), ("/var/log", "9.8G"), ("/tmp", "9.8G"))],
            "limites": {"nofile_soft": 131072, "nofile_hard": 131072, "nproc_soft": 65536, "nproc_hard": 65536},
            "java": sorted(set(r.choice(["/opt/jdk1.8.0_391", "/opt/jdk1.8.0_221", "/usr/java/jdk1.7.0_241",
                                         "/opt/jdk1.6.0_151"]) for _ in range(2))),
            "weblogic": [self._sim_dominio(r, h, i) for i in range(1, r.randint(2, 5))],
            "dns": {"bd-tron": "10.7.0.41", "bd-terceros": "10.7.0.40"},
        }
        for i, linea in enumerate([
            "CMOPS: recolectando SO, CPU, memoria, discos, limites, Java y WebLogic...",
            "CMOPS_JSON_BEGIN", json.dumps(datos, ensure_ascii=False), "CMOPS_JSON_END",
            "CMOPS: recoleccion finalizada"]):
            self._p(emitir, cancelado, linea, 2 if i == 0 else 1)
        return 0

    # ------------------------------------------------------- orquestacion
    def _sim_orq(self, paso, emitir, cancelado):
        s = paso.sim
        host, acc = s["host"], s["accion"]
        def ts():
            return time.strftime("[%Y-%m-%d %H:%M:%S]")

        def status(d):
            self._p(emitir, cancelado, "==================== %s ====================" % d["nombre"])
            if self._admin_up(host, d["nombre"]):
                self._p(emitir, cancelado, "  AdminServer(%s): RUNNING pid=%d" % (d["admin"], self._rnd(host, d["nombre"]).randint(20000, 90000)))
            else:
                self._p(emitir, cancelado, "  AdminServer(%s): NOT_RUNNING" % d["admin"])
            for srv in d["servers"]:
                self._p(emitir, cancelado, "  SERVER=%s STATE=%s" % (srv, self._estado_ms(host, d["nombre"], srv)), 0.5)

        def inventory(d):
            run = [x for x in d["servers"] if self._estado_ms(host, d["nombre"], x) == "RUNNING"]
            self._snap[(host, d["nombre"])] = run
            self._p(emitir, cancelado, "%s snapshot %s -> %d MS RUNNING guardados en state/%s.snapshot" % (ts(), d["nombre"], len(run), d["nombre"]))

        yo = s.get("usuario_so", "")

        def sin_nm(d):
            """Igual que el script real: sin credencial cifrada no hay nmConnect."""
            if d.get("propietario", yo) != yo:
                self._p(emitir, cancelado, "%s   [%s] ERROR: DOMAIN_HOME pertenece a '%s' y se ejecuta como '%s'"
                        % (ts(), d["nombre"], d["propietario"], yo))
                return True
            if not self._creds(host, d["nombre"])["nm"]:
                self._p(emitir, cancelado, "%s   [%s] NM_CONNECT_ERROR: no existe nmsecure/nmUserConfig.secure "
                        "(registre credenciales del dominio)" % (ts(), d["nombre"]))
                return True
            return False

        if acc == "verificar":
            self._p(emitir, cancelado, "Usuario que ejecuta: %s" % yo)
            for d in s["dominios"]:
                c = self._creds(host, d["nombre"])
                pr = d.get("propietario", yo)
                self._p(emitir, cancelado, "DOMINIO=%s DIR=OK PROPIETARIO=%s COINCIDE=%s NM_CRED=%s ADMIN_CRED=%s" % (
                    d["nombre"], pr, "SI" if pr == yo else "NO", "OK" if c["nm"] else "FALTA",
                    "OK" if c["admin"] else "FALTA"), 0.6)
            return 0
        if acc == "credenciales":
            d = s["dominios"][0]
            if d.get("propietario", yo) != yo:
                self._p(emitir, cancelado, "ERROR: el dominio %s pertenece a '%s' y se esta ejecutando como '%s'."
                        % (d["nombre"], d["propietario"], yo))
                return 3
            c = self._creds(host, d["nombre"])
            self._p(emitir, cancelado, "[%s] Registrando credencial de Node Manager (usuario %s)..."
                    % (d["nombre"], s.get("nm_usuario") or "?"), 6)
            self._p(emitir, cancelado, "  CRED_OK nm")
            c["nm"] = True
            if s.get("admin_usuario"):
                self._p(emitir, cancelado, "[%s] Registrando credencial de consola (usuario %s)..."
                        % (d["nombre"], s["admin_usuario"]), 6)
                self._p(emitir, cancelado, "  CRED_OK admin")
                c["admin"] = True
            else:
                self._p(emitir, cancelado, "[%s] Credencial de consola omitida (el status usara el Node Manager)" % d["nombre"])
            return 0

        if acc == "status":
            rc = 0
            for d in s["dominios"]:
                if sin_nm(d):
                    rc = 4
                    continue
                status(d)
            return rc
        if acc in ("stop", "start", "inventory") and any(sin_nm(d) for d in s["dominios"]):
            self._p(emitir, cancelado, "ERROR: hay dominios sin credenciales o con propietario distinto; se aborta")
            return 4
        if acc == "inventory":
            for d in s["dominios"]:
                inventory(d)
            self._p(emitir, cancelado, "%s inventario tomado" % ts())
            return 0
        if acc == "stop":
            self._p(emitir, cancelado, ">> Snapshot previo a la baja")
            for d in s["dominios"]:
                inventory(d)
            self._p(emitir, cancelado, ">> Bajando dominios")
            for d in s["dominios"]:
                self._p(emitir, cancelado, "==================== STOP %s ====================" % d["nombre"])
                self._p(emitir, cancelado, "%s   [%s] deteniendo Managed Servers via Node Manager (nmKill)" % (ts(), d["nombre"]))
                for srv in d["servers"]:
                    prev = self._estado_ms(host, d["nombre"], srv)
                    self._ms[(host, d["nombre"], srv)] = "SHUTDOWN"
                    res = "OK" if prev == "RUNNING" else "ALREADY_DOWN"
                    self._p(emitir, cancelado, "    SERVER=%s ACTION=kill RESULT=%s" % (srv, res), 3)
                if s.get("bajar_nm"):
                    self._p(emitir, cancelado, "%s   [%s] deteniendo Node Manager (SIGTERM)" % (ts(), d["nombre"]), 4)
                self._p(emitir, cancelado, "%s   [%s] deteniendo AdminServer (SIGTERM, timeout 120s)" % (ts(), d["nombre"]), 4)
                self._admin[(host, d["nombre"])] = False
                self._p(emitir, cancelado, "%s   [%s] AdminServer detenido" % (ts(), d["nombre"]), 3)
            return 0
        if acc == "start":
            rc = 0
            for d in s["dominios"]:
                self._p(emitir, cancelado, "==================== START %s ====================" % d["nombre"])
                self._p(emitir, cancelado, "%s   [%s] Node Manager ya activo" % (ts(), d["nombre"]))
                if not self._admin_up(host, d["nombre"]):
                    self._p(emitir, cancelado, "%s   [%s] arrancando AdminServer (%s)" % (ts(), d["nombre"], d["admin"]), 8)
                    self._admin[(host, d["nombre"])] = True
                    self._p(emitir, cancelado, "%s   [%s] AdminServer arriba" % (ts(), d["nombre"]))
                else:
                    self._p(emitir, cancelado, "%s   [%s] AdminServer ya RUNNING" % (ts(), d["nombre"]))
                snap = self._snap.get((host, d["nombre"]), list(d["servers"]))
                for srv in snap:
                    if self._estado_ms(host, d["nombre"], srv) == "RUNNING":
                        self._p(emitir, cancelado, "    SERVER=%s ACTION=start RESULT=ALREADY_RUNNING(skip)" % srv)
                    else:
                        self._ms[(host, d["nombre"], srv)] = "RUNNING"
                        self._p(emitir, cancelado, "    SERVER=%s ACTION=start RESULT=OK" % srv, 6)
            return rc
        return 0

    # ------------------------------------------------------ usuarios WL
    def _usuarios_dom(self, dom):
        return self._usuarios.setdefault(dom, {"weblogic": ["Administrators"], "OracleSystemUser": ["OracleSystemGroup"],
                                               "monitor01": ["Monitors"]})

    def _listar(self, dom, emitir, cancelado):
        us = self._usuarios_dom(dom)
        self._p(emitir, cancelado, "  LISTADO DE USUARIOS EN EL REALM")
        for i, (u, g) in enumerate(sorted(us.items()), 1):
            self._p(emitir, cancelado, "  %d. %s  -->  %s" % (i, u, ", ".join(g) or "(sin grupo)"), 0.4)
        self._p(emitir, cancelado, "  Total de usuarios: %d" % len(us))

    def _sim_usr(self, paso, emitir, cancelado):
        s = paso.sim
        dom = s["dominio"]
        self._p(emitir, cancelado, "[INFO]  Conectando a AdminServer del dominio %s (t3s)" % dom, 3)
        if s["accion"] == "preparar":
            self._p(emitir, cancelado, "[1] Extrayendo certificado del AdminServer...", 3)
            self._p(emitir, cancelado, "    Certificado extraido OK")
            self._p(emitir, cancelado, "[2] Creando truststore %s_trust.jks... OK" % dom, 2)
            self._p(emitir, cancelado, "[OK]    Archivos de credenciales encriptadas generados (.wl_userconfig / .wl_keyfile)", 3)
            return 0
        self._p(emitir, cancelado, "[OK]    Conexion exitosa al AdminServer")
        if s["accion"] == "listar":
            self._listar(dom, emitir, cancelado)
            return 0
        us = self._usuarios_dom(dom)
        ok = fal = 0
        for a in s.get("acciones", []):
            acc, u, g = a["accion"], a["usuario"], a.get("grupo")
            self._p(emitir, cancelado, "=" * 60)
            if acc == "CREAR":
                if u in us:
                    self._p(emitir, cancelado, '[WARN]  El usuario "%s" ya existe. Se omite la creacion.' % u); fal += 1; continue
                us[u] = [g] if g else []
                self._p(emitir, cancelado, '[OK]    Usuario "%s" creado exitosamente' % u, 3)
                if g:
                    self._p(emitir, cancelado, '[OK]    Usuario "%s" agregado al grupo "%s" exitosamente' % (u, g))
                ok += 1
            elif acc == "ELIMINAR":
                if u not in us:
                    self._p(emitir, cancelado, '[WARN]  El usuario "%s" no existe. No se puede eliminar.' % u); fal += 1; continue
                del us[u]
                self._p(emitir, cancelado, '[OK]    Usuario "%s" eliminado exitosamente' % u, 3); ok += 1
            else:
                grupo = "Administrators" if acc == "ADMIN" else g
                if u not in us:
                    self._p(emitir, cancelado, '[ERROR] El usuario "%s" no existe' % u); fal += 1; continue
                if grupo in us[u]:
                    self._p(emitir, cancelado, '[WARN]  El usuario "%s" ya es miembro del grupo "%s"' % (u, grupo)); fal += 1; continue
                us[u].append(grupo)
                self._p(emitir, cancelado, '[OK]    Usuario "%s" agregado al grupo "%s" exitosamente' % (u, grupo), 2); ok += 1
        self._p(emitir, cancelado, "  RESUMEN: total=%d exitosas=%d fallidas=%d" % (ok + fal, ok, fal))
        self._listar(dom, emitir, cancelado)
        return 0 if fal == 0 else 4

    # --------------------------------------------------------- frmweb
    def _sim_frm(self, paso, emitir, cancelado):
        s = paso.sim
        r = random.Random()
        ms = s.get("managed") or ["WLS_FORMS1_1", "WLS_FORMS1_2", "WLS_FORMS1_3"]
        huer = {m: r.randint(0, 25) for m in ms}
        conx = {m: r.randint(30, 120) for m in ms}
        th, tc = sum(huer.values()), sum(conx.values())
        ts = lambda: time.strftime("[%Y-%m-%d %H:%M:%S]")
        self._p(emitir, cancelado, "%s === INICIO %s ===" % (ts(), "VALIDACION (--dry-run)" if s["accion"] == "validar" else "MITIGACION (lote=%d)" % s["lote"]))
        self._p(emitir, cancelado, " 10:31:02 up 63 days,  load average: %.2f, %.2f, %.2f" % (r.uniform(20, 60), r.uniform(20, 60), r.uniform(20, 60)))
        self._p(emitir, cancelado, "%s === PRE-VALIDACION: buscando procesos frmweb huerfanos ===" % ts(), 4)
        self._p(emitir, cancelado, "  %-18s %-12s %-14s" % ("MANAGED SERVER", "HUERFANOS", "CON_CONEXION"))
        for m in ms:
            self._p(emitir, cancelado, "  %-18s %-12s %-14s" % (m, huer[m], conx[m]))
        self._p(emitir, cancelado, "  %-18s %-12s %-14s" % ("TOTAL", th, tc))
        if s["accion"] == "validar":
            self._p(emitir, cancelado, "%s MODO --dry-run: solo validacion, no se termina ningun proceso." % ts())
            self._p(emitir, cancelado, "%s Se detectaron %d huerfanos." % (ts(), th))
            return 0
        if th == 0:
            self._p(emitir, cancelado, "%s No se detectaron procesos frmweb huerfanos. No hay nada que mitigar." % ts())
            return 0
        enviados = 0
        while enviados < th:
            n = min(s["lote"], th - enviados)
            enviados += n
            self._p(emitir, cancelado, "%s --- %d SIGTERM enviados; pausa 15s ---" % (ts(), enviados), 6)
        self._p(emitir, cancelado, "%s === Fase 1 completa: SIGTERM a %d procesos, 0 saltados ===" % (ts(), th))
        k = r.randint(0, 2)
        self._p(emitir, cancelado, "%s === Fase 2 completa: SIGKILL a %d procesos que sobrevivieron ===" % (ts(), k), 6)
        self._p(emitir, cancelado, "%s Total terminados: %d via TERM + %d via KILL" % (ts(), th, k))
        return 0


class _Cancelado(Exception):
    pass
