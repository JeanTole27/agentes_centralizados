# -*- coding: utf-8 -*-
"""Gestor de trabajos: ejecuta planes en segundo plano, guarda log y evidencia.

  evidencias/trabajos/<id>.json -> metadatos (accion, usuario, cambio, estado, rc...)
  evidencias/trabajos/<id>.log  -> salida completa (sin secretos)
"""
import glob
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import escribir_json, leer_json, ruta
from .ejecutor import EjecutorSSH
from .modulos import ACCIONES, ErrorValidacion, planificar
from .seguridad import puede
from .simulacion import EjecutorSimulado

DIR = ruta("evidencias", "trabajos")
RE_CAMBIO = re.compile(r"^[A-Za-z0-9_\-/.]{3,40}$")


class Trabajo(object):
    def __init__(self, tid, plan, usuario, rol, cambio, simulacion):
        self.id = tid
        self.plan = plan
        self.meta = {
            "id": tid, "accion": plan.accion, "nombre": ACCIONES[plan.accion]["nombre"],
            "modulo": ACCIONES[plan.accion]["modulo"], "resumen": plan.resumen,
            "parametros": plan.publicos, "hosts": plan.hosts, "es_prod": plan.es_prod,
            "solo_lectura": ACCIONES[plan.accion]["solo_lectura"],
            "usuario": usuario, "rol": rol, "cambio": cambio, "simulacion": simulacion,
            "estado": "en_cola", "creado": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "inicio": None, "fin": None, "duracion_s": None,
            "pasos": [{"titulo": p.titulo, "host": p.host["hostname"], "estado": "pendiente", "rc": None}
                      for p in plan.pasos],
        }
        self.lineas = []
        self.cancelado = threading.Event()
        self._lock = threading.Lock()
        self._log = os.path.join(DIR, tid + ".log")

    def emitir(self, linea, prefijo=""):
        txt = "%s %s%s" % (time.strftime("%H:%M:%S"), prefijo, linea)
        with self._lock:
            self.lineas.append(txt)
            if len(self.lineas) > 50000:
                del self.lineas[:10000]
            with open(self._log, "a", encoding="utf-8") as f:
                f.write(txt + "\n")

    def guardar(self):
        escribir_json(os.path.join(DIR, self.id + ".json"), self.meta)


class GestorTrabajos(object):
    def __init__(self, cfg, inventario, auditoria):
        self.cfg = cfg
        self.inv = inventario
        self.aud = auditoria
        self.simulacion = bool(cfg.get("modo_simulacion"))
        self.ejecutor = EjecutorSimulado(cfg) if self.simulacion else EjecutorSSH(cfg)
        self._trabajos = {}
        self._hosts_ocupados = {}
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(int(cfg.get("max_trabajos_simultaneos", 4)))
        if not os.path.isdir(DIR):
            os.makedirs(DIR)
        self._historico = self._cargar_historico()

    # --------------------------------------------------------------- historico
    def _cargar_historico(self):
        out = {}
        for p in glob.glob(os.path.join(DIR, "*.json")):
            m = leer_json(p, None)
            if not m:
                continue
            if m.get("estado") in ("en_cola", "ejecutando"):
                m["estado"] = "interrumpido"
                escribir_json(p, m)
            out[m["id"]] = m
        return out

    def listar(self, limite=200, usuario=None, estado=None, modulo=None):
        with self._lock:
            todos = dict(self._historico)
            for t in self._trabajos.values():
                todos[t.id] = t.meta
        lst = sorted(todos.values(), key=lambda m: m["id"], reverse=True)
        if usuario:
            lst = [m for m in lst if m.get("usuario") == usuario]
        if estado:
            lst = [m for m in lst if m.get("estado") == estado]
        if modulo:
            lst = [m for m in lst if m.get("modulo") == modulo]
        return lst[:limite]

    def obtener(self, tid, desde=0):
        if not re.match(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$", tid or ""):
            return None
        with self._lock:
            t = self._trabajos.get(tid)
        if t:
            with t._lock:
                lineas = t.lineas[desde:]
                total = len(t.lineas)
            return {"meta": t.meta, "lineas": lineas, "total": total}
        m = self._historico.get(tid)
        if not m:
            return None
        p = os.path.join(DIR, tid + ".log")
        lineas = []
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lineas = [l.rstrip("\n") for l in f]
        return {"meta": m, "lineas": lineas[desde:], "total": len(lineas)}

    def ruta_log(self, tid):
        if not re.match(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$", tid or ""):
            return None
        p = os.path.join(DIR, tid + ".log")
        return p if os.path.exists(p) else None

    # ------------------------------------------------------------------ crear
    def previsualizar(self, accion, params, secretos):
        plan = planificar(self.inv, self.cfg, accion, params, secretos)
        return plan

    def crear(self, accion, params, secretos, sesion, cambio=""):
        plan = planificar(self.inv, self.cfg, accion, params, secretos)
        adef = ACCIONES[accion]
        rol = sesion["rol"]
        if not puede(rol, adef, plan.es_prod):
            raise PermisoDenegado("el rol '%s' no puede ejecutar '%s' en %s" % (
                rol, adef["nombre"], "ambiente productivo" if plan.es_prod else "este ambiente"))
        cambio = (cambio or "").strip()
        if plan.es_prod and not adef["solo_lectura"]:
            if not RE_CAMBIO.match(cambio):
                raise ErrorValidacion("en PROD/DRP las acciones con cambios requieren numero de cambio (CAB/RFC)")
        elif cambio and not RE_CAMBIO.match(cambio):
            raise ErrorValidacion("numero de cambio invalido")

        tid = time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        t = Trabajo(tid, plan, sesion["usuario"], rol, cambio, self.simulacion)
        with self._lock:
            if not adef["solo_lectura"]:
                ocupados = [h for h in plan.hosts if h in self._hosts_ocupados]
                if ocupados:
                    raise Conflicto("ya hay un trabajo con cambios en curso sobre: %s (trabajo %s)" % (
                        ", ".join(ocupados), self._hosts_ocupados[ocupados[0]]))
                for h in plan.hosts:
                    self._hosts_ocupados[h] = tid
            self._trabajos[tid] = t
        t.guardar()
        self.aud.registrar("trabajo_creado", sesion["usuario"], trabajo=tid, accion=accion,
                           parametros=plan.publicos, cambio=cambio, es_prod=plan.es_prod,
                           simulacion=self.simulacion)
        threading.Thread(target=self._correr, args=(t,), daemon=True).start()
        return t.meta

    def cancelar(self, tid, sesion):
        with self._lock:
            t = self._trabajos.get(tid)
        if not t or t.meta["estado"] not in ("en_cola", "ejecutando"):
            raise ErrorValidacion("el trabajo no esta en ejecucion")
        if sesion["rol"] != "admin" and sesion["usuario"] != t.meta["usuario"]:
            raise PermisoDenegado("solo el creador o un admin puede cancelar")
        t.cancelado.set()
        t.emitir("CMOPS: cancelacion solicitada por %s" % sesion["usuario"])
        self.aud.registrar("trabajo_cancelado", sesion["usuario"], trabajo=tid)

    # ---------------------------------------------------------------- correr
    def _correr(self, t):
        with self._sem:
            t.meta["estado"] = "ejecutando"
            t.meta["inicio"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            t0 = time.time()
            t.guardar()
            t.emitir("CMOPS: inicio '%s' por %s%s%s" % (
                t.meta["nombre"], t.meta["usuario"], " | cambio %s" % t.meta["cambio"] if t.meta["cambio"] else "",
                " | MODO SIMULACION" if self.simulacion else ""))
            fallos = 0
            try:
                pasos = list(enumerate(t.plan.pasos))
                if t.plan.paralelo and len(pasos) > 1:
                    n = int(self.cfg.get("max_hosts_paralelo", 10))
                    with ThreadPoolExecutor(max_workers=n) as ex:
                        for rc in ex.map(lambda ip: self._paso(t, ip[0], ip[1], True), pasos):
                            fallos += 1 if rc != 0 else 0
                else:
                    for i, p in pasos:
                        if t.cancelado.is_set():
                            t.meta["pasos"][i]["estado"] = "omitido"
                            continue
                        rc = self._paso(t, i, p, False)
                        if rc != 0:
                            fallos += 1
            except Exception as e:  # nunca dejar un trabajo colgado
                fallos += 1
                t.emitir("ERROR interno: %s" % e)
            if t.cancelado.is_set():
                estado = "cancelado"
            elif fallos == 0:
                estado = "exitoso"
            elif fallos < len(t.plan.pasos):
                estado = "parcial"
            else:
                estado = "fallido"
            t.meta.update(estado=estado, fin=time.strftime("%Y-%m-%dT%H:%M:%S"),
                          duracion_s=round(time.time() - t0, 1))
            t.emitir("CMOPS: fin del trabajo -> %s (%d paso(s) con error)" % (estado.upper(), fallos))
            t.guardar()
            self.aud.registrar("trabajo_finalizado", t.meta["usuario"], trabajo=t.id, estado=estado)
            with self._lock:
                for h, tid in list(self._hosts_ocupados.items()):
                    if tid == t.id:
                        del self._hosts_ocupados[h]
                self._historico[t.id] = t.meta
                self._trabajos.pop(t.id, None)

    def _paso(self, t, i, p, con_prefijo):
        info = t.meta["pasos"][i]
        info["estado"] = "ejecutando"
        pref = "[%s] " % p.host["hostname"] if con_prefijo else ""
        if not con_prefijo:
            t.emitir("---- PASO %d/%d: %s" % (i + 1, len(t.plan.pasos), p.titulo))
        capturadas = []

        def emitir(l):
            if p.post:
                capturadas.append(l)
                # el JSON de recoleccion no se muestra completo en el log
                if l.startswith("{") and len(l) > 300:
                    t.emitir("(datos JSON recolectados: %d bytes)" % len(l), pref)
                    return
            t.emitir(l, pref)

        try:
            rc = self.ejecutor.ejecutar_paso(p, emitir, t.cancelado)
        except Exception as e:
            t.emitir("ERROR: %s" % e, pref)
            rc = 1
        if rc == 0 and p.post == "guardar_hechos":
            rc = self._guardar_hechos(t, p, capturadas, pref)
        info["rc"] = rc
        info["estado"] = "ok" if rc == 0 else ("cancelado" if t.cancelado.is_set() else "error")
        if rc != 0:
            t.emitir("PASO con error (rc=%s)" % rc, pref)
        return rc

    def _guardar_hechos(self, t, p, lineas, pref):
        try:
            a = lineas.index("CMOPS_JSON_BEGIN")
            b = lineas.index("CMOPS_JSON_END")
            datos = json.loads("\n".join(lineas[a + 1:b]))
        except (ValueError, IndexError) as e:
            t.emitir("ERROR: la salida de recoleccion no contiene JSON valido (%s)" % e, pref)
            return 3
        datos["_recolectado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        datos["_trabajo"] = t.id
        self.inv.guardar_hechos(p.host["hostname"], datos)
        t.emitir("informacion guardada en inventario/hechos/%s.json" % p.host["hostname"], pref)
        return 0


class PermisoDenegado(Exception):
    pass


class Conflicto(Exception):
    pass
