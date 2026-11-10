# -*- coding: utf-8 -*-
"""Carga de configuracion y utilidades de archivos JSON."""
import json
import os
import re
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFECTO = {
    "nombre": "Capa Media Ops",
    "modo_simulacion": True,
    "escuchar": "127.0.0.1",
    "puerto": 8080,
    "tls": {"certificado": "", "llave": ""},
    "ssh": {
        "usuario": "svc_capamedia",
        "llave": "",
        "known_hosts": "",
        "verificar_host": True,
        "timeout_conexion": 10,
        "control_master": True,
    },
    "ruta_remota_defecto": "/tmp/cmops",
    "sesion_minutos": 30,
    "ambientes_productivos": ["prod", "drp"],
    "max_trabajos_simultaneos": 4,
    "max_hosts_paralelo": 10,
    "timeout_paso_segundos": 3600,
}

NOMBRE_SEGURO = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def ruta(*partes):
    return os.path.join(RAIZ, *partes)


def _fusionar(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _fusionar(base[k], v)
        else:
            base[k] = v


def cargar():
    cfg = json.loads(json.dumps(DEFECTO))
    p = ruta("config", "app.json")
    if os.path.exists(p):
        _fusionar(cfg, leer_json(p, {}))
    return cfg


def leer_json(p, defecto=None):
    if not os.path.exists(p):
        return defecto
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def escribir_json(p, datos):
    """Escritura atomica (archivo temporal + rename)."""
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2, sort_keys=False)
        os.replace(tmp, p)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def nombre_seguro(valor, que="valor"):
    if not isinstance(valor, str) or not NOMBRE_SEGURO.match(valor):
        raise ValueError("%s invalido: %r" % (que, valor))
    return valor
