# -*- coding: utf-8 -*-
"""Usuarios de la interfaz, sesiones, roles y auditoria."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from .config import escribir_json, leer_json, ruta

ROLES = ("consulta", "operador", "admin")
DESCRIPCION_ROLES = {
    "consulta": "Solo acciones de lectura (estado, listados, validaciones, recoleccion).",
    "operador": "Lectura + acciones con cambios en ambientes NO productivos.",
    "admin": "Todo, incluido PROD/DRP (con numero de cambio obligatorio).",
}
ITERACIONES = 200000


def hash_clave(clave, sal=None, it=ITERACIONES):
    sal = sal or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), it).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (it, sal, h)


def verificar_clave(clave, almacenado):
    try:
        alg, it, sal, h = almacenado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def puede(rol, accion_def, es_prod):
    """Matriz de permisos."""
    if rol not in ROLES:
        return False
    if accion_def.get("solo_lectura"):
        return True
    if rol == "admin":
        return True
    if rol == "operador":
        return not es_prod
    return False


class Usuarios(object):
    ARCHIVO = ruta("config", "usuarios_app.json")

    def __init__(self):
        self._lock = threading.Lock()

    def _cargar(self):
        return leer_json(self.ARCHIVO, {"usuarios": {}}) or {"usuarios": {}}

    def listar(self):
        d = self._cargar()["usuarios"]
        return [{"usuario": u, "nombre": i.get("nombre", ""), "rol": i.get("rol"), "activo": i.get("activo", True)}
                for u, i in sorted(d.items())]

    def guardar(self, usuario, clave, rol, nombre=""):
        if rol not in ROLES:
            raise ValueError("rol invalido (use: %s)" % ", ".join(ROLES))
        if len(clave) < 10:
            raise ValueError("la clave debe tener minimo 10 caracteres")
        with self._lock:
            d = self._cargar()
            d["usuarios"][usuario] = {"hash": hash_clave(clave), "rol": rol, "nombre": nombre, "activo": True}
            escribir_json(self.ARCHIVO, d)
        try:
            os.chmod(self.ARCHIVO, 0o600)
        except OSError:
            pass

    def autenticar(self, usuario, clave):
        info = self._cargar()["usuarios"].get(usuario)
        if not info or not info.get("activo", True):
            verificar_clave(clave, hash_clave("x"))  # tiempo constante aproximado
            return None
        if verificar_clave(clave, info["hash"]):
            return {"usuario": usuario, "rol": info["rol"], "nombre": info.get("nombre") or usuario}
        return None


class Sesiones(object):
    def __init__(self, minutos):
        self.ttl = minutos * 60
        self.max_absoluto = 12 * 3600
        self._s = {}
        self._fallos = {}
        self._lock = threading.Lock()

    def crear(self, info):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._s[tok] = dict(info, creado=now, ultimo=now, csrf=secrets.token_urlsafe(24))
        return tok

    def obtener(self, tok):
        if not tok:
            return None
        now = time.time()
        with self._lock:
            s = self._s.get(tok)
            if not s:
                return None
            if now - s["ultimo"] > self.ttl or now - s["creado"] > self.max_absoluto:
                del self._s[tok]
                return None
            s["ultimo"] = now
            return s

    def cerrar(self, tok):
        with self._lock:
            self._s.pop(tok, None)

    # bloqueo por intentos fallidos (5 en 15 min)
    def bloqueado(self, usuario):
        with self._lock:
            f = [t for t in self._fallos.get(usuario, []) if time.time() - t < 900]
            self._fallos[usuario] = f
            return len(f) >= 5

    def fallo(self, usuario):
        with self._lock:
            self._fallos.setdefault(usuario, []).append(time.time())

    def exito(self, usuario):
        with self._lock:
            self._fallos.pop(usuario, None)


class Auditoria(object):
    ARCHIVO = ruta("evidencias", "auditoria.jsonl")

    def __init__(self):
        self._lock = threading.Lock()
        d = os.path.dirname(self.ARCHIVO)
        if not os.path.isdir(d):
            os.makedirs(d)

    def registrar(self, evento, usuario=None, **datos):
        reg = {"fecha": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "evento": evento, "usuario": usuario}
        reg.update(datos)
        linea = json.dumps(reg, ensure_ascii=False)
        with self._lock:
            with open(self.ARCHIVO, "a", encoding="utf-8") as f:
                f.write(linea + "\n")

    def ultimos(self, n=300):
        if not os.path.exists(self.ARCHIVO):
            return []
        with open(self.ARCHIVO, "r", encoding="utf-8") as f:
            lineas = f.readlines()[-n:]
        out = []
        for l in reversed(lineas):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
        return out
