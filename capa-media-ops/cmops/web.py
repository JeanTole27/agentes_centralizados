# -*- coding: utf-8 -*-
"""Servidor web (libreria estandar): interfaz + API JSON.

Seguridad:
  - Sesion en cookie HttpOnly + SameSite=Strict; token CSRF en cabecera X-CSRF-Token
  - Roles (consulta / operador / admin) validados en el servidor
  - TLS opcional (config/app.json -> tls.certificado / tls.llave)
  - Cabeceras: CSP estricta, X-Frame-Options, no-store en la API
"""
import json
import mimetypes
import os
import re
import socketserver
import ssl
import sys
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from . import VERSION
from .config import cargar, ruta
from .inventario import Inventario
from . import modulos
from .modulos import ACCIONES, ErrorValidacion
from .seguridad import DESCRIPCION_ROLES, Auditoria, Sesiones, Usuarios, puede
from .trabajos import Conflicto, GestorTrabajos, PermisoDenegado

WEB = ruta("web")
COOKIE = "cmops_sesion"


class App(object):
    def __init__(self):
        self.cfg = cargar()
        self.inv = Inventario(self.cfg)
        self.aud = Auditoria()
        self.usuarios = Usuarios()
        self.sesiones = Sesiones(int(self.cfg.get("sesion_minutos", 30)))
        self.trabajos = GestorTrabajos(self.cfg, self.inv, self.aud)


class Servidor(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Manejador(BaseHTTPRequestHandler):
    server_version = "CapaMediaOps/" + VERSION
    sys_version = ""
    app = None  # se asigna en iniciar()

    # ------------------------------------------------------------ utilidades
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cabeceras_seguridad(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _json(self, codigo, datos, cookie=None):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self._cabeceras_seguridad()
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, codigo, msg):
        self._json(codigo, {"error": msg})

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1024 * 1024:
            raise ErrorValidacion("cuerpo demasiado grande")
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise ErrorValidacion("JSON invalido")

    def _token(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[COOKIE].value if COOKIE in c else None

    def _sesion(self):
        return self.app.sesiones.obtener(self._token())

    def _cookie(self, valor, borrar=False):
        segura = "; Secure" if self.app.cfg.get("tls", {}).get("certificado") else ""
        if borrar:
            return "%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0%s" % (COOKIE, segura)
        return "%s=%s; Path=/; HttpOnly; SameSite=Strict%s" % (COOKIE, valor, segura)

    # --------------------------------------------------------------- estatico
    def _estatico(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith("..") or not re.match(r"^[A-Za-z0-9_.\-/]+$", rel):
            return self._error(404, "no encontrado")
        p = os.path.join(WEB, rel)
        if not os.path.isfile(p):
            return self._error(404, "no encontrado")
        with open(p, "rb") as f:
            data = f.read()
        tipo = mimetypes.guess_type(p)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript",):
            tipo += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cabeceras_seguridad()
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ rutas
    def do_GET(self):
        self._despachar("GET")

    def do_POST(self):
        self._despachar("POST")

    def _despachar(self, metodo):
        u = urlparse(self.path)
        path = u.path
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if not path.startswith("/api/"):
                if metodo != "GET":
                    return self._error(405, "metodo no permitido")
                return self._estatico(path)
            if path == "/api/login" and metodo == "POST":
                return self._login()
            s = self._sesion()
            if not s:
                return self._error(401, "sesion no valida o expirada")
            if metodo == "POST":
                if self.headers.get("X-CSRF-Token") != s["csrf"]:
                    return self._error(403, "token CSRF invalido")
            return self._api(metodo, path, qs, s)
        except ErrorValidacion as e:
            self._error(400, str(e))
        except PermisoDenegado as e:
            self._error(403, str(e))
        except Conflicto as e:
            self._error(409, str(e))
        except Exception:
            traceback.print_exc()
            self._error(500, "error interno (ver log del servidor)")

    def _login(self):
        b = self._cuerpo()
        usuario = str(b.get("usuario", "")).strip()
        clave = str(b.get("clave", ""))
        ses = self.app.sesiones
        if not usuario or ses.bloqueado(usuario):
            self.app.aud.registrar("login_bloqueado", usuario, ip=self.client_address[0])
            return self._error(429, "usuario bloqueado temporalmente por intentos fallidos")
        info = self.app.usuarios.autenticar(usuario, clave)
        if not info:
            ses.fallo(usuario)
            self.app.aud.registrar("login_fallido", usuario, ip=self.client_address[0])
            return self._error(401, "usuario o clave incorrectos")
        ses.exito(usuario)
        tok = ses.crear(info)
        self.app.aud.registrar("login", usuario, ip=self.client_address[0])
        return self._json(200, self._info_sesion(ses.obtener(tok)), cookie=self._cookie(tok))

    def _info_sesion(self, s):
        return {"usuario": s["usuario"], "nombre": s["nombre"], "rol": s["rol"], "csrf": s["csrf"],
                "simulacion": bool(self.app.cfg.get("modo_simulacion")), "version": VERSION,
                "aplicacion": self.app.cfg.get("nombre"), "descripcion_rol": DESCRIPCION_ROLES.get(s["rol"])}

    def _api(self, metodo, path, qs, s):
        app = self.app
        inv = app.inv
        if path == "/api/sesion":
            return self._json(200, self._info_sesion(s))
        if path == "/api/logout" and metodo == "POST":
            app.sesiones.cerrar(self._token())
            app.aud.registrar("logout", s["usuario"])
            return self._json(200, {"ok": True}, cookie=self._cookie("", borrar=True))
        if path == "/api/resumen":
            tr = app.trabajos.listar(limite=500)
            hoy = time.strftime("%Y-%m-%d")
            return self._json(200, {
                "inventario": inv.resumen(),
                "trabajos_hoy": len([t for t in tr if (t.get("creado") or "").startswith(hoy)]),
                "en_ejecucion": len([t for t in tr if t.get("estado") == "ejecutando"]),
                "fallidos_hoy": len([t for t in tr if (t.get("creado") or "").startswith(hoy)
                                     and t.get("estado") in ("fallido", "parcial")]),
                "ultimos": tr[:8],
            })
        if path == "/api/acciones":
            return self._json(200, {k: dict(v, permitido_no_prod=puede(s["rol"], v, False),
                                            permitido_prod=puede(s["rol"], v, True))
                                    for k, v in ACCIONES.items()})
        if path == "/api/inventario":
            return self._json(200, {"servidores": inv.listar(qs.get("ambiente"), qs.get("q")),
                                    "meta": inv.meta})
        if path == "/api/inventario/recargar" and metodo == "POST":
            if s["rol"] != "admin":
                return self._error(403, "solo admin")
            inv.recargar()
            app.aud.registrar("inventario_recargado", s["usuario"])
            return self._json(200, {"ok": True})
        m = re.match(r"^/api/inventario/([A-Za-z0-9_.\-]+)$", path)
        if m and metodo == "GET":
            h = m.group(1)
            if h not in inv.servidores:
                return self._error(404, "host no existe")
            info = dict(inv.servidores[h])
            info["hostname"] = h
            info["es_prod"] = inv.es_prod(h)
            info["modulos"] = inv.modulos_de_host(h)
            info["hechos"] = inv.hechos(h)
            return self._json(200, info)
        if path == "/api/orquestacion":
            out = []
            for h, oc in sorted(inv.orquestacion().items()):
                hh = inv.host(h, "orquestacion")
                insts = []
                for i in modulos.instalaciones_de(oc):
                    insts.append({k: i.get(k) for k in ("id", "usuario_so", "ruta", "mw_home", "nm_host",
                                                          "nm_usuario", "admin_usuario", "nota")}
                                 )
                    insts[-1]["dominios"] = i.get("dominios", [])
                out.append({"host": h, "es_prod": inv.es_prod(h), "ip": hh["ip"],
                            "ssh_usuario": hh["ssh_usuario"] or app.cfg.get("ssh", {}).get("usuario", ""),
                            "nota": oc.get("nota", ""), "instalaciones": insts})
            return self._json(200, {"hosts": out})
        if path == "/api/usuarios-wl":
            out = []
            for did, d in sorted(inv.usuarios_wl().items()):
                out.append(dict({k: v for k, v in d.items()}, id=did, es_prod=inv.es_prod(d.get("host", ""))))
            return self._json(200, {"dominios": out})
        if path == "/api/frmweb":
            out = [dict(v, host=h, es_prod=inv.es_prod(h)) for h, v in sorted(inv.frmweb().items())]
            return self._json(200, {"hosts": out})
        if path == "/api/trabajos" and metodo == "GET":
            return self._json(200, {"trabajos": app.trabajos.listar(
                limite=int(qs.get("limite", 200)), estado=qs.get("estado"), modulo=qs.get("modulo"),
                usuario=qs.get("usuario"))})
        if path == "/api/trabajos" and metodo == "POST":
            b = self._cuerpo()
            meta = app.trabajos.crear(str(b.get("accion", "")), b.get("parametros") or {},
                                      b.get("secretos") or {}, s, b.get("cambio") or "")
            return self._json(201, meta)
        if path == "/api/trabajos/previsualizar" and metodo == "POST":
            b = self._cuerpo()
            plan = app.trabajos.previsualizar(str(b.get("accion", "")), b.get("parametros") or {},
                                              b.get("secretos") or {})
            adef = ACCIONES[plan.accion]
            return self._json(200, {"resumen": plan.resumen, "hosts": plan.hosts, "es_prod": plan.es_prod,
                                    "solo_lectura": adef["solo_lectura"],
                                    "requiere_cambio": plan.es_prod and not adef["solo_lectura"],
                                    "permitido": puede(s["rol"], adef, plan.es_prod),
                                    "pasos": [p.titulo for p in plan.pasos], "parametros": plan.publicos})
        m = re.match(r"^/api/trabajos/([0-9a-z\-]+)$", path)
        if m and metodo == "GET":
            r = app.trabajos.obtener(m.group(1), int(qs.get("desde", 0) or 0))
            return self._json(200, r) if r else self._error(404, "trabajo no existe")
        m = re.match(r"^/api/trabajos/([0-9a-z\-]+)/cancelar$", path)
        if m and metodo == "POST":
            app.trabajos.cancelar(m.group(1), s)
            return self._json(200, {"ok": True})
        m = re.match(r"^/api/trabajos/([0-9a-z\-]+)/log$", path)
        if m and metodo == "GET":
            p = app.trabajos.ruta_log(m.group(1))
            if not p:
                return self._error(404, "log no existe")
            with open(p, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="trabajo-%s.log"' % m.group(1))
            self.send_header("Content-Length", str(len(data)))
            self._cabeceras_seguridad()
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/auditoria":
            if s["rol"] != "admin":
                return self._error(403, "solo admin")
            return self._json(200, {"eventos": app.aud.ultimos(int(qs.get("n", 300)))})
        if path == "/api/usuarios-app":
            if s["rol"] != "admin":
                return self._error(403, "solo admin")
            return self._json(200, {"usuarios": app.usuarios.listar(), "roles": DESCRIPCION_ROLES})
        return self._error(404, "ruta no existe")


def iniciar():
    app = App()
    Manejador.app = app
    cfg = app.cfg
    host, puerto = cfg.get("escuchar", "127.0.0.1"), int(cfg.get("puerto", 8080))
    srv = Servidor((host, puerto), Manejador)
    esquema = "http"
    tls = cfg.get("tls", {})
    if tls.get("certificado") and tls.get("llave"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1  # minimo TLS 1.2 (compatible Python 3.6)
        ctx.load_cert_chain(tls["certificado"], tls["llave"])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        esquema = "https"
    if not app.usuarios.listar():
        print("AVISO: no hay usuarios de la interfaz. Cree uno con: bin/cm usuario-app crear <usuario> --rol admin")
    print("%s %s escuchando en %s://%s:%d  (modo %s)" % (
        cfg.get("nombre"), VERSION, esquema, host, puerto,
        "SIMULACION" if cfg.get("modo_simulacion") else "REAL / SSH"))
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
