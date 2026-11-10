# -*- coding: utf-8 -*-
"""Toma SOLO los usuarios SSH por servidor (SSH_USER_<HOSTNAME>=usuario) de un archivo
.env como el de capa_media y los guarda como ssh_usuario en inventario/servidores.json.

Las lineas SSH_PASS_* se IGNORAN: la plataforma se autentica con llave SSH; la llave
publica debe instalarse en la cuenta indicada de cada servidor.
"""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from cmops.config import escribir_json, leer_json, ruta  # noqa: E402

RE = re.compile(r"^\s*SSH_USER_([A-Za-z0-9_\-]+)\s*=\s*([A-Za-z0-9_.\-]+)\s*$")


def main(archivo):
    p = ruta("inventario", "servidores.json")
    inv = leer_json(p, {})
    srv = inv.get("servidores", {})
    asignados, sin_host = 0, []
    with open(archivo, encoding="utf-8") as f:
        for linea in f:
            m = RE.match(linea)
            if not m:
                continue
            host, usuario = m.group(1).upper(), m.group(2)
            # las variables de entorno no admiten '-' ni '.', por eso se normaliza
            norm = lambda x: re.sub(r"[^A-Z0-9]", "_", (x or "").upper())
            objetivos = [h for h, e in srv.items() if norm(h) == host or norm(e.get("hostname_excel")) == host]
            if not objetivos:
                sin_host.append(host)
            for h in objetivos:
                srv[h]["ssh_usuario"] = usuario
                asignados += 1
    escribir_json(p, inv)
    usados = sorted(set(e.get("ssh_usuario", "(defecto)") for e in srv.values()))
    print("ssh_usuario asignado en %d servidor(es). Usuarios distintos: %s" % (asignados, ", ".join(usados)))
    if sin_host:
        print("AVISO: %d usuario(s) del .env sin servidor en el inventario: %s" % (len(sin_host), ", ".join(sin_host[:15])))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: importar_usuarios_ssh.py <archivo .env>")
    main(sys.argv[1])
