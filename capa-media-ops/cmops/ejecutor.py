# -*- coding: utf-8 -*-
"""Ejecucion remota por OpenSSH (cliente `ssh` del sistema operativo).

Cada accion se traduce en una lista de `Paso`. Un paso puede:
  1) desplegar un modulo (paquete tar enviado por stdin; solo se extrae si la
     version remota difiere), y
  2) ejecutar un comando remoto, opcionalmente como otro usuario de SO via
     `sudo -n -u <usuario_so>` y con datos sensibles enviados por stdin
     (nunca por argumentos ni en logs).
"""
import hashlib
import io
import os
import shlex
import subprocess
import tarfile
import threading
import time

q = shlex.quote
MTIME_FIJO = 1700000000


class Paso(object):
    def __init__(self, host, titulo, comando=None, stdin=None, desplegar=None,
                 usuario_so=None, sim=None, post=None, timeout=None):
        self.host = host              # dict de Inventario.host()
        self.titulo = titulo
        self.comando = comando        # script bash remoto
        self.stdin = stdin            # bytes (puede contener secretos)
        self.desplegar = desplegar    # {"origen": dir, "destino": ruta, "extras": {nombre: texto}, "excluir": [...]}
        self.usuario_so = usuario_so
        self.sim = sim or {}          # descriptor para el modo simulacion
        self.post = post              # nombre de post-proceso (p.ej. "guardar_hechos")
        self.timeout = timeout


def empaquetar(desplegar):
    """Empaqueta un directorio + extras en un tar deterministico. Devuelve (bytes, version)."""
    buf = io.BytesIO()
    excluir = tuple(desplegar.get("excluir", [])) + (".bak", ".pyc", "__pycache__")
    entradas = []
    origen = desplegar.get("origen")
    if origen:
        for base, dirs, files in os.walk(origen):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if any(x in f for x in excluir):
                    continue
                p = os.path.join(base, f)
                rel = os.path.relpath(p, origen).replace(os.sep, "/")
                with open(p, "rb") as fh:
                    entradas.append((rel, fh.read(), None))
    for nombre, contenido in sorted(desplegar.get("extras", {}).items()):
        if isinstance(contenido, str):
            contenido = contenido.encode("utf-8")
        entradas.append((nombre, contenido, None))
    entradas.sort(key=lambda e: e[0])
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for nombre, datos, _ in entradas:
            # normalizar saltos de linea de scripts (por si se editan en Windows)
            if nombre.endswith((".sh", ".py", ".conf", ".env", ".tpl")):
                datos = datos.replace(b"\r\n", b"\n")
            ti = tarfile.TarInfo(nombre)
            ti.size = len(datos)
            ti.mtime = MTIME_FIJO
            ti.mode = 0o750 if nombre.endswith((".sh", ".py")) else 0o640
            ti.uname = ti.gname = ""
            tar.addfile(ti, io.BytesIO(datos))
    data = buf.getvalue()
    return data, hashlib.sha1(data).hexdigest()[:12]


def envolver(script, usuario_so, usuario_ssh):
    """Comando remoto: bash -c '<script>' (como usuario_so via sudo si aplica)."""
    if usuario_so and usuario_so != usuario_ssh:
        return "sudo -n -u %s -H bash -c %s" % (q(usuario_so), q(script))
    return "bash -c %s" % q(script)


def script_despliegue(destino, version):
    return (
        'R=%s; V=%s; umask 027; '
        'if [ "$(cat "$R/.cmops_version" 2>/dev/null)" != "$V" ]; then '
        'mkdir -p "$R" && tar -xf - -C "$R" && echo "$V" > "$R/.cmops_version" '
        '&& echo "CMOPS: modulo desplegado en $R (version $V)"; '
        'else cat >/dev/null; echo "CMOPS: modulo al dia en $R (version $V)"; fi'
    ) % (q(destino), q(version))


class EjecutorSSH(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.ssh = cfg.get("ssh", {})
        self.dir_cm = os.path.join(os.path.expanduser("~"), ".ssh", "cmops-cm")

    def _base(self, host):
        s = self.ssh
        cmd = ["ssh", "-T",
               "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=%d" % int(s.get("timeout_conexion", 10)),
               "-o", "StrictHostKeyChecking=%s" % ("yes" if s.get("verificar_host", True) else "accept-new"),
               "-o", "ServerAliveInterval=30",
               "-p", str(host.get("ssh_port", 22))]
        if s.get("llave"):
            cmd += ["-i", s["llave"]]
        if s.get("known_hosts"):
            cmd += ["-o", "UserKnownHostsFile=" + s["known_hosts"]]
        if s.get("control_master", True) and os.name != "nt":
            if not os.path.isdir(self.dir_cm):
                os.makedirs(self.dir_cm, 0o700)
            cmd += ["-o", "ControlMaster=auto",
                    "-o", "ControlPath=" + os.path.join(self.dir_cm, "%r@%h-%p"),
                    "-o", "ControlPersist=120"]
        usuario = host.get("ssh_usuario") or s.get("usuario")
        cmd.append("%s@%s" % (usuario, host["ip"]))
        return cmd

    def usuario_ssh(self, host):
        return host.get("ssh_usuario") or self.ssh.get("usuario")

    def _ssh(self, host, remoto, stdin_bytes, emitir, cancelado, timeout):
        cmd = self._base(host) + [remoto]
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        except OSError as e:
            emitir("ERROR: no se pudo ejecutar ssh: %s" % e)
            return 255

        def escribir():
            try:
                if stdin_bytes:
                    p.stdin.write(stdin_bytes)
                p.stdin.close()
            except (OSError, ValueError):
                pass

        def vigilar():
            limite = time.time() + (timeout or self.cfg.get("timeout_paso_segundos", 3600))
            while p.poll() is None:
                if cancelado.is_set():
                    emitir("CMOPS: cancelacion solicitada; terminando sesion SSH")
                    p.kill()
                    return
                if time.time() > limite:
                    emitir("ERROR: tiempo maximo del paso excedido; terminando sesion SSH")
                    p.kill()
                    return
                time.sleep(0.5)

        threading.Thread(target=escribir, daemon=True).start()
        threading.Thread(target=vigilar, daemon=True).start()
        for raw in iter(p.stdout.readline, b""):
            emitir(raw.decode("utf-8", "replace").rstrip("\r\n"))
        return p.wait()

    def ejecutar_paso(self, paso, emitir, cancelado):
        host = paso.host
        usr = self.usuario_ssh(host)
        if paso.desplegar:
            data, ver = empaquetar(paso.desplegar)
            remoto = envolver(script_despliegue(paso.desplegar["destino"], ver), paso.usuario_so, usr)
            rc = self._ssh(host, remoto, data, emitir, cancelado, 300)
            if rc != 0:
                emitir("ERROR: fallo el despliegue del modulo (rc=%d)" % rc)
                return rc
        if paso.comando:
            remoto = envolver(paso.comando, paso.usuario_so, usr)
            return self._ssh(host, remoto, paso.stdin, emitir, cancelado, paso.timeout)
        return 0
